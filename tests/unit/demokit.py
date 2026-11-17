"""Set-up shared by the teacher demo tests (T-073). It is not a test file.

A throwaway schema with every migration and the Baobab College rulebook with NO measure parameter
active (so `MP-1` lives only in the template patch a build proposed, as it does at the demo), the
people the registry knows about, and hand-made versions waiting at the demo, made through the real
registry functions (the same walk a build makes).

The hand-made measure is the one of T-072 (tests/unit/measurekit.py): the change across the window
of one subject, and whether it reaches the moved threshold. It reads `MP-1` (the threshold, 10, from
the patch), `TR-1.window_terms` (3) and `TR-1.min_scores` (2). The expected answers are worked by
hand from data/synthetic/methodology/methodology-baobab.md, sections 5 and 6.4:

    English 52, 58, 63            change 63 - 52 = 11, 3 used, threshold met
    English 60, 70                2 scores meet the minimum, change 10, met (case B-06)
    Mathematics 40, 62, 66, 64    only the last 3 count: change 64 - 62 = 2, not met
    Science 64                    1 score is below the minimum: enough_scores 0, scores_used 1
"""

import hashlib
import json
import os
from pathlib import Path

import psycopg
from measurekit import (
    CLAUSES,
    DESCRIPTION,
    NAME,
    OUTPUT_SCHEMA,
    SOURCE,
    TEACHER,
    build_schema,
    context,
    drop_schema,
    methodology_document,
    mp_rule,
)
from psycopg.types.json import Jsonb

from mdeis import registry, rulebook
from mdeis.tools.context import Approver

__all__ = ["build_schema", "drop_schema", "TEACHER", "SECOND", "DEVELOPER", "STRANGER", "setup"]

SECOND = Approver("u-second", "Mr Second")
DEVELOPER = Approver("u-dev", "Dev One")
STRANGER = Approver("u-nobody", "Someone Else")
REVOKED = Approver("u-gone", "Ms Gone")

QUOTE = "The scores, oldest first, were 52, 58 and 63. The change was 63 minus 52, which is 11"
RULE_QUOTE = "A change of 10 points or more out of 100 counts as moved."
SECTION_TEXT = "6.4 Moved. " + RULE_QUOTE + " " + QUOTE + "."

WORKED = {"change": "11", "enough_scores": "1", "scores_used": "3", "threshold_met": "1"}
BUILD_CASE = {"change": "2", "enough_scores": "1", "scores_used": "3", "threshold_met": "0"}


def tests_for(independent: bool = True) -> list:
    """The tests a build stores: records hold the score only, as `record_fields` says."""
    made = [{
        "name": "four scores, only the last three",
        "source": "build",
        "records": [{"score": s} for s in ("40", "62", "66", "64")],
        "expected": BUILD_CASE,
    }]
    if independent:
        made.append({
            "name": "worked example 6.4",
            "source": "document_example",
            "quote": QUOTE,
            "quote_section": "6.4",
            "records": [{"score": s} for s in ("52", "58", "63")],
            "expected": WORKED,
        })
    return made


def setup(base_url: str, directory: Path) -> tuple[str, str]:
    """(schema name, its url). `baobab-v1` (no measure parameter) is active."""
    name, url = build_schema(base_url, directory)
    with psycopg.connect(url) as conn:
        rulebook.activate_version(conn, "baobab-v1", url=url, by="test")
        conn.execute(
            "INSERT INTO registry_reviewers (user_id, role, name, granted_by) VALUES "
            "(%s, 'teacher', %s, 'admin'), (%s, 'developer', %s, 'admin'), "
            "(%s, 'teacher', %s, 'admin')",
            (SECOND.id, SECOND.name, DEVELOPER.id, DEVELOPER.name, REVOKED.id, REVOKED.name),
        )
        conn.execute("UPDATE registry_reviewers SET revoked_at = now() WHERE user_id = %s",
                     (REVOKED.id,))
        conn.commit()
    return name, url


def document_with_sections(conn) -> str:
    """A methodology document with the one section the measure quotes (page 3, section 6.4)."""
    document = methodology_document(conn)
    conn.execute(
        "INSERT INTO method_chunks (document_id, section_id, title, page, ordinal, text, "
        "text_sha256) VALUES (%s, '6.4', 'Moved', 3, 0, %s, %s)",
        (document, SECTION_TEXT, hashlib.sha256(SECTION_TEXT.encode()).hexdigest()),
    )
    return document


def at_demo(
    url: str,
    *,
    name: str = NAME,
    method_ref: str = "6.4",
    independent: bool = True,
    review: str = "pass",
    clear_hold: bool = False,
    measure_id: str | None = None,
    patch: bool = True,
    source: str = SOURCE,
    description: str = DESCRIPTION,
    stop_after: str = "awaiting_demo",
    reasons=("agrees with the quoted section",),
) -> dict:
    """Make a version and walk it as far as it can go. Returns {measure_id, version, patch_id,
    document_id}. With review="hold" it stays at awaiting_review unless clear_hold is set."""
    clause = mp_rule(confirmed_by=None)
    with psycopg.connect(url) as conn:
        document = document_with_sections(conn)
        system = context(url, conn, None, True)
        made = registry.create_version(
            system,
            measure_name=name,
            method_ref=method_ref,
            description=description,
            input_scope="learner_skill",
            source_code=source,
            output_kind="number",
            output_schema=OUTPUT_SCHEMA,
            methodology_document_id=document,
            section_sha256=hashlib.sha256(SECTION_TEXT.encode()).hexdigest(),
            clause_ids=list(CLAUSES),
            template_version="baobab-v1",
            tests=tests_for(independent),
            template_patch={"base_template_version": "baobab-v1", "measure_params": [clause]},
            rules_quoted=[{"name": "moved_at_or_above", "setting": None, "quote": RULE_QUOTE,
                           "quote_section": "6.4", "key": "MP-1", "source": "proposed"}],
            measure_id=measure_id,
        )
        assert made["status"] == "ok", made
        which = {"measure_id": made["measure_id"], "version": made["version"]}
        patch_id = None
        if patch:
            patch_id = store_patch(conn, which, document, [clause])
        names = [t["name"] for t in tests_for(independent)]
        done = [{"name": n, "passed": True} for n in names]
        assert registry.advance_status(system, **which, to="testing")["status"] == "ok"
        assert registry.record_tests(system, **which, results=done)["status"] == "ok"
        assert registry.advance_status(system, **which, to="awaiting_review")["status"] == "ok"
        recorded = registry.record_review(
            system, **which, result=review, reasons=list(reasons))
        assert recorded["status"] == "ok", recorded
        if review == "hold" and clear_hold:
            dev = context(url, conn, DEVELOPER, True)
            cleared = registry.clear_hold(dev, **which, note="read it; the section agrees")
            assert cleared["status"] == "ok", cleared
        if review == "pass" or clear_hold:
            moved = registry.advance_status(system, **which, to="awaiting_demo")
            assert moved["status"] == "ok", moved
        if stop_after == "approved":
            teacher = context(url, conn, TEACHER, True)
            decided = registry.record_teacher_decision(
                teacher, **which, decision="confirmed")
            assert decided["status"] == "ok", decided
        return {**which, "patch_id": patch_id, "document_id": document}


def store_patch(conn, which: dict, document: str, clauses: list) -> str:
    text = json.dumps({"base_template_version": "baobab-v1", "measure_params": clauses},
                      sort_keys=True)
    row = conn.execute(
        "INSERT INTO template_patches (measure_id, version, source_document_id, "
        "base_template_version, section_hashes, patch_text, patch_sha256, clauses) "
        "VALUES (%s, %s, %s, 'baobab-v1', %s, %s, %s, %s) RETURNING patch_id",
        (which["measure_id"], which["version"], document,
         Jsonb({"6.4": hashlib.sha256(SECTION_TEXT.encode()).hexdigest()}), text,
         hashlib.sha256(text.encode("utf-8")).hexdigest(), Jsonb(clauses)),
    ).fetchone()
    return row[0]


def build_plan_row(url: str, document_id: str, skipped: list, anyway: list) -> None:
    """The action-log row the build's plan step writes: the skipped sections and the sections built
    anyway. The demo reads it back (`store.latest_skipped`)."""
    from mdeis.db import diary

    diary.write(
        "build_plan", run_id="BUILD-demo-test",
        inputs={"document_id": document_id, "build_anyway": anyway},
        outputs={"status": "ok", "document_id": document_id, "skipped": skipped,
                 "build_anyway": anyway},
        outcome="ok", url=url,
    )


def ctx_for(url: str, who: Approver | None, school: str | None = "BAO"):
    made = context(url, None, who, True)
    made.school = school
    return made


def status_of(url: str, which: dict) -> dict:
    with psycopg.connect(url) as conn:
        row = conn.execute(
            "SELECT status, teacher_decision, teacher_name, teacher_note, tests "
            "FROM measure_versions WHERE measure_id = %s AND version = %s",
            (which["measure_id"], which["version"]),
        ).fetchone()
    return {"status": row[0], "decision": row[1], "teacher": row[2], "note": row[3],
            "tests": row[4]}


def log_rows(url: str, tool: str) -> list:
    with psycopg.connect(url) as conn:
        return conn.execute(
            "SELECT tool, inputs, outputs, outcome, approver_id, approver_name "
            "FROM action_log WHERE tool = %s ORDER BY event_id",
            (tool,),
        ).fetchall()


def have_database() -> str | None:
    return os.environ.get("DATABASE_URL")
