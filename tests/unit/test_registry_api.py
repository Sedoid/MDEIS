"""The registry functions (src/mdeis/registry). The first group needs no database; the second is
integration (DATABASE_URL), in a throwaway schema.

What these tests hold the code to: every function takes the connection and the signed-in context and
nothing that names a person; only the teacher's decision can set `approved`; the automated review
can only record pass or hold; a refusal leaves the version as it was; and every status move is a
row in the action log with the signed-in person, or none for a system step.
"""

import ast
import inspect
import json
from pathlib import Path

import psycopg
import pytest
import yaml
from dbtools import make_document, run_migrate
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from mdeis import registry
from mdeis.registry import store
from mdeis.tools.context import Approver, ToolContext

ROOT = Path(__file__).resolve().parents[2]
FUNCTIONS = ["create_version", "advance_status", "record_tests", "record_review", "clear_hold",
             "record_teacher_decision", "retire"]
FORBIDDEN_IN_A_TOOL_NAME = (
    "approve", "reject", "register", "registry", "review", "retire", "teacher", "hold",
)
NAMING_A_PERSON = {
    "approver", "approver_id", "approver_name", "user", "user_id", "user_name", "teacher",
    "teacher_id", "teacher_name", "reviewer", "reviewer_id", "reviewer_name", "developer",
    "dev_reviewer", "person", "person_name", "signed_in", "who", "decided_by", "cleared_by",
}


# --------------------------------------------------------------------------- no database needed
def test_the_registry_offers_exactly_these_functions():
    assert sorted(registry.__all__) == sorted(FUNCTIONS)
    for name in FUNCTIONS:
        assert callable(getattr(registry, name))


@pytest.mark.parametrize("name", FUNCTIONS)
def test_every_function_takes_the_context_first_and_no_argument_that_names_a_person(name):
    parameters = list(inspect.signature(getattr(registry, name)).parameters)
    assert parameters[0] == "ctx"
    assert not NAMING_A_PERSON & set(parameters), parameters


def functions_in_store():
    tree = ast.parse(inspect.getsource(store))
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def strings_in(function):
    """Every string constant in a function body, without its docstring."""
    body = function.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return [
        n.value for stmt in body for n in ast.walk(stmt)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def test_only_the_teachers_decision_mentions_approval_in_code():
    mentions = {
        name for name, fn in functions_in_store().items()
        if any("approved" in s for s in strings_in(fn))
    }
    assert mentions == {"record_teacher_decision"}


def test_the_automated_review_never_writes_a_status():
    fn = functions_in_store()["record_review"]
    assert not [s for s in strings_in(fn) if "status" in s.lower() and "SET" in s]
    assert not [s for s in strings_in(fn) if "approved" in s]


def test_no_function_takes_a_number_or_a_verdict_from_the_model_except_the_recorded_results():
    # A test result, a review result and a decision are the only verdicts, and each is checked
    # against a fixed list before it is written.
    source = inspect.getsource(store)
    for fixed in ("REVIEW_RESULTS", "DECISIONS", "RETIRE_REASONS", "TEST_SOURCES"):
        assert fixed in source, fixed


def test_a_registry_function_is_not_an_mcp_tool_and_the_model_cannot_reach_it():
    server = (ROOT / "src" / "mdeis" / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(server)
    tool_names = [
        n.name for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
        and any("tool" in ast.unparse(d) for d in n.decorator_list)
    ]
    assert tool_names, "the server lists tools"
    for name in tool_names:
        for word in FORBIDDEN_IN_A_TOOL_NAME:
            assert word not in name, (name, word)
    assert "registry" not in server
    for place in ("orchestrator", "gateway", "portal"):
        for path in (ROOT / "src" / place).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "mdeis.registry" not in text and "from mdeis import registry" not in text, path


def test_the_tool_wrapper_logs_each_function_under_its_own_name():
    for name in FUNCTIONS:
        assert getattr(registry, name).__wrapped__.__name__ == name
    source = inspect.getsource(store)
    for name in FUNCTIONS:
        assert f'logged_tool("registry_{name}")' in source, name


# --------------------------------------------------------------------------- with a database
TEACHER = Approver("u-teacher", "Ms Teacher")
DEVELOPER = Approver("u-dev", "Dev One")
STRANGER = Approver("u-nobody", "Someone")


@pytest.fixture
def db(scratch_schema):
    schema, url = scratch_schema
    built = run_migrate(url)
    assert built.returncode == 0, built.stderr
    with psycopg.connect(url) as conn:
        conn.schema, conn.url = schema, url
        content = yaml.safe_load((ROOT / "templates" / "v1.yaml").read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO templates (version_name, file_name, sha256, template_id, content, active) "
            "VALUES ('t-v1', 'v1.yaml', repeat('a', 64), 'tid', %s, false)",
            (Jsonb(content, dumps=lambda o: json.dumps(o, default=str)),),
        )
        conn.execute(
            "INSERT INTO registry_reviewers (user_id, role, name, granted_by) VALUES "
            "('u-teacher', 'teacher', 'Ms Teacher', 'admin'), "
            "('u-dev', 'developer', 'Dev One', 'admin')"
        )
        yield conn
        conn.rollback()


def ctx(db, who=None):
    return ToolContext(conn=db, run_id="RUN-T", approver=who, database_url=db.url, commit=False)


_count = iter(range(1, 10_000))


def source(extra=""):
    n = next(_count)
    return (
        f'OUTPUT_KIND = "number"\n# build {n}\n\n\ndef measure(records, params):\n'
        f'    return {{"value": "n"}}{extra}\n'
    )


def document(db):
    return make_document(db, kind="methodology", status="done", sha256=f"{next(_count):064x}")


def case(name, kind="document_example"):
    test = {"name": name, "source": kind, "records": [], "expected": {"value": "n"}}
    if kind == "document_example":
        test["quote"] = "a worked example in the methodology"
    return test


def make(db, **over):
    args = {
        "measure_name": "A measure", "method_ref": "5.3", "description": "what it shows",
        "input_scope": "learner", "source_code": source(), "output_kind": "number",
        "output_schema": {"kind": "number"}, "methodology_document_id": document(db),
        "section_sha256": "a" * 64, "clause_ids": ["SC-3"], "template_version": "t-v1",
        "tests": [case("c1", "build"), case("c2")],
        **over,
    }
    return registry.create_version(ctx(db), **args)


def ok(result):
    assert result["status"] == "ok", result
    return result


def review(db, made, result, reasons=None, who=None):
    return registry.record_review(ctx(db, who), **ref(made), result=result, reasons=reasons)


def record(db, made, results, new=None, who=None):
    return registry.record_tests(ctx(db, who), **ref(made), results=results, new_tests=new)


def passing(*names):
    return [{"name": n, "passed": True} for n in names]


def clear(db, made, who, note):
    return registry.clear_hold(ctx(db, who), **ref(made), note=note)


def ref(made):
    return {"measure_id": made["measure_id"], "version": made["version"]}


def row(db, made):
    return db.cursor(row_factory=dict_row).execute(
        "SELECT * FROM measure_versions WHERE measure_id = %s AND version = %s",
        (made["measure_id"], made["version"]),
    ).fetchone()


def results_for(db, made, failed=()):
    names = [t["name"] for t in row(db, made)["tests"]]
    return [{"name": n, "passed": n not in failed} for n in names]


def to_testing(db, made):
    ok(registry.advance_status(ctx(db), **ref(made), to="testing"))
    ok(registry.record_tests(ctx(db), **ref(made), results=results_for(db, made)))


def to_review(db, made):
    to_testing(db, made)
    ok(registry.advance_status(ctx(db), **ref(made), to="awaiting_review"))


def to_demo(db, made, review="pass"):
    to_review(db, made)
    reasons = ["the quote and the code agree"] if review == "pass" else ["a reason"]
    ok(registry.record_review(ctx(db), **ref(made), result=review, reasons=reasons))
    ok(registry.advance_status(ctx(db), **ref(made), to="awaiting_demo"))


def decide(db, made, who=TEACHER, decision="confirmed", note=None):
    return registry.record_teacher_decision(ctx(db, who), **ref(made), decision=decision, note=note)


def log(db):
    return db.cursor(row_factory=dict_row).execute(
        "SELECT tool, outcome, inputs, outputs, approver_id, approver_name "
        "FROM action_log ORDER BY event_id"
    ).fetchall()


# --------------------------------------------------------------------------- create
@pytest.mark.integration
def test_a_version_is_created_proposed_with_its_static_check_and_the_run_that_built_it(db):
    made = ok(make(db))
    assert made["measure_id"].startswith("MS-") and made["version"] == 1
    assert made["version_status"] == "proposed" and made["created"] is True
    got = row(db, made)
    assert got["status"] == "proposed" and got["built_by_run"] == "RUN-T"
    assert got["static_check"]["passed"] is True and got["static_check"]["findings"] == []
    assert got["static_check"]["rules_version"] == "sandbox-v1"
    assert [t["name"] for t in got["tests"]] == ["c1", "c2"]


@pytest.mark.integration
def test_a_second_version_of_a_measure_is_number_two_and_the_same_code_is_not_made_twice(db):
    first = ok(make(db))
    code = source()
    second = ok(make(db, measure_id=first["measure_id"], source_code=code))
    assert (second["measure_id"], second["version"]) == (first["measure_id"], 2)
    again = ok(make(db, measure_id=first["measure_id"], source_code=code,
                    methodology_document_id=row(db, second)["methodology_document_id"]))
    assert again["created"] is False and again["version"] == 2


@pytest.mark.integration
def test_the_same_method_ref_and_name_find_the_same_measure(db):
    first = ok(make(db))
    other = ok(make(db))
    assert other["measure_id"] == first["measure_id"] and other["version"] == 2


@pytest.mark.integration
def test_code_that_fails_the_static_check_is_kept_with_its_findings_and_can_only_be_rejected(db):
    # 10 is the pass mark in the template (SC-3): a school rule written into code.
    bad = (
        'OUTPUT_KIND = "number"\n\n\ndef measure(records, params):\n'
        '    return {"value": str(len(records) * 10)}\n'
    )
    made = ok(make(db, source_code=bad))
    check = row(db, made)["static_check"]
    assert check["passed"] is False
    codes = {f["code"] for f in check["findings"]}
    assert {"hardcoded_value", "duplicates_template_value"} <= codes
    dup = next(f for f in check["findings"] if f["code"] == "duplicates_template_value")
    assert "SC-3" in dup["clause_ids"]
    refused = registry.advance_status(ctx(db), **ref(made), to="testing")
    assert refused["status"] == "refused"
    assert row(db, made)["status"] == "proposed"
    ok(registry.advance_status(ctx(db), **ref(made), to="rejected"))


@pytest.mark.integration
@pytest.mark.parametrize(
    "over",
    [
        dict(template_version="no-such-template"),
        dict(output_kind="chart", output_schema={"kind": "chart"}),
        dict(output_schema={"kind": "table"}),
        dict(input_scope="class"),
        dict(clause_ids=[]),
        dict(description=""),
        dict(measure_name=""),
        dict(section_sha256="short"),
        dict(measure_id="MS-9999"),
        dict(tests=[case("a", "teacher")]),  # a teacher's case is added at the demo, by the teacher
        dict(tests=[{"name": "a", "source": "document_example"}]),  # no quote
        dict(tests=[case("a", "build"), case("a", "break_it")]),
        dict(tests=[{"name": "a", "source": "model"}]),
        dict(tests="not a list"),
    ],
)
def test_a_version_that_is_not_well_formed_is_refused_and_nothing_is_stored(db, over):
    result = make(db, **over)
    assert result["status"] == "refused", result
    assert db.execute("SELECT count(*) FROM measure_versions").fetchone()[0] == 0


@pytest.mark.integration
def test_the_signed_in_person_is_not_an_argument(db):
    with pytest.raises(TypeError):
        registry.advance_status(
            ctx(db), measure_id="MS-0001", version=1, to="testing", approver="x"
        )
    with pytest.raises(TypeError):
        registry.record_teacher_decision(
            ctx(db), measure_id="MS-0001", version=1, decision="confirmed", teacher_id="u-teacher"
        )


# --------------------------------------------------------------------------- the whole path
@pytest.mark.integration
def test_a_version_goes_from_proposed_to_approved_with_the_teachers_name_from_the_session(db):
    made = ok(make(db))
    to_demo(db, made)
    assert row(db, made)["status"] == "awaiting_demo"
    result = ok(decide(db, made))
    assert (result["from_status"], result["to_status"]) == ("awaiting_demo", "approved")
    got = row(db, made)
    assert got["status"] == "approved" and got["approved_at"] is not None
    assert (got["teacher_id"], got["teacher_name"], got["teacher_decision"]) == (
        "u-teacher", "Ms Teacher", "confirmed")


@pytest.mark.integration
def test_the_status_moves_one_step_at_a_time(db):
    made = ok(make(db))
    for skip in ("awaiting_review", "awaiting_demo", "proposed"):
        refused = registry.advance_status(ctx(db), **ref(made), to=skip)
        assert refused["status"] == "refused" and "cannot move" in refused["reason"], skip
    assert row(db, made)["status"] == "proposed"


@pytest.mark.integration
@pytest.mark.parametrize("target", ["approved", "retired", "proposed", "no_such_status"])
def test_advance_cannot_approve_retire_or_go_back(db, target):
    made = ok(make(db))
    to_demo(db, made)
    refused = registry.advance_status(ctx(db, TEACHER), **ref(made), to=target)
    assert refused["status"] == "refused"
    assert row(db, made)["status"] == "awaiting_demo"
    if target == "approved":
        assert "teacher" in refused["reason"].lower()


@pytest.mark.integration
def test_an_unknown_version_is_refused(db):
    for call in (
        lambda: registry.advance_status(ctx(db), measure_id="MS-0042", version=1, to="testing"),
        lambda: registry.record_review(ctx(db), measure_id="MS-0042", version=1, result="pass"),
        lambda: registry.retire(ctx(db), measure_id="MS-0042", version=1, reason="withdrawn"),
        lambda: decide(db, {"measure_id": "MS-0042", "version": 1}),
    ):
        assert call()["status"] == "refused"


# --------------------------------------------------------------------------- tests and results
@pytest.mark.integration
def test_recorded_results_are_counted_by_code_and_a_failure_blocks_the_move_to_review(db):
    made = ok(make(db))
    ok(registry.advance_status(ctx(db), **ref(made), to="testing"))
    recorded = ok(registry.record_tests(ctx(db), **ref(made),
                                        results=results_for(db, made, failed={"c1"})))
    assert recorded["tests_passed"] is False
    assert (recorded["ran"], recorded["failed"]) == (2, ["c1"])
    assert recorded["by_source"] == {"build": 1, "document_example": 1}
    refused = registry.advance_status(ctx(db), **ref(made), to="awaiting_review")
    assert refused["status"] == "refused"
    assert row(db, made)["status"] == "testing"
    again = ok(registry.record_tests(ctx(db), **ref(made), results=results_for(db, made)))
    assert again["tests_passed"] is True
    ok(registry.advance_status(ctx(db), **ref(made), to="awaiting_review"))


@pytest.mark.integration
def test_a_test_with_no_result_counts_as_not_passed(db):
    made = ok(make(db))
    ok(registry.advance_status(ctx(db), **ref(made), to="testing"))
    got = ok(registry.record_tests(ctx(db), **ref(made), results=[{"name": "c1", "passed": True}]))
    assert got["tests_passed"] is False and got["ran"] == 1 and got["failed"] == ["c2"]


@pytest.mark.integration
@pytest.mark.parametrize(
    "results",
    [
        [{"name": "no such test", "passed": True}],
        [{"name": "c1", "passed": True}, {"name": "c1", "passed": True}],
        [{"name": "c1", "passed": "yes"}],
        [{"name": "c1"}],
        "passed",
    ],
)
def test_results_that_do_not_match_the_recorded_tests_are_refused(db, results):
    made = ok(make(db))
    ok(registry.advance_status(ctx(db), **ref(made), to="testing"))
    assert registry.record_tests(ctx(db), **ref(made), results=results)["status"] == "refused"
    assert row(db, made)["test_result"] is None


@pytest.mark.integration
def test_results_are_only_taken_while_testing_or_at_the_demo(db):
    made = ok(make(db))
    assert record(db, made, results_for(db, made))["status"] == "refused"
    to_review(db, made)
    assert record(db, made, results_for(db, made))["status"] == "refused"


@pytest.mark.integration
def test_the_system_can_add_build_tests_while_testing_but_not_a_teachers_case(db):
    made = ok(make(db))
    ok(registry.advance_status(ctx(db), **ref(made), to="testing"))
    added = [case("c3", "break_it")]
    got = ok(record(db, made, passing("c1", "c2", "c3"), new=added))
    assert got["by_source"] == {"build": 1, "document_example": 1, "break_it": 1}
    teachers = [case("c4", "teacher")]
    assert record(db, made, passing("c1", "c2", "c3", "c4"), new=teachers)["status"] == "refused"
    assert [t["name"] for t in row(db, made)["tests"]] == ["c1", "c2", "c3"]


@pytest.mark.integration
def test_a_teacher_adds_a_case_at_the_demo_and_it_counts_as_independent(db):
    made = ok(make(db, tests=[case("c1", "build")]))  # only a model-written test so far
    to_demo(db, made)
    refused = decide(db, made)
    assert refused["status"] == "refused"
    assert row(db, made)["status"] == "awaiting_demo"
    names = ("c1", "t1")
    new = [case("t1", "teacher")]
    stranger = registry.record_tests(ctx(db, STRANGER), **ref(made), new_tests=new,
                                     results=[{"name": n, "passed": True} for n in names])
    assert stranger["status"] == "refused"
    system = registry.record_tests(ctx(db), **ref(made), new_tests=new,
                                   results=[{"name": n, "passed": True} for n in names])
    assert system["status"] == "refused"
    ok(registry.record_tests(ctx(db, TEACHER), **ref(made), new_tests=new,
                             results=[{"name": n, "passed": True} for n in names]))
    ok(decide(db, made))
    assert row(db, made)["status"] == "approved"


@pytest.mark.integration
def test_a_case_that_fails_at_the_demo_is_not_stored_as_passing(db):
    made = ok(make(db))
    to_demo(db, made)
    refused = registry.record_tests(
        ctx(db, TEACHER), **ref(made), new_tests=[case("t1", "teacher")],
        results=[{"name": "c1", "passed": True}, {"name": "c2", "passed": True},
                 {"name": "t1", "passed": False}],
    )
    assert refused["status"] == "refused"
    assert [t["name"] for t in row(db, made)["tests"]] == ["c1", "c2"]


# --------------------------------------------------------------------------- the automated review
@pytest.mark.integration
def test_the_automated_review_records_pass_or_hold_once_and_never_moves_the_status(db):
    made = ok(make(db))
    to_review(db, made)
    got = ok(review(db, made, "pass", ["matches the quote"]))
    assert got["review_result"] == "pass"
    after = row(db, made)
    assert after["status"] == "awaiting_review"
    assert after["review_result"] == "pass" and after["review_reasons"] == ["matches the quote"]
    assert review(db, made, "hold", ["late"])["status"] == "refused"
    assert row(db, made)["review_result"] == "pass"


@pytest.mark.integration
@pytest.mark.parametrize(
    "result, reasons",
    [
        ("approve", []), ("approved", ["x"]), ("hold", []), ("hold", None), ("hold", [""]),
        ("pass", "text"), ("", []),
    ],
)
def test_a_review_can_only_be_pass_or_hold_and_a_hold_gives_its_reasons(db, result, reasons):
    made = ok(make(db))
    to_review(db, made)
    assert review(db, made, result, reasons)["status"] == "refused"
    assert row(db, made)["review_result"] is None


@pytest.mark.integration
def test_a_review_is_a_system_step_so_a_signed_in_person_cannot_record_one(db):
    made = ok(make(db))
    to_review(db, made)
    for who in (TEACHER, DEVELOPER, STRANGER):
        refused = registry.record_review(ctx(db, who), **ref(made), result="pass", reasons=[])
        assert refused["status"] == "refused" and "automated" in refused["reason"]
    assert row(db, made)["review_result"] is None


@pytest.mark.integration
def test_a_review_is_only_recorded_while_the_version_waits_for_it(db):
    made = ok(make(db))
    assert review(db, made, "pass", [])["status"] == "refused"
    to_testing(db, made)
    assert review(db, made, "pass", [])["status"] == "refused"


@pytest.mark.integration
def test_a_held_version_waits_until_a_registered_developer_clears_the_hold(db):
    made = ok(make(db))
    to_review(db, made)
    ok(review(db, made, "hold", ["the code reads a field the quote does not name"]))
    blocked = registry.advance_status(ctx(db), **ref(made), to="awaiting_demo")
    assert blocked["status"] == "refused"
    assert row(db, made)["status"] == "awaiting_review"
    # nobody signed in, a teacher, and someone not in the registry cannot clear it
    for who in (None, TEACHER, STRANGER):
        refused = registry.clear_hold(ctx(db, who), **ref(made), note="read it")
        assert refused["status"] == "refused", who
    # and the automated step cannot turn the hold into a pass
    assert review(db, made, "pass", [])["status"] == "refused"
    cleared = ok(clear(db, made, DEVELOPER, "the code matches the quote"))
    assert cleared["cleared_by"] == "u-dev"
    got = row(db, made)
    assert (got["hold_cleared_by_id"], got["hold_cleared_name"]) == ("u-dev", "Dev One")
    ok(registry.advance_status(ctx(db), **ref(made), to="awaiting_demo"))


@pytest.mark.integration
def test_a_hold_needs_a_note_and_a_pass_has_no_hold_to_clear(db):
    made = ok(make(db))
    to_review(db, made)
    ok(registry.record_review(ctx(db), **ref(made), result="pass", reasons=[]))
    assert registry.clear_hold(ctx(db, DEVELOPER), **ref(made), note="x")["status"] == "refused"
    other = ok(make(db))
    to_review(db, other)
    ok(registry.record_review(ctx(db), **ref(other), result="hold", reasons=["r"]))
    assert registry.clear_hold(ctx(db, DEVELOPER), **ref(other), note="  ")["status"] == "refused"
    ok(registry.clear_hold(ctx(db, DEVELOPER), **ref(other), note="reviewed"))
    assert clear(db, other, DEVELOPER, "again")["status"] == "refused"


@pytest.mark.integration
def test_a_cleared_hold_still_needs_the_teachers_confirmation(db):
    made = ok(make(db))
    to_review(db, made)
    ok(registry.record_review(ctx(db), **ref(made), result="hold", reasons=["r"]))
    ok(registry.clear_hold(ctx(db, DEVELOPER), **ref(made), note="reviewed"))
    ok(registry.advance_status(ctx(db), **ref(made), to="awaiting_demo"))
    assert row(db, made)["status"] == "awaiting_demo"
    assert row(db, made)["teacher_id"] is None
    ok(decide(db, made))


# --------------------------------------------------------------------------- the teacher's decision
@pytest.mark.integration
def test_a_decision_needs_a_signed_in_person_who_is_a_registered_teacher(db):
    made = ok(make(db))
    to_demo(db, made)
    for who, word in ((None, "signed"), (STRANGER, "teacher"), (DEVELOPER, "teacher")):
        refused = decide(db, made, who=who)
        assert refused["status"] == "refused", who
        assert word in refused["reason"].lower(), (who, refused)
    got = row(db, made)
    assert got["status"] == "awaiting_demo" and got["teacher_id"] is None


@pytest.mark.integration
def test_a_decision_is_only_taken_at_the_demo(db):
    made = ok(make(db))
    assert decide(db, made)["status"] == "refused"
    to_review(db, made)
    assert decide(db, made)["status"] == "refused"


@pytest.mark.integration
def test_a_version_with_only_tests_the_model_wrote_is_refused_and_left_as_it_was(db):
    made = ok(make(db, tests=[case("c1", "build"), case("c2", "break_it")]))
    to_demo(db, made)
    refused = decide(db, made)
    assert refused["status"] == "refused"
    got = row(db, made)
    assert got["status"] == "awaiting_demo" and got["teacher_id"] is None
    assert got["approved_at"] is None


@pytest.mark.integration
def test_a_rejection_needs_a_note_and_closes_the_version(db):
    made = ok(make(db))
    to_demo(db, made)
    assert decide(db, made, decision="rejected")["status"] == "refused"
    assert decide(db, made, decision="rejected", note=" ")["status"] == "refused"
    assert decide(db, made, decision="maybe", note="x")["status"] == "refused"
    assert decide(db, made, decision="approved", note="x")["status"] == "refused"
    result = ok(decide(db, made, decision="rejected", note="it counts the wrong terms"))
    assert result["to_status"] == "rejected"
    got = row(db, made)
    assert got["status"] == "rejected" and got["teacher_decision"] == "rejected"
    assert got["teacher_note"] == "it counts the wrong terms" and got["approved_at"] is None


@pytest.mark.integration
def test_the_name_stored_is_the_registered_one_not_the_one_in_the_token(db):
    made = ok(make(db))
    to_demo(db, made)
    ok(decide(db, made, who=Approver("u-teacher", "A Different Display Name")))
    assert row(db, made)["teacher_name"] == "Ms Teacher"


@pytest.mark.integration
def test_confirming_a_newer_version_retires_the_one_in_force_in_the_same_step(db):
    first = ok(make(db))
    to_demo(db, first)
    ok(decide(db, first))
    second = ok(make(db))
    assert second["measure_id"] == first["measure_id"]
    to_demo(db, second)
    ok(decide(db, second))
    old, new = row(db, first), row(db, second)
    assert (old["status"], old["retired_reason"]) == ("retired", "superseded")
    assert new["status"] == "approved"


@pytest.mark.integration
def test_a_refused_confirmation_does_not_retire_the_version_in_force(db):
    first = ok(make(db))
    to_demo(db, first)
    ok(decide(db, first))
    weak = ok(make(db, tests=[case("c1", "build")]))
    to_demo(db, weak)
    assert decide(db, weak)["status"] == "refused"
    assert row(db, first)["status"] == "approved"


# --------------------------------------------------------------------------- retire
@pytest.mark.integration
def test_an_approved_version_can_be_retired_with_a_reason_from_the_list(db):
    made = ok(make(db))
    to_demo(db, made)
    ok(decide(db, made))
    for bad in ("", "because", "superseded "):
        assert registry.retire(ctx(db), **ref(made), reason=bad)["status"] == "refused"
    assert row(db, made)["status"] == "approved"
    got = ok(registry.retire(ctx(db, TEACHER), **ref(made), reason="section_removed"))
    assert (got["from_status"], got["to_status"]) == ("approved", "retired")
    assert row(db, made)["retired_reason"] == "section_removed"
    assert registry.retire(ctx(db), **ref(made), reason="withdrawn")["status"] == "refused"


@pytest.mark.integration
def test_a_version_that_was_never_approved_can_be_retired_too(db):
    made = ok(make(db))
    to_review(db, made)
    ok(registry.retire(ctx(db), **ref(made), reason="withdrawn"))
    assert row(db, made)["status"] == "retired"


# --------------------------------------------------------------------------- the action log
@pytest.mark.integration
def test_every_status_move_is_a_row_in_the_action_log_with_who_moved_it(db):
    made = ok(make(db))
    to_demo(db, made)
    ok(decide(db, made))
    rows = log(db)
    tools = [r["tool"] for r in rows]
    assert tools[0] == "registry_create_version"
    moves = [r for r in rows if r["tool"] == "registry_advance_status"]
    assert [(m["outputs"]["from_status"], m["outputs"]["to_status"]) for m in moves] == [
        ("proposed", "testing"), ("testing", "awaiting_review"),
        ("awaiting_review", "awaiting_demo"),
    ]
    assert all(m["approver_id"] is None and m["outcome"] == "ok" for m in moves)
    assert all(m["inputs"]["measure_id"] == made["measure_id"] for m in moves)
    assert all(m["inputs"]["version"] == 1 for m in moves)
    final = rows[-1]
    assert final["tool"] == "registry_record_teacher_decision" and final["outcome"] == "ok"
    assert (final["approver_id"], final["approver_name"]) == ("u-teacher", "Ms Teacher")
    moved = (final["outputs"]["from_status"], final["outputs"]["to_status"])
    assert moved == ("awaiting_demo", "approved")
    review = next(r for r in rows if r["tool"] == "registry_record_review")
    assert review["approver_id"] is None and review["outputs"]["review_result"] == "pass"


@pytest.mark.integration
def test_a_refused_move_is_in_the_log_too_and_the_version_is_unchanged(db):
    made = ok(make(db))
    registry.advance_status(ctx(db), **ref(made), to="awaiting_demo")
    last = log(db)[-1]
    assert last["tool"] == "registry_advance_status" and last["outcome"] == "refused"
    assert "cannot move" in last["outputs"]["reason"]
    assert row(db, made)["status"] == "proposed"


@pytest.mark.integration
def test_a_refused_decision_is_logged_with_the_person_who_tried(db):
    made = ok(make(db))
    to_demo(db, made)
    decide(db, made, who=STRANGER)
    last = log(db)[-1]
    assert last["tool"] == "registry_record_teacher_decision" and last["outcome"] == "refused"
    assert last["approver_id"] == "u-nobody"


@pytest.mark.integration
def test_the_log_holds_no_label_or_ranking_of_a_learner(db):
    made = ok(make(db))
    to_demo(db, made)
    ok(decide(db, made))
    text = json.dumps(log(db), default=str).lower()
    for word in ("weak", "remedial", "gifted", "rank", "recommend"):
        assert word not in text, word
