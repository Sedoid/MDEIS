"""The build against a real Postgres (T-071), in a scratch schema that is dropped afterwards.

Marked `integration`: skipped without DATABASE_URL, and a skip is not a pass. The migration set,
including 010, is run into the scratch schema. The Baobab methodology is stored as sections, the
Baobab rulebook is loaded and active, and the sandbox is the real child run in this process (the
runner's socket is not needed). The model is a script.

What is shown: the six build tools write the registry and the patch table, each as one action-log
row with no approver; a function the checks refuse never reaches the registry; the database refuses
what the code refuses; a patch is applied only for a measure a registered teacher confirmed, as a
new template version; and the measure then reads its confirmed values."""

import asyncio
import json

import psycopg
import pytest
import yaml
from build_fakes import REVIEW, LocalServer, ScriptedModel
from build_kit import (
    HARDCODED_SOURCE,
    ROOT,
    extraction,
    moved_proposal,
    parsed,
    rule_evidence,
    rule_moved,
    rule_window,
)
from dbtools import make_document, one, refused
from inprocess_runner import Calls

from mdeis import registry, rulebook
from mdeis.core.measure_params import params_for
from mdeis.core.template import load_template
from mdeis.measure_build import api, apply
from mdeis.tools.context import Approver, ToolContext
from orchestrator.build import graph as build_graph
from orchestrator.config import load_settings
from orchestrator.tools import MDEIS

BAOBAB = ROOT / "templates" / "baobab-v1.yaml"
OCC = ROOT / "templates" / "v1.1.yaml"
TEACHER = Approver("u-teacher", "Ms Teacher")
GOOD_RULES = extraction(rule_moved(), rule_window(), rule_evidence())
SETTINGS = load_settings({"OPENROUTER_MODEL": "test-model"})

pytestmark = pytest.mark.integration


class World:
    def __init__(self, conn, url, document):
        self.conn, self.url, self.document = conn, url, document

    def ctx(self, who=None):
        return ToolContext(conn=self.conn, run_id="RUN-T", approver=who, database_url=self.url,
                           commit=False)  # fmt: skip

    def tool(self, name, who=None, **arguments):
        return api.TOOLS[name](self.ctx(who), **arguments)

    def settle(self):
        got = self.tool("build_settle_rules", document_id=self.document, section_id="6.4",
                        rules=json.loads(GOOD_RULES)["rules"])  # fmt: skip
        assert got["status"] == "ok", got
        return got

    def store(self, proposal=None):
        settled = self.settle()
        stored = self.tool("build_store_version", document_id=self.document, section_id="6.4",
                           settled=settled, proposal=proposal or moved_proposal())  # fmt: skip
        return stored, settled

    def one(self, sql, params=()):
        return one(self.conn, sql, params)


@pytest.fixture
def world(built_schema, monkeypatch):
    _, url = built_schema
    monkeypatch.setattr(api.client, "run", Calls())
    with psycopg.connect(url) as conn:
        rulebook.load_file(conn, BAOBAB, activate=True, url=url, by="test")
        document = make_document(conn, kind="methodology", status="done", active=True,
                                 sha256="c" * 64)  # fmt: skip
        for ordinal, s in enumerate(parsed().sections, start=1):
            conn.execute(
                "INSERT INTO method_chunks (document_id, section_id, title, page, ordinal, text, "
                "text_sha256) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (document, s.id, s.title or None, s.page, ordinal, s.text, s.sha256),
            )
        conn.execute(
            "INSERT INTO registry_reviewers (user_id, role, name, granted_by) VALUES "
            "('u-teacher', 'teacher', 'Ms Teacher', 'admin'), "
            "('u-dev', 'developer', 'Dev One', 'admin')"
        )
        conn.commit()
        yield World(conn, url, document)
        conn.rollback()


def log(world, like="build_%"):
    return world.conn.execute(
        "SELECT tool, outcome, approver_id FROM action_log WHERE tool LIKE %s ORDER BY event_id",
        (like,),
    ).fetchall()


# ----------------------------------------------------------------------------- the plan
def test_the_plan_reads_the_stored_methodology_and_refuses_what_it_should(world):
    plan = world.tool("build_plan", document_id=world.document, build_anyway=["6.4"])
    assert plan["status"] == "ok" and plan["first_methodology"] is True
    assert plan["document_ref"] == "BAO-METH-1.0" and plan["template_version"] == "baobab-v1"
    assert "6.4" in [s["section_id"] for s in plan["sections"]]
    assert "6.2" in [s["section_id"] for s in plan["skipped"]] and plan["build_anyway"] == ["6.4"]

    assert world.tool("build_plan", who=TEACHER, document_id=world.document)["status"] == "refused"
    assert world.tool("build_plan", document_id="D-999999")["status"] == "refused"
    results = make_document(world.conn, kind="results", status="done", sha256="d" * 64)
    assert world.tool("build_plan", document_id=results)["status"] == "refused"
    waiting = make_document(world.conn, kind="methodology", status="needs_review", sha256="e" * 64)
    refusal = world.tool("build_plan", document_id=waiting)
    assert refusal["status"] == "refused" and "acknowledged" in refusal["reason"]

    rows = log(world, "build_plan")
    assert [r[1] for r in rows] == ["ok", "refused", "refused", "refused", "refused"]
    assert rows[1][2] == "u-teacher", "the refusal of a signed-in person is logged with who tried"


# ----------------------------------------------------------------------------- the registry
def test_a_function_that_passed_every_check_is_stored_with_its_tests_and_its_patch(world):
    stored, settled = world.store()
    assert stored["status"] == "stored" and stored["version_status"] == "awaiting_review"
    assert stored["patch_id"].startswith("TP-") and stored["clause_ids"] == ["MP-1", "TR-1"]
    measure = stored["measure_id"]
    row = world.conn.execute(
        "SELECT status, clause_ids, template_version, methodology_document_id, input_scope, "
        "output_kind, output_schema, static_check -> 'passed', test_result -> 'passed', "
        "tests, built_by_run FROM measure_versions WHERE measure_id = %s", (measure,),
    ).fetchone()  # fmt: skip
    assert row[0] == "awaiting_review" and row[1] == ["MP-1", "TR-1"]
    assert (row[2], row[3], row[4], row[5]) == ("baobab-v1", world.document, "learner", "number")
    fields = ["change", "enough_evidence", "moved_down", "moved_up", "scores_used"]
    assert row[6]["fields"] == fields
    assert row[7] is True and row[8] is True and row[10] == "RUN-T"
    assert "document_example" in {t["source"] for t in row[9]}
    patch = world.conn.execute(
        "SELECT status, base_template_version, source_document_id, clauses, section_hashes "
        "FROM template_patches WHERE patch_id = %s", (stored["patch_id"],),
    ).fetchone()  # fmt: skip
    assert patch[:3] == ("proposed", "baobab-v1", world.document)
    assert [c["id"] for c in patch[3]] == ["MP-1"] and patch[3][0]["confirmed_by"] is None
    assert set(patch[4]) >= {"6.4"}
    # the same function again is not made twice
    again = world.tool("build_store_version", document_id=world.document, section_id="6.4",
                       settled=settled, proposal=moved_proposal())  # fmt: skip
    assert again["status"] == "refused" and "already built" in again["reason"]


def test_every_build_step_is_an_action_log_row_with_no_approver(world):
    settled = world.settle()
    arguments = {"document_id": world.document, "section_id": "6.4", "settled": settled,
                 "proposal": moved_proposal()}  # fmt: skip
    assert world.tool("build_check_function", **arguments)["passed"] is True
    stored = world.tool("build_store_version", **arguments)
    world.tool("build_record_review", measure_id=stored["measure_id"], version=stored["version"],
               result="pass")  # fmt: skip
    rows = log(world, "build_%") + log(world, "registry_%")
    names = [r[0] for r in rows]
    for name in ("build_settle_rules", "build_check_function", "build_store_version",
                 "build_record_review", "registry_create_version", "registry_advance_status",
                 "registry_record_tests", "registry_record_review"):  # fmt: skip
        assert name in names, name
    assert all(r[2] is None for r in rows), "the build is by the system: no one is recorded"
    assert [r[1] for r in rows].count("ok") == len(rows)


def test_a_function_the_checks_refuse_never_reaches_the_registry_or_the_patch_table(world):
    bad = moved_proposal(source=HARDCODED_SOURCE)
    refused_result, _ = world.store(bad)
    assert refused_result["status"] == "refused"
    codes = {p["code"] for p in refused_result["problems"]}
    assert {"hardcoded_value", "duplicates_template_value"} <= codes
    assert world.one("SELECT count(*) FROM measure_versions") == 0
    assert world.one("SELECT count(*) FROM template_patches") == 0


def test_the_review_can_hold_and_a_pass_moves_the_version_to_the_demo(world):
    held, _ = world.store()
    measure, version = held["measure_id"], held["version"]
    out = world.tool("build_record_review", measure_id=measure, version=version, result="hold",
                     reasons=["The boundary is not clear."])  # fmt: skip
    assert out["version_status"] == "awaiting_review" and out["review_result"] == "hold"
    assert world.one("SELECT status FROM measure_versions WHERE measure_id = %s", (measure,)) == (
        "awaiting_review"
    )
    # the review is written once: it cannot be turned into a pass afterwards
    again = world.tool("build_record_review", measure_id=measure, version=version, result="pass")
    assert again["status"] == "refused" and "already recorded" in again["reason"]
    assert world.one("SELECT review_result FROM measure_versions WHERE measure_id = %s",
                     (measure,)) == "hold"  # fmt: skip


def test_reasons_that_use_a_banned_word_are_recorded_as_a_hold_with_a_plain_reason(world):
    stored, _ = world.store()
    out = world.tool("build_record_review", measure_id=stored["measure_id"],
                     version=stored["version"], result="pass",
                     reasons=["Useful to find weak learners."])  # fmt: skip
    assert out["review_result"] == "hold" and out["reasons"] == [api.REASON_BANNED]
    assert out["version_status"] == "awaiting_review"


def test_a_signed_in_person_cannot_record_the_automated_review(world):
    stored, _ = world.store()
    out = world.tool("build_record_review", who=TEACHER, measure_id=stored["measure_id"],
                     version=stored["version"], result="pass")  # fmt: skip
    assert out["status"] == "refused" and "system step" in out["reason"]


# ----------------------------------------------------------------------------- the whole graph
def test_the_whole_graph_runs_on_baobab_against_the_registry(world):
    model = ScriptedModel(
        {
            ("extract", "6.4"): [GOOD_RULES],
            ("function", "6.4"): [json.dumps(moved_proposal())],
            ("review", "6.4"): [json.dumps({"result": "pass", "reasons": []})],
        }
    )
    server = LocalServer(world.conn, world.url)
    answer = asyncio.run(
        build_graph.run(document_id=world.document, build_anyway=["6.4"], run_id="RUN-T",
                        settings=SETTINGS, model=model, servers={MDEIS: server}, engine="plain")
    )  # fmt: skip
    assert answer["outcome"] == "finished", answer
    moved = next(s for s in answer["sections"] if s["section_id"] == "6.4")
    assert moved["outcome"] == "awaiting_demo" and moved["patch_id"].startswith("TP-")
    assert world.one("SELECT status FROM measure_versions WHERE measure_id = %s",
                     (moved["measure_id"],)) == "awaiting_demo"  # fmt: skip
    assert len(model.tasks(REVIEW)) == 1, "the reviewer was asked once, after the checks"


# ----------------------------------------------------------------------------- the patch table
def build_and_confirm(world):
    """A built measure, passed by the review, confirmed by the registered teacher."""
    stored, _ = world.store()
    measure, version = stored["measure_id"], stored["version"]
    world.tool("build_record_review", measure_id=measure, version=version, result="pass")
    done = registry.record_teacher_decision(
        world.ctx(TEACHER), measure_id=measure, version=version, decision="confirmed"
    )
    assert done["status"] == "ok", done
    return stored


def test_the_database_applies_a_patch_only_for_a_measure_a_teacher_confirmed(world):
    stored, _ = world.store()
    patch_id = stored["patch_id"]
    sql = (
        "UPDATE template_patches SET status = 'applied', applied_by = 'Ada', "
        "applied_at = now(), applied_as = 'baobab-v1' WHERE patch_id = %s"
    )
    error = refused(world.conn, psycopg.errors.RestrictViolation, sql, (patch_id,))
    assert "confirmed" in str(error)


def test_what_a_patch_is_never_changes_and_a_patch_is_never_deleted(world):
    stored, _ = world.store()
    patch_id = stored["patch_id"]
    for column, value in (("patch_text", "'{}'"), ("clauses", "'[]'::jsonb"),
                          ("base_template_version", "'v1.1'")):  # fmt: skip
        refused(world.conn, (psycopg.errors.RestrictViolation, psycopg.errors.CheckViolation),
                f"UPDATE template_patches SET {column} = {value} WHERE patch_id = %s", (patch_id,))
    gone = (psycopg.errors.RestrictViolation, psycopg.errors.InsufficientPrivilege)
    refused(world.conn, gone, "DELETE FROM template_patches WHERE patch_id = %s", (patch_id,))
    refused(world.conn, gone, "TRUNCATE template_patches")
    refused(world.conn, psycopg.errors.CheckViolation,
            "UPDATE template_patches SET status = 'rejected' WHERE patch_id = %s", (patch_id,))
    # a text that does not hash to its stored hash is refused at insert
    refused(world.conn, psycopg.errors.CheckViolation,
            "INSERT INTO template_patches (measure_id, version, source_document_id, "
            "base_template_version, section_hashes, patch_text, patch_sha256, clauses) "
            "SELECT measure_id, version, methodology_document_id, 'baobab-v1', '{}', 'x', "
            "repeat('a', 64), '[{}]' FROM measure_versions LIMIT 1")  # fmt: skip


def test_an_unconfirmed_measure_cannot_be_applied_and_the_refusal_is_logged(world, tmp_path):
    stored, _ = world.store()
    with pytest.raises(apply.ApplyRefused, match="not confirmed"):
        apply.apply_patches(world.conn, [stored["patch_id"]], "baobab-v2", "Ada Admin", tmp_path,
                            url=world.url)  # fmt: skip
    assert not list(tmp_path.iterdir()), "no file was written"
    assert [r[1] for r in log(world, "apply_template_patch")] == ["refused"]
    assert world.one("SELECT count(*) FROM templates") == 1, "no template version was loaded"


def test_a_confirmed_patch_becomes_a_new_template_version_the_measure_then_reads(world, tmp_path):
    stored = build_and_confirm(world)
    patch_id = stored["patch_id"]
    done = apply.apply_patches(world.conn, [patch_id], "baobab-v2", "Ada Admin", tmp_path,
                               url=world.url)  # fmt: skip
    assert done["loaded"].version == "baobab-v2" and done["loaded"].active is True
    assert done["declared_version"] == 2 and len(done["sha256"]) == 64
    assert any("MP-1" in line and "10" in line and "Ms Teacher" in line for line in done["added"])
    assert [p.name for p in tmp_path.iterdir()] == ["baobab-v2.yaml"]
    written = yaml.safe_load((tmp_path / "baobab-v2.yaml").read_text("utf-8"))
    assert [r["confirmed_by"] for r in written["measure_params"]] == ["Ms Teacher"]
    assert load_template(tmp_path / "baobab-v2.yaml").version == 2

    versions = dict(world.conn.execute("SELECT version_name, active FROM templates").fetchall())
    assert versions == {"baobab-v1": False, "baobab-v2": True}
    assert world.one("SELECT count(*) FROM clauses WHERE version_name = 'baobab-v2'") == (
        world.one("SELECT count(*) FROM clauses WHERE version_name = 'baobab-v1'") + 1
    )
    row = world.conn.execute(
        "SELECT status, applied_by, applied_as, confirmed_by_name FROM template_patches "
        "WHERE patch_id = %s", (patch_id,),
    ).fetchone()  # fmt: skip
    assert row == ("applied", "Ada Admin", "baobab-v2", "Ms Teacher")
    logged = world.conn.execute(
        "SELECT outcome, approver_id, outputs FROM action_log WHERE tool = 'apply_template_patch'"
    ).fetchone()  # fmt: skip
    assert logged[0] == "ok" and logged[1] is None and logged[2]["added"] == done["added"]

    # the patch is closed, and applying it again is refused
    refused(world.conn, psycopg.errors.RestrictViolation,
            "UPDATE template_patches SET applied_by = 'Someone Else' WHERE patch_id = %s",
            (patch_id,))  # fmt: skip
    with pytest.raises(apply.ApplyRefused, match="applied, not proposed"):
        apply.apply_patches(world.conn, [patch_id], "baobab-v3", "Ada Admin", tmp_path,
                            url=world.url)  # fmt: skip

    # the measure now reads its values from the active template, because a teacher confirmed them
    content = world.one("SELECT content FROM templates WHERE active")
    values = params_for(content, ["MP-1", "TR-1.window_terms", "TR-1.min_scores"],
                        ["MP-1", "TR-1"], separator=".")  # fmt: skip
    assert values == {"MP-1": "10", "TR-1.window_terms": "3", "TR-1.min_scores": "2"}


def test_a_name_that_is_taken_and_a_template_that_moved_on_are_refused(world, tmp_path):
    stored = build_and_confirm(world)
    with pytest.raises(apply.ApplyRefused, match="already exists"):
        apply.apply_patches(world.conn, [stored["patch_id"]], "baobab-v1", "Ada Admin", tmp_path,
                            url=world.url)  # fmt: skip
    rulebook.load_file(world.conn, OCC, activate=True, url=world.url, by="test")
    with pytest.raises(apply.ApplyRefused, match="build the measure again"):
        apply.apply_patches(world.conn, [stored["patch_id"]], "baobab-v2", "Ada Admin", tmp_path,
                            url=world.url)  # fmt: skip
    assert not list(tmp_path.iterdir())


def test_the_step_needs_a_name_and_a_version_name_that_is_a_file_name(world, tmp_path):
    stored = build_and_confirm(world)
    for by, name in (("", "baobab-v2"), ("Ada", "../v2"), ("Ada", "")):
        with pytest.raises(apply.ApplyRefused):
            apply.apply_patches(world.conn, [stored["patch_id"]], name, by, tmp_path, url=world.url)


# ----------------------------------------------------------------------------- the registry option
def test_declared_params_adds_the_two_value_checks_and_off_it_checks_as_before(world):
    source = (
        'OUTPUT_KIND = "number"\n\n\ndef measure(records, params):\n    return {"value": "n"}\n'
    )
    section = next(s for s in parsed().sections if s.id == "6.4")
    common = {
        "measure_name": "reads_nothing", "method_ref": "6.4", "description": "reads no value",
        "input_scope": "learner", "source_code": source, "output_kind": "number",
        "output_schema": {"kind": "number"}, "methodology_document_id": world.document,
        "section_sha256": section.sha256, "clause_ids": ["TR-1"], "template_version": "baobab-v1",
    }  # fmt: skip
    plain = registry.create_version(world.ctx(), **common)
    assert plain["static_check"]["passed"] is True, "a version registered without it is as before"
    strict = registry.create_version(world.ctx(), **{**common, "measure_name": "also_reads_nothing",
                                                     "declared_params": True})  # fmt: skip
    assert strict["static_check"]["passed"] is False
    assert [f["code"] for f in strict["static_check"]["findings"]] == ["param_unused"]
