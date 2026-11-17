"""The run-time tools for approved measures: `list_measures` and `compute_measure` (T-072).

The first group needs no database: the signatures, that neither tool names a person or takes a
number, the allow-list, the server's listing, and the wording of what the model reads. The second
group is integration (needs DATABASE_URL). It builds a throwaway schema with the Baobab rulebook in
five variants, registers a small hand-made measure through the real registry functions, and runs the
tools on learners whose answers are worked out by hand (see tests/unit/measurekit.py):

    English 52, 58, 63          change 63 - 52 = 11, window 3, minimum 2, threshold 10: met
    Mathematics 40, 62, 66, 64  last 3 only: change 64 - 62 = 2: not met
    English 60, 70              2 scores: change 10, "10 or more": met (Baobab case B-06)
    Science 64                  1 score is below the minimum of 2: not enough evidence

The runner is the real child, called in this process (tests/unit/inprocess_runner.py): the real
static check, the real restricted namespace, the real exact-text conversion, with no socket.
"""

import ast
import asyncio
import hashlib
import importlib
import inspect
import re
import uuid
from pathlib import Path

import psycopg
import pytest
import yaml
from inprocess_runner import Calls
from measurekit import (
    CLAUSES,
    DESCRIPTION,
    NAME,
    SOURCE,
    SOURCE_V2,
    active,
    add_learner,
    add_patch,
    build_schema,
    context,
    drop_schema,
    mp_rule,
    record_ids,
    register,
)

from mdeis import registry
from mdeis.sandbox import client
from mdeis.tools import measures as measure_tools
from mdeis.tools.context import Approver, ToolContext

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
ADA = Approver("u-ada", "Ada Teacher")
NAMING_A_PERSON = {
    "approver", "approver_id", "approver_name", "user", "user_id", "user_name", "teacher",
    "teacher_id", "by", "who", "reviewer", "person", "signed_in",
}  # fmt: skip
EXTRA_WORDS = ["label", "rank", "ranking", "ranked", "pathway", "career", "best", "worst"]


def tool(name):
    return getattr(importlib.import_module("mdeis.tools"), name)


def banned_words() -> list:
    """The banned words of every shipped rulebook, and the labels the product never uses."""
    words = set(EXTRA_WORDS)
    for path in sorted((ROOT / "templates").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for rule in data.get("wording") or []:
                words.update(w.lower() for w in rule.get("banned") or [])
    return sorted(words)


def tokens(text: str) -> set:
    return {t for t in re.split(r"[^A-Za-z0-9]+", text.lower()) if t}


# =============================================================================== no database
def test_both_tools_take_the_context_first_and_no_argument_that_names_a_person():
    for name in ("list_measures", "compute_measure"):
        parameters = list(inspect.signature(tool(name)).parameters)
        assert parameters[0] == "ctx"
        assert not NAMING_A_PERSON & set(parameters), parameters


def test_the_arguments_are_exactly_these_and_none_of_them_is_a_number_or_a_rulebook_value():
    assert list(inspect.signature(tool("list_measures")).parameters) == ["ctx"]
    assert list(inspect.signature(tool("compute_measure")).parameters) == [
        "ctx", "measure_id", "learner_id", "skill",
    ]  # fmt: skip


def test_each_tool_is_logged_under_its_own_name():
    for name in ("list_measures", "compute_measure"):
        assert tool(name).__wrapped__.__name__ == name
    source = inspect.getsource(measure_tools)
    assert 'logged_tool("list_measures")' in source
    assert 'logged_tool("compute_measure")' in source


def test_the_tool_package_offers_the_two_new_tools():
    offered = set(importlib.import_module("mdeis.tools").__all__)
    assert {"list_measures", "compute_measure"} <= offered


def test_neither_tool_writes_registers_approves_or_names_an_approver():
    text = (SRC / "mdeis" / "tools" / "measures.py").read_text(encoding="utf-8")
    for word in ("INSERT", "UPDATE", "DELETE", "approver", "mdeis.registry", "from ..registry"):
        assert word not in text, word
    tree = ast.parse(text)
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(m and "registry" in m for m in imported)


def test_no_school_number_is_written_in_the_new_code():
    for path in (SRC / "mdeis" / "core" / "measures.py", SRC / "mdeis" / "tools" / "measures.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = {
            n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
            and not isinstance(n.value, bool)
        }  # fmt: skip
        assert found <= {0, 1}, (path.name, sorted(found - {0, 1}))


def test_the_tools_reach_the_sandbox_only_through_the_call_path_that_builds_the_values():
    # test_sandbox_supervisor.py bars the client, the supervisor and the child from every file the
    # model can reach. compute_measure needs the runner, so it goes through `with_template`, which
    # builds the school's values first and refuses before anything is sent.
    for path in sorted((SRC / "mdeis" / "tools").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if path.name != "measures.py":
            assert "sandbox" not in text, path.name
            continue
        assert "sandbox import with_template" in text
        for word in ("sandbox.client", "sandbox import client", "sandbox.supervisor",
                     "sandbox.child", "MEASURE_RUNNER"):
            assert word not in text, word


# ----------------------------------------------------------------------- the allow-list
def test_the_allow_list_gains_exactly_these_two_tools_and_nothing_else():
    from orchestrator import tools as allow

    before = {
        "ingest_assessments", "resolve_issue", "compute_trend", "get_criterion", "draft_summary",
        "check_citations", "make_chart", "release_report",
    }  # fmt: skip
    assert set(allow.MDEIS_TOOLS) - before == {"list_measures", "compute_measure"}
    assert before <= set(allow.MDEIS_TOOLS)
    assert len(allow.MDEIS_TOOLS) == len(set(allow.MDEIS_TOOLS)) == 10
    charts = [n for n in allow.MDEIS_TOOLS if "chart" in n]
    assert charts == ["make_chart"], "no new chart tool"


def test_the_model_is_offered_both_and_neither_is_dropped_for_an_approver_like_argument():
    from orchestrator import tools as allow

    def spec(name, properties):
        return {"name": name, "description": name, "inputSchema": {"properties": properties}}

    listed = {
        allow.MDEIS: [
            spec("list_measures", {}),
            spec("compute_measure", {"measure_id": {}, "learner_id": {}, "skill": {}}),
        ]
    }
    definitions, owner, dropped = allow.bind(listed, ())
    assert {d["function"]["name"] for d in definitions} == {"list_measures", "compute_measure"}
    assert dropped == []


# ----------------------------------------------------------------------- the server's listing
def server_function(name):
    tree = ast.parse((SRC / "mdeis" / "server.py").read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def test_the_server_registers_list_measures_with_no_argument_but_the_request():
    assert [a.arg for a in server_function("list_measures").args.args] == ["request"]


def test_the_server_passes_compute_measure_only_the_measure_the_learner_and_the_skill():
    function = server_function("compute_measure")
    assert [a.arg for a in function.args.args] == ["request", "measure_id", "learner_id", "skill"]
    calls = [n for n in ast.walk(function) if isinstance(n, ast.Call)]
    run = next(c for c in calls if getattr(c.func, "id", "") == "_run")
    assert {k.arg for k in run.keywords} == {"measure_id", "learner_id", "skill"}


def test_the_server_gives_make_chart_the_result_hashes_and_nothing_that_could_be_a_number():
    function = server_function("make_chart")
    assert [a.arg for a in function.args.args] == [
        "request", "learner_id", "skills", "template_version", "measure_hashes", "measure_field",
    ]  # fmt: skip
    calls = [n for n in ast.walk(function) if isinstance(n, ast.Call)]
    run = next(c for c in calls if getattr(c.func, "id", "") == "_run")
    assert {k.arg for k in run.keywords} == {
        "learner_id", "skills", "template_version", "measure_hashes", "measure_field",
    }


def test_make_chart_keeps_its_old_positions_so_a_call_that_worked_still_works():
    parameters = list(inspect.signature(tool("make_chart")).parameters)
    assert parameters == [
        "ctx", "learner_id", "skills", "template_version", "measure_hashes", "measure_field",
    ]  # fmt: skip


# ----------------------------------------------------------------------- what the model reads
def test_what_the_model_reads_about_the_tools_carries_no_banned_wording():
    words = set(banned_words())
    assert {"weak", "remedial", "gifted"} <= words, "the list is read from the shipped rulebooks"
    for name in ("list_measures", "compute_measure", "make_chart"):
        doc = inspect.getdoc(tool(name))
        assert doc and tokens(doc).isdisjoint(words), (name, tokens(doc) & words)


def test_the_server_descriptions_of_the_three_tools_carry_no_banned_wording_either():
    pytest.importorskip("mcp")
    from mdeis import server

    listed = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    words = set(banned_words())
    for name in ("list_measures", "compute_measure", "make_chart"):
        shown = " ".join([listed[name].description or "", yaml.safe_dump(listed[name].inputSchema)])
        assert tokens(shown).isdisjoint(words), (name, tokens(shown) & words)
    assert set(listed["compute_measure"].inputSchema["properties"]) == {
        "measure_id", "learner_id", "skill",
    }


def test_the_descriptions_say_the_model_never_computes_and_that_no_measure_is_a_valid_answer():
    text = " ".join(inspect.getdoc(tool("compute_measure")).lower().split())  # one line
    assert "never" in text and "number" in text
    assert "no measure applies" in text or "no_tool_applies" in text


# =============================================================================== with a database
@pytest.fixture(scope="module")
def world(tmp_path_factory):
    import os

    base = os.environ.get("DATABASE_URL")
    if not base:
        pytest.skip("DATABASE_URL is not set: integration tests skipped")
    name, url = build_schema(base, tmp_path_factory.mktemp("variants"))
    try:
        learners = {
            "amina": add_learner(
                url,
                {"English": [52, 58, 63], "Mathematics": [40, 62, 66, 64], "Science": [64]},
            ),
            "bola": add_learner(url, {"English": [60, 70]}),
            "carol": add_learner(url, {"Humanities": [60, None, 70]}),
        }
        ms = {
            "approved": register(url),
            "proposed": register(url, stop_after="proposed", name="A proposed measure",
                                 method_ref="6.5"),
            "demo": register(url, stop_after="awaiting_demo", name="A measure at the demo",
                             method_ref="6.6"),
            "rejected": register(url, stop_after="rejected", name="A rejected measure",
                                 method_ref="6.7"),
            "table": register(
                url, name="A table", method_ref="6.8",
                source=SOURCE.replace('OUTPUT_KIND = "number"', 'OUTPUT_KIND = "table"'),
                output_kind="table", output_schema={"kind": "table"},
            ),
            "wording": register(
                url, name="Weak start finder", method_ref="6.9",
                description="Finds a weak pupil from the scores.",
            ),
            "no_numbers": register(
                url, name="Gives no number", method_ref="6.10",
                source=SOURCE.replace(
                    "    used = records[-window:]\n",
                    "    used = records[-window:]\n    return {}\n",
                ),
            ),
            "stranger": register(
                url, name="Names a record it was not given", method_ref="6.11",
                source=SOURCE.replace(
                    '        "threshold_met": reached,\n',
                    '        "threshold_met": reached,\n        "record_ids": ["A-999999"],\n',
                ),
            ),
            "crashes": register(
                url, name="Divides by zero", method_ref="6.12",
                source=SOURCE.replace(
                    'change = Fraction(used[-1]["score"]) - Fraction(used[0]["score"])',
                    "change = Fraction(1, 0)",
                ),
            ),
        }
        patched = register(url, name="Built against a rational", method_ref="6.14")
        # The build recorded MP-1 as a rational for this version; the template now holds an integer.
        add_patch(url, patched, [mp_rule(value_type="rational", value="10")])
        ms["patched"] = patched
        retired = register(url, name="Withdrawn measure", method_ref="6.13")
        retiring = context(url)
        registry.retire(retiring, **retired, reason="withdrawn")
        retiring.conn.close()
        ms["retired"] = retired
        yield {"url": url, "learners": learners, "ms": ms}
    finally:
        drop_schema(base, name)


@pytest.fixture
def opened():
    connections = []
    yield connections
    for c in connections:
        c.rollback()
        c.close()


@pytest.fixture
def make_ctx(world, opened):
    def make(approver=ADA, run_id=None, school=None):
        conn = psycopg.connect(world["url"])
        opened.append(conn)
        return ToolContext(
            conn=conn,
            run_id=run_id or f"RUN-{uuid.uuid4()}",
            approver=approver,
            database_url=world["url"],
            commit=False,
            school=school,
        )

    return make


@pytest.fixture
def runner(monkeypatch):
    """The real child, in this process, and a record of every call it was given."""
    spy = Calls()
    monkeypatch.setattr(client, "run", spy)
    return spy


@pytest.fixture
def no_runner(monkeypatch):
    """A runner that fails the test if anything reaches it."""

    def refuse(*args, **kwargs):
        raise AssertionError("the runner must not be reached")

    monkeypatch.setattr(client, "run", refuse)


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute(ctx, world, who="amina", skill="English", measure="approved"):
    return tool("compute_measure")(
        ctx, measure_id=world["ms"][measure]["measure_id"],
        learner_id=world["learners"][who], skill=skill,
    )  # fmt: skip


def log_rows(world, run_id):
    with psycopg.connect(world["url"]) as conn:
        return conn.execute(
            "SELECT tool, outcome, inputs, outputs, approver_id, approver_name FROM action_log "
            "WHERE run_id = %s ORDER BY event_id",
            (run_id,),
        ).fetchall()


# ----------------------------------------------------------------------- list_measures
@pytest.mark.integration
def test_the_approved_measure_in_force_is_listed_with_what_a_person_needs_to_cite_it(
    world, make_ctx
):
    ctx = make_ctx()
    listed = tool("list_measures")(ctx)
    assert listed["status"] == "ok" and listed["template_version"] == "baobab-mp"
    wanted = world["ms"]["approved"]["measure_id"]
    mine = [m for m in listed["measures"] if m["measure_id"] == wanted]
    assert len(mine) == 1
    entry = mine[0]
    assert entry["version"] == 1 and entry["name"] == NAME and entry["description"] == DESCRIPTION
    assert entry["output_kind"] == "number" and entry["input_scope"] == "learner_skill"
    assert entry["clause_ids"] == CLAUSES and entry["method_ref"] == "6.4"
    assert entry["section_sha256"] == hash_text("6.4")
    assert entry["code_sha256"] == hash_text(SOURCE)
    assert re.fullmatch(r"D-\d{6,}", entry["methodology_document_id"])
    assert set(entry) == {
        "measure_id", "version", "name", "description", "input_scope", "output_kind", "clause_ids",
        "method_ref", "methodology_document_id", "section_sha256", "code_sha256",
    }, "no source code, no tests, no names of reviewers"


@pytest.mark.integration
def test_nothing_unapproved_retired_or_unsupported_is_listed(world, make_ctx):
    listed = tool("list_measures")(make_ctx())
    shown = {m["measure_id"] for m in listed["measures"]}
    ms = world["ms"]
    assert ms["approved"]["measure_id"] in shown
    for kept_out in ("proposed", "demo", "rejected", "retired", "table", "wording", "patched"):
        assert ms[kept_out]["measure_id"] not in shown, kept_out
    assert all(m["output_kind"] == "number" for m in listed["measures"]), "number only in v1"


@pytest.mark.integration
@pytest.mark.parametrize("variant", ["baobab-v1", "baobab-mp-open", "baobab-mp-rational"])
def test_a_measure_is_not_listed_when_its_clauses_are_missing_unconfirmed_or_changed(
    world, make_ctx, variant
):
    with active(world["url"], variant):
        listed = tool("list_measures")(make_ctx())
    assert listed["template_version"] == variant
    shown = {m["measure_id"] for m in listed["measures"]}
    assert world["ms"]["approved"]["measure_id"] not in shown


@pytest.mark.integration
def test_a_changed_value_keeps_the_measure_listed(world, make_ctx):
    with active(world["url"], "baobab-mp-12"):
        listed = tool("list_measures")(make_ctx())
    assert world["ms"]["approved"]["measure_id"] in {m["measure_id"] for m in listed["measures"]}


@pytest.mark.integration
def test_the_listing_is_the_same_each_time_and_carries_a_hash(world, make_ctx):
    first, second = (tool("list_measures")(make_ctx()) for _ in range(2))
    assert first == second and re.fullmatch(r"[0-9a-f]{64}", first["result_hash"])


@pytest.mark.integration
def test_a_listing_is_one_row_in_the_action_log_with_the_signed_in_person(world, make_ctx):
    ctx = make_ctx(ADA)
    tool("list_measures")(ctx)
    rows = [r for r in log_rows(world, ctx.run_id) if r[0] == "list_measures"]
    assert len(rows) == 1
    _, outcome, inputs, outputs, approver_id, approver_name = rows[0]
    assert outcome == "ok" and inputs == {}
    assert (approver_id, approver_name) == ("u-ada", "Ada Teacher")
    assert outputs["status"] == "ok"


@pytest.mark.integration
def test_a_listing_with_nobody_signed_in_is_allowed_and_logged_with_no_name(world, make_ctx):
    ctx = make_ctx(None)
    assert tool("list_measures")(ctx)["status"] == "ok"
    row = [r for r in log_rows(world, ctx.run_id) if r[0] == "list_measures"][0]
    assert row[4] is None and row[5] is None


@pytest.mark.integration
def test_a_signed_in_person_cannot_be_named_in_an_argument(world, make_ctx):
    ctx = make_ctx()
    with pytest.raises(TypeError):
        tool("list_measures")(ctx, approver="Someone")
    with pytest.raises(TypeError):
        tool("compute_measure")(ctx, measure_id="MS-0001", learner_id="L-0001", approver="Someone")
    with pytest.raises(TypeError):
        tool("compute_measure")(
            ctx, measure_id="MS-0001", learner_id="L-0001", params={"MP-1": "1"}
        )


# ----------------------------------------------------------------------- compute_measure: numbers
@pytest.mark.integration
def test_the_worked_example_gives_the_hand_worked_answer_and_everything_needed_to_cite_it(
    world, make_ctx, runner
):
    ctx = make_ctx()
    got = compute(ctx, world)
    ids = record_ids(world["url"], world["learners"]["amina"], "English")
    assert got["status"] == "ok" and got["output_kind"] == "number"
    assert got["result"] == {
        "fields": {
            "change": {"value": "11.00", "exact": "11"},
            "enough_scores": {"value": "1.00", "exact": "1"},
            "scores_used": {"value": "3.00", "exact": "3"},
            "threshold_met": {"value": "1.00", "exact": "1"},
        }
    }
    assert got["record_ids"] == ids and len(ids) == 3
    assert got["clause_ids"] == ["MP-1", "TR-1"]
    assert (got["measure_id"], got["measure_version"], got["name"]) == (
        world["ms"]["approved"]["measure_id"], 1, NAME,
    )  # fmt: skip
    assert got["code_sha256"] == hash_text(SOURCE) and got["section_sha256"] == hash_text("6.4")
    assert got["method_ref"] == "6.4"
    assert re.fullmatch(r"D-\d{6,}", got["measure_methodology_document_id"])
    assert got["learner_id"] == world["learners"]["amina"] and got["skill"] == "English"
    assert got["template_version"] == "baobab-mp"
    assert got["values_used"] == {"MP-1": "10", "TR-1.min_scores": "2", "TR-1.window_terms": "3"}
    assert re.fullmatch(r"[0-9a-f]{64}", got["result_hash"])
    assert [r["id"] for r in got["rules"]] == ["MP-1", "TR-1"]
    assert all(r["text"] for r in got["rules"])
    assert "sources" in got and "methodology_document_id" in got


@pytest.mark.integration
def test_only_the_last_three_scores_of_four_are_used(world, make_ctx, runner):
    got = compute(make_ctx(), world, skill="Mathematics")
    ids = record_ids(world["url"], world["learners"]["amina"], "Mathematics")
    # 62, 66, 64: change 64 - 62 = 2, below 10, so the threshold is not met. Three scores used.
    fields = got["result"]["fields"]
    assert fields["change"] == {"value": "2.00", "exact": "2"}
    assert fields["threshold_met"]["exact"] == "0" and fields["scores_used"]["exact"] == "3"
    # The function does not name the records it used, so all four it was given are cited.
    assert got["record_ids"] == ids and len(ids) == 4


@pytest.mark.integration
def test_ten_points_exactly_meets_a_ten_or_more_threshold_on_two_scores(world, make_ctx, runner):
    got = compute(make_ctx(), world, who="bola")
    # 60, 70: two scores meet the minimum of 2; change 70 - 60 = 10; 10 is at least 10.
    fields = got["result"]["fields"]
    assert fields["change"]["exact"] == "10" and fields["threshold_met"]["exact"] == "1"
    assert fields["scores_used"]["exact"] == "2"


@pytest.mark.integration
def test_one_score_is_below_the_minimum_and_the_function_says_so_in_numbers(
    world, make_ctx, runner
):
    ctx = make_ctx()
    got = compute(ctx, world, skill="Science")
    # Case B-07: one score, minimum 2. The function returns enough_scores 0 and the count, and no
    # change at all: nothing is worked out from one score.
    assert got["result"] == {
        "fields": {
            "enough_scores": {"value": "0.00", "exact": "0"},
            "scores_used": {"value": "1.00", "exact": "1"},
        }
    }
    assert re.fullmatch(r"[0-9a-f]{64}", got["result_hash"])
    row = [r for r in log_rows(world, ctx.run_id) if r[0] == "compute_measure"][0]
    assert row[1] == "ok"


@pytest.mark.integration
def test_the_same_input_gives_the_same_result_and_the_same_hash_in_two_runs(
    world, make_ctx, runner
):
    first = compute(make_ctx(), world)
    second = compute(make_ctx(), world)
    assert first == second and first["result_hash"] == second["result_hash"]


@pytest.mark.integration
def test_a_different_value_in_the_active_template_changes_the_answer_and_the_hash(
    world, make_ctx, runner
):
    first = compute(make_ctx(), world)
    with active(world["url"], "baobab-mp-12"):
        second = compute(make_ctx(), world)
    # With 12 as the threshold, the change of 11 does not reach it.
    assert second["values_used"]["MP-1"] == "12" and second["template_version"] == "baobab-mp-12"
    assert second["result"]["fields"]["change"]["exact"] == "11"
    assert second["result"]["fields"]["threshold_met"]["exact"] == "0"
    assert first["result_hash"] != second["result_hash"]


@pytest.mark.integration
def test_the_function_is_given_one_learners_one_subject_and_only_the_values_it_reads(
    world, make_ctx, runner
):
    compute(make_ctx(), world)
    assert len(runner) == 1
    sent = runner[0]
    ids = record_ids(world["url"], world["learners"]["amina"], "English")
    assert [r["record_id"] for r in sent["records"]] == ids
    assert {r["subject"] for r in sent["records"]} == {"English"}
    assert [r["score"] for r in sent["records"]] == ["52", "58", "63"]
    assert set(sent["params"]) == {"MP-1", "TR-1.min_scores", "TR-1.window_terms"}
    assert all(isinstance(v, str) for v in sent["params"].values())


@pytest.mark.integration
def test_the_result_has_no_banned_word_in_any_key_or_text(world, make_ctx, runner):
    got = compute(make_ctx(), world)
    words = set(banned_words())

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from walk(item)
        elif isinstance(value, list):
            for item in value:
                yield from walk(item)
        elif isinstance(value, str):
            yield value

    for text in walk(got):
        assert tokens(text).isdisjoint(words), text


# ----------------------------------------------------------------------- compute_measure: refusals
@pytest.mark.integration
def test_an_unknown_measure_and_every_unapproved_one_get_the_same_typed_refusal_and_it_is_logged(
    world, make_ctx, no_runner
):
    ctx = make_ctx()
    learner = world["learners"]["amina"]
    unknown = tool("compute_measure")(
        ctx, measure_id="MS-9999", learner_id=learner, skill="English"
    )
    assert unknown["status"] == "refused" and unknown["reason"] == "no_tool_applies"
    assert "result" not in unknown and "result_hash" not in unknown
    for name in ("proposed", "demo", "rejected", "retired"):
        got = compute(ctx, world, measure=name)
        assert got == unknown, f"{name}: the refusal does not describe what stage a measure is at"
    rows = [r for r in log_rows(world, ctx.run_id) if r[0] == "compute_measure"]
    assert len(rows) == 5 and {r[1] for r in rows} == {"refused"}
    assert rows[0][2]["measure_id"] == "MS-9999", "the log keeps the identifier that was asked for"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("variant", "reason"),
    [
        ("baobab-v1", "clause_missing"),
        ("baobab-mp-open", "placeholder_value"),
        ("baobab-mp-rational", "clause_changed"),
    ],
)
def test_a_measure_whose_clauses_are_gone_unconfirmed_or_changed_is_refused_with_the_reason(
    world, make_ctx, no_runner, variant, reason
):
    with active(world["url"], variant):
        got = compute(make_ctx(), world)
    assert got["status"] == "refused" and got["reason"] == reason
    assert "MP-1" in got["detail"] and "result" not in got and "result_hash" not in got


@pytest.mark.integration
def test_what_the_build_recorded_for_a_version_decides_whether_a_parameter_has_changed(
    world, make_ctx, no_runner
):
    got = compute(make_ctx(), world, measure="patched")
    assert got["status"] == "refused" and got["reason"] == "clause_changed"
    assert "MP-1" in got["detail"]


@pytest.mark.integration
def test_a_skill_is_needed_and_must_be_a_subject(world, make_ctx, no_runner):
    ctx = make_ctx()
    assert compute(ctx, world, skill=None)["reason"] == "skill_needed"
    assert compute(ctx, world, skill="Astronomy")["reason"] == "unknown_skill"
    area = compute(ctx, world, skill="literacy")
    assert area["reason"] == "unknown_skill", "an area is not a subject"


@pytest.mark.integration
def test_an_unknown_learner_is_refused_like_any_other_tool(world, make_ctx, no_runner):
    got = tool("compute_measure")(
        make_ctx(), measure_id=world["ms"]["approved"]["measure_id"], learner_id="L-9999",
        skill="English",
    )  # fmt: skip
    assert got["status"] == "refused" and "L-9999" in got["reason"]


@pytest.mark.integration
def test_a_subject_with_an_unanswered_blocking_problem_gets_no_number(world, make_ctx, no_runner):
    ctx = make_ctx()
    got = compute(ctx, world, who="carol", skill="Humanities")
    assert got["status"] == "blocked" and "result" not in got
    assert [r["kind"] for r in got["reasons"]] == ["missing_term"]
    row = [r for r in log_rows(world, ctx.run_id) if r[0] == "compute_measure"][0]
    assert row[1] == "blocked"


@pytest.mark.integration
def test_a_kind_the_first_version_cannot_return_is_refused_before_anything_runs(
    world, make_ctx, no_runner
):
    got = compute(make_ctx(), world, measure="table")
    assert got["status"] == "refused" and got["reason"] == "kind_not_supported"


@pytest.mark.integration
def test_a_measure_with_banned_wording_in_what_it_says_about_itself_is_not_offered_or_run(
    world, make_ctx, no_runner
):
    got = compute(make_ctx(), world, measure="wording")
    assert got["status"] == "refused" and got["reason"] == "withheld_wording"
    assert "weak" not in str(got).lower(), "the refusal does not repeat the word"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("measure", "kind"),
    [("no_numbers", "bad_output"), ("stranger", "bad_output"), ("crashes", "runtime")],
)
def test_an_answer_that_does_not_hold_up_is_refused_with_its_kind_and_no_number(
    world, make_ctx, runner, measure, kind
):
    got = compute(make_ctx(), world, measure=measure)
    assert got["status"] == "refused" and got["reason"] == "measure_failed" and got["kind"] == kind
    assert "result" not in got and "result_hash" not in got
    assert "A-999999" not in str(got), "a record the function invented is not repeated"


@pytest.mark.integration
@pytest.mark.parametrize("kind", ["timeout", "memory", "crash", "static", "bad_request"])
def test_a_failed_run_is_one_typed_refusal_with_no_second_try(world, make_ctx, monkeypatch, kind):
    calls = []

    def fails(*args, **kwargs):
        calls.append(1)
        raise client.RunnerRefusal(kind, "short detail")

    monkeypatch.setattr(client, "run", fails)
    got = compute(make_ctx(), world)
    assert got["status"] == "refused" and got["reason"] == "measure_failed" and got["kind"] == kind
    assert len(calls) == 1 and "result" not in got


@pytest.mark.integration
def test_a_runner_that_cannot_be_reached_is_its_own_reason(world, make_ctx, monkeypatch):
    def down(*args, **kwargs):
        raise client.RunnerRefusal("runner_unavailable", "no socket")

    monkeypatch.setattr(client, "run", down)
    got = compute(make_ctx(), world)
    assert got["status"] == "refused" and got["reason"] == "runner_unavailable"
    assert "result" not in got


@pytest.mark.integration
def test_every_compute_call_is_in_the_action_log_with_its_inputs_output_and_the_person(
    world, make_ctx, runner
):
    ctx = make_ctx(ADA)
    ok = compute(ctx, world)
    refused = compute(ctx, world, measure="proposed")
    rows = [r for r in log_rows(world, ctx.run_id) if r[0] == "compute_measure"]
    assert [r[1] for r in rows] == ["ok", "refused"]
    tool_name, outcome, inputs, outputs, approver_id, approver_name = rows[0]
    assert inputs == {
        "measure_id": world["ms"]["approved"]["measure_id"],
        "learner_id": world["learners"]["amina"],
        "skill": "English",
    }
    assert outputs["result_hash"] == ok["result_hash"] and outputs["result"] == ok["result"]
    assert (approver_id, approver_name) == ("u-ada", "Ada Teacher")
    assert rows[1][3]["reason"] == refused["reason"] == "no_tool_applies"


@pytest.mark.integration
def test_the_class_view_is_not_needed_and_a_measure_never_reads_two_learners(
    world, make_ctx, runner
):
    compute(make_ctx(), world)
    compute(make_ctx(), world, who="bola")
    amina = set(record_ids(world["url"], world["learners"]["amina"], "English"))
    bola = set(record_ids(world["url"], world["learners"]["bola"], "English"))
    assert {r["record_id"] for r in runner[0]["records"]} == amina
    assert {r["record_id"] for r in runner[1]["records"]} == bola
    assert not amina & bola


# ----------------------------------------------------------------------- pins
def draft_text(result):
    marks = " ".join(f"[{r}]" for r in result["record_ids"])
    return (
        f"The English scores changed by 11 points across 3 term scores {marks} [MP-1] [TR-1] "
        f"[{result['measure_id']}]."
    )


@pytest.mark.integration
def test_a_draft_remembers_the_measure_version_it_used_and_stores_the_result_as_returned(
    world, make_ctx, runner
):
    ctx = make_ctx()
    got = compute(ctx, world)
    drafted = tool("draft_summary")(
        ctx, learner_id=world["learners"]["amina"], text=draft_text(got),
        finding_hashes=[got["result_hash"]],
    )  # fmt: skip
    assert drafted["status"] == "pending_review"
    assert drafted["measures_used"] == [
        {"measure_id": got["measure_id"], "version": 1, "code_sha256": hash_text(SOURCE)}
    ]
    stored = ctx.conn.execute(
        "SELECT findings, template_version FROM reports WHERE report_id = %s",
        (drafted["report_id"],),
    ).fetchone()
    assert stored[1] == "baobab-mp"
    assert [(f["measure_id"], f["measure_version"], f["result_hash"]) for f in stored[0]] == [
        (got["measure_id"], 1, got["result_hash"])
    ]


@pytest.mark.integration
def test_a_draft_can_only_use_a_measure_result_the_run_returned(world, make_ctx, runner):
    earlier = make_ctx()
    got = compute(earlier, world)
    other_run = make_ctx()
    refused = tool("draft_summary")(
        other_run, learner_id=world["learners"]["amina"], text=draft_text(got),
        finding_hashes=[got["result_hash"]],
    )  # fmt: skip
    assert refused["status"] == "refused"
    assert "not returned by a tool in this run" in refused["reason"]


@pytest.mark.integration
def test_a_draft_about_another_learner_cannot_rest_on_this_learners_measure(
    world, make_ctx, runner
):
    ctx = make_ctx()
    got = compute(ctx, world)
    refused = tool("draft_summary")(
        ctx, learner_id=world["learners"]["bola"], text=draft_text(got),
        finding_hashes=[got["result_hash"]],
    )  # fmt: skip
    assert refused["status"] == "refused"


def checked(ctx, world, text=None, result=None):
    result = result or compute(ctx, world)
    drafted = tool("draft_summary")(
        ctx, learner_id=world["learners"]["amina"], text=text or draft_text(result),
        finding_hashes=[result["result_hash"]],
    )  # fmt: skip
    assert drafted["status"] == "pending_review", drafted
    report_id = drafted["report_id"]
    return result, report_id, tool("check_citations")(ctx, report_id=report_id)


# ----------------------------------------------------------------------- citations
@pytest.mark.integration
def test_a_finding_that_cites_a_measure_result_passes_the_check_and_leads_to_its_source(
    world, make_ctx, runner
):
    ctx = make_ctx()
    result, _, check = checked(ctx, world)
    assert check["status"] == "ok" and check["problems"] == []
    kinds = {s["id"]: s["kind"] for s in check["sources"]}
    assert kinds[result["measure_id"]] == "measure" and kinds["MP-1"] == "clause"
    measure = next(s for s in check["sources"] if s["id"] == result["measure_id"])
    assert (measure["name"], measure["version"], measure["method_ref"]) == (NAME, 1, "6.4")


@pytest.mark.integration
def test_citing_a_measure_no_tool_returned_in_the_run_fails(world, make_ctx, runner):
    ctx = make_ctx()
    result = compute(ctx, world)
    text = draft_text(result).replace(result["measure_id"], "MS-9999")
    _, _, check = checked(ctx, world, text=text, result=result)
    assert check["status"] == "fail"
    found = {(p["reason"], p["detail"]) for p in check["problems"]}
    assert found == {("unknown_measure", "MS-9999")}


@pytest.mark.integration
def test_citing_a_measure_returned_in_the_run_but_not_among_the_drafts_findings_fails(
    world, make_ctx, runner
):
    ctx = make_ctx()
    learner = world["learners"]["amina"]
    result = compute(ctx, world)  # returned in the run ...
    trend = tool("compute_trend")(ctx, learner_id=learner, skill="English")
    text = (
        "The English scores were "
        + " ".join(f"[{r}]" for r in trend["record_ids"])
        + f" [TR-1] and the measure was [{result['measure_id']}]."
    )
    drafted = tool("draft_summary")(
        ctx, learner_id=learner, text=text, finding_hashes=[trend["result_hash"]]
    )  # ... but the draft is built from the trend only
    check = tool("check_citations")(ctx, report_id=drafted["report_id"])
    assert check["status"] == "fail"
    assert [(p["reason"], p["detail"]) for p in check["problems"]] == [
        ("unknown_measure", result["measure_id"])
    ]


@pytest.mark.integration
def test_a_clause_a_record_or_a_number_the_measure_did_not_return_fails_as_for_a_trend(
    world, make_ctx, runner
):
    ctx = make_ctx()
    result = compute(ctx, world)
    others = record_ids(world["url"], world["learners"]["bola"], "English")
    text = draft_text(result).replace("[TR-1]", "[TR-2]").replace("11 points", "12 points")
    text += f" And [{others[0]}]."
    _, _, check = checked(ctx, world, text=text, result=result)
    reasons = {p["reason"] for p in check["problems"]}
    assert check["status"] == "fail"
    assert reasons == {"unknown_clause", "unknown_number", "unknown_record"}, check["problems"]


@pytest.mark.integration
def test_a_number_the_result_or_its_rule_wording_states_is_allowed_and_any_other_is_not(
    world, make_ctx, runner
):
    ctx = make_ctx()
    result = compute(ctx, world, who="amina", skill="Mathematics")  # value 2, scores used 3
    marks = " ".join(f"[{r}]" for r in result["record_ids"])

    def about(threshold):
        return (
            f"The Mathematics scores changed by 2 points {marks} [MP-1] [TR-1]. "
            f"The threshold of {threshold} points was not reached [MP-1]."
        )

    # 10 is the school's value, in the wording of MP-1 that the result carries, so it can be stated.
    _, _, check = checked(ctx, world, text=about(10), result=result)
    assert check["status"] == "ok", check["problems"]
    # 5 is in neither the result nor the wording of the rules it applied.
    _, _, again = checked(ctx, world, text=about(5), result=result)
    assert {p["reason"] for p in again["problems"]} == {"unknown_number"}


# ----------------------------------------------------------------------- release and the pin
def supersede(ctx, mid):
    """Approve a second version of the measure inside this test's transaction."""
    return register(
        ctx.database_url, conn=ctx.conn, commit=False, measure_id=mid, source=SOURCE_V2,
    )  # fmt: skip


@pytest.mark.integration
def test_a_draft_that_used_the_version_in_force_can_be_released(world, make_ctx, runner):
    ctx = make_ctx(ADA)
    _, report_id, check = checked(ctx, world)
    assert check["status"] == "ok"
    released = tool("release_report")(ctx, report_id=report_id)
    assert released["status"] == "released" and released["approver"] == "Ada Teacher"


@pytest.mark.integration
def test_a_draft_goes_stale_when_a_newer_version_of_the_measure_takes_over(
    world, make_ctx, runner
):
    ctx = make_ctx(ADA)
    result, report_id, check = checked(ctx, world)
    assert check["status"] == "ok"
    second = supersede(ctx, result["measure_id"])
    assert second["version"] == 2
    refused = tool("release_report")(ctx, report_id=report_id)
    assert refused["status"] == "refused" and refused["reason"] == "stale_pin"
    assert ctx.conn.execute(
        "SELECT status FROM reports WHERE report_id = %s", (report_id,)
    ).fetchone()[0] == "pending_review"


@pytest.mark.integration
def test_a_draft_goes_stale_when_the_measure_it_used_is_withdrawn(world, make_ctx, runner):
    ctx = make_ctx(ADA)
    result, report_id, _ = checked(ctx, world)
    registry.retire(
        ToolContext(conn=ctx.conn, run_id=ctx.run_id, approver=None, database_url=ctx.database_url,
                    commit=False),
        measure_id=result["measure_id"], version=1, reason="withdrawn",
    )  # fmt: skip
    assert tool("release_report")(ctx, report_id=report_id)["reason"] == "stale_pin"


@pytest.mark.integration
def test_a_released_report_is_never_recomputed_under_a_newer_version(world, make_ctx, runner):
    ctx = make_ctx(ADA)
    learner = world["learners"]["bola"]
    result = compute(ctx, world, who="bola")  # 60, 70: change 10, at or above 10: met
    text = (
        "The English scores changed by 10 points across 2 term scores "
        + " ".join(f"[{r}]" for r in result["record_ids"])
        + f" [MP-1] [TR-1] [{result['measure_id']}]."
    )
    drafted = tool("draft_summary")(
        ctx, learner_id=learner, text=text, finding_hashes=[result["result_hash"]]
    )
    assert tool("check_citations")(ctx, report_id=drafted["report_id"])["status"] == "ok"
    released = tool("release_report")(ctx, report_id=drafted["report_id"])
    assert released["status"] == "released"
    before = ctx.conn.execute(
        "SELECT body, findings, template_version FROM reports WHERE report_id = %s",
        (drafted["report_id"],),
    ).fetchone()
    second = supersede(ctx, result["measure_id"])
    assert second["version"] == 2
    after = ctx.conn.execute(
        "SELECT body, findings, template_version FROM reports WHERE report_id = %s",
        (drafted["report_id"],),
    ).fetchone()
    assert after == before, "the released report is exactly as it was"
    assert before[1][0]["measure_version"] == 1
    assert before[1][0]["result"]["fields"]["threshold_met"]["exact"] == "1"
    # A new question gets the new version (10 is not MORE than 10), and the old report is untouched.
    newer = compute(ctx, world, who="bola")
    assert newer["measure_version"] == 2
    assert newer["result"]["fields"]["threshold_met"]["exact"] == "0"
    assert newer["result_hash"] != result["result_hash"]


# ----------------------------------------------------------------------- make_chart
@pytest.mark.integration
def test_a_chart_is_drawn_from_a_measure_result_the_run_returned(world, make_ctx, runner):
    ctx = make_ctx()
    got = compute(ctx, world)
    chart = tool("make_chart")(
        ctx, learner_id=world["learners"]["amina"], measure_hashes=[got["result_hash"]],
        measure_field="change",
    )  # fmt: skip
    assert chart["status"] == "ok" and chart["scale_max"] == "100.00"
    assert [(s["name"], s["kind"], s["values"]) for s in chart["series"]] == [
        (f"{NAME}: change", "measure", ["11.00"])
    ], "the measure returns four numbers: the one drawn is named, and the line says which"
    assert chart["series"][0]["record_ids"] == [got["record_ids"]]
    assert chart["record_ids"] == sorted(got["record_ids"])
    assert chart["clause_ids"] == ["MP-1", "TR-1"]
    assert chart["measures"] == [
        {"measure_id": got["measure_id"], "version": 1, "code_sha256": hash_text(SOURCE),
         "result_hash": got["result_hash"]}
    ]  # fmt: skip
    assert chart["template_version"] == "baobab-mp" and chart["excluded"] == []
    assert re.fullmatch(r"[0-9a-f]{64}", chart["result_hash"])
    assert [r["id"] for r in chart["rules"]] == ["MP-1", "TR-1"]


@pytest.mark.integration
def test_a_result_with_several_numbers_needs_the_one_to_draw_named_and_it_must_be_one_it_has(
    world, make_ctx, runner
):
    ctx = make_ctx()
    learner = world["learners"]["amina"]
    got = compute(ctx, world)
    hashes = [got["result_hash"]]
    unnamed = tool("make_chart")(ctx, learner_id=learner, measure_hashes=hashes)
    assert unnamed["status"] == "refused"
    assert "change, enough_scores, scores_used, threshold_met" in unnamed["reason"]
    wrong = tool("make_chart")(
        ctx, learner_id=learner, measure_hashes=hashes, measure_field="value"
    )
    assert wrong["status"] == "refused"
    count = tool("make_chart")(
        ctx, learner_id=learner, measure_hashes=hashes, measure_field="scores_used"
    )
    assert [s["values"] for s in count["series"]] == [["3.00"]]


@pytest.mark.integration
def test_a_chart_takes_no_number_only_hashes_of_results_that_are_in_the_log(
    world, make_ctx, runner
):
    ctx = make_ctx()
    learner = world["learners"]["amina"]
    got = compute(ctx, world)
    made_up = "f" * 64
    refused = tool("make_chart")(ctx, learner_id=learner, measure_hashes=[made_up])
    assert refused["status"] == "refused"
    assert "not returned by a tool in this run" in refused["reason"]
    # the hash of a different kind of result (a listing) is not a measure result either
    listing = tool("list_measures")(ctx)
    refused = tool("make_chart")(ctx, learner_id=learner, measure_hashes=[listing["result_hash"]])
    assert refused["status"] == "refused"
    other_learner = tool("make_chart")(
        ctx, learner_id=world["learners"]["bola"], measure_hashes=[got["result_hash"]]
    )
    assert other_learner["status"] == "refused", "one learner's result is not drawn for another"


@pytest.mark.integration
def test_a_measure_that_was_not_approved_cannot_be_charted(world, make_ctx, no_runner):
    ctx = make_ctx()
    refused = compute(ctx, world, measure="demo")  # a refusal, so it has no result to chart
    assert refused["reason"] == "no_tool_applies" and "result_hash" not in refused
    chart = tool("make_chart")(
        ctx, learner_id=world["learners"]["amina"], measure_hashes=[hash_text("MS-demo")]
    )
    assert chart["status"] == "refused"


@pytest.mark.integration
def test_a_result_is_not_drawn_if_its_measure_has_stopped_being_in_force_since(
    world, make_ctx, runner
):
    ctx = make_ctx()
    got = compute(ctx, world)
    supersede(ctx, got["measure_id"])
    chart = tool("make_chart")(
        ctx, learner_id=world["learners"]["amina"], measure_hashes=[got["result_hash"]]
    )
    assert chart["status"] == "refused" and "no longer in force" in chart["reason"]


@pytest.mark.integration
def test_subjects_and_measures_are_not_mixed_and_one_of_them_is_needed(world, make_ctx, runner):
    ctx = make_ctx()
    learner = world["learners"]["amina"]
    got = compute(ctx, world)
    both = tool("make_chart")(
        ctx, learner_id=learner, skills=["English"], measure_hashes=[got["result_hash"]]
    )
    assert both["status"] == "refused"
    neither = tool("make_chart")(ctx, learner_id=learner)
    assert neither["status"] == "refused"


@pytest.mark.integration
def test_the_old_subject_chart_still_works_the_same_way(world, make_ctx):
    ctx = make_ctx()
    chart = tool("make_chart")(ctx, learner_id=world["learners"]["amina"], skills=["English"])
    assert chart["status"] == "ok"
    assert [(s["name"], s["kind"], s["values"]) for s in chart["series"]] == [
        ("English", "subject", ["52.00", "58.00", "63.00"])
    ]
    assert "measures" not in chart


@pytest.mark.integration
def test_no_other_chart_tool_exists():
    names = [n for n in importlib.import_module("mdeis.tools").__all__ if "chart" in n]
    assert names == ["make_chart"]

