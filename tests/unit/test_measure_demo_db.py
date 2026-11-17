"""The teacher demo against Postgres (T-073), on a scratch schema, through the real registry.

A version is made at `awaiting_demo` by the registry's own functions (tests/unit/demokit.py), then
walked the way a teacher walks it: open the demo, try a few synthetic scores, save a case,
confirm or reject. The sandbox is the real child called in this process
(tests/unit/inprocess_runner.py): the real static check, the real restricted namespace, the real
exact-text conversion, no socket.

The expected numbers are worked by hand from the Baobab methodology (see demokit.py), never copied
from what the system printed. The values the function reads are `MP-1` = 10 (the threshold, from the
proposed patch), `TR-1.window_terms` = 3 and `TR-1.min_scores` = 2 (the Baobab rulebook).
"""

import inspect
import itertools
import os

import pytest

pytest.importorskip("psycopg")

import demokit  # noqa: E402
import psycopg  # noqa: E402
from demokit import (  # noqa: E402
    DEVELOPER,
    REVOKED,
    SECOND,
    STRANGER,
    TEACHER,
    at_demo,
    build_plan_row,
    ctx_for,
    log_rows,
    status_of,
)
from inprocess_runner import Calls  # noqa: E402
from measurekit import SOURCE  # noqa: E402

from mdeis.measure_demo import api  # noqa: E402
from mdeis.sandbox import client  # noqa: E402
from mdeis.sandbox.protocol import UNAVAILABLE  # noqa: E402

pytestmark = pytest.mark.integration
NAMES = itertools.count(1)
VALUES_USED = {"MP-1": "10", "TR-1.min_scores": "2", "TR-1.window_terms": "3"}
FORM_1 = {"stage": "Form 1"}


def fresh(**more) -> str:
    return f"Demo measure {next(NAMES)} {more.get('tag', '')}".strip()


# =============================================================================== set-up
@pytest.fixture(scope="module")
def world(tmp_path_factory):
    base = os.environ.get("DATABASE_URL")
    if not base:
        pytest.skip("DATABASE_URL is not set: integration tests skipped")
    name, url = demokit.setup(base, tmp_path_factory.mktemp("variants"))
    try:
        yield url
    finally:
        demokit.drop_schema(base, name)


@pytest.fixture
def runner(monkeypatch):
    spy = Calls()
    monkeypatch.setattr(client, "run", spy)
    return spy


def entries(subject, *scores):
    return [{"subject": subject, "term": i, "score": s} for i, s in enumerate(scores, start=1)]


def numbers(answer: dict) -> dict:
    """The exact form of each number of an answer."""
    return {field: shown["exact"] for field, shown in answer.items()}


def demo_version(url, **more):
    return at_demo(url, name=fresh(), **more)


# =============================================================================== who may act
FUNCTIONS = ["list_awaiting", "open_demo", "try_measure", "save_case", "confirm", "reject"]
NAMING_A_PERSON = {
    "approver", "approver_id", "approver_name", "user", "user_id", "user_name", "teacher",
    "teacher_id", "teacher_name", "person", "who", "decided_by", "school", "school_code",
    "learner", "learner_id", "name", "decision", "status",
}


@pytest.mark.parametrize("name", FUNCTIONS)
def test_no_function_takes_an_argument_that_names_a_person_a_school_a_learner_or_a_decision(name):
    parameters = list(inspect.signature(getattr(api, name)).parameters)
    assert parameters[0] == "ctx"
    assert not NAMING_A_PERSON & set(parameters), parameters


def call(url, who, name, school="BAO", **arguments):
    return getattr(api, name)(ctx_for(url, who, school), **arguments)


def arguments_for(name, which):
    given = {"measure_id": which["measure_id"], "version": which["version"]}
    if name == "list_awaiting":
        return {}
    if name == "open_demo":
        return given
    if name == "try_measure":
        return {**given, **FORM_1, "entries": entries("English", "52", "58", "63")}
    if name == "save_case":
        return {**given, **FORM_1, "entries": entries("English", "52", "58", "63"),
                "expected": {"change": "11"}}
    if name == "confirm":
        return {**given, "note": None}
    return {**given, "note": "no"}


@pytest.mark.parametrize("name", FUNCTIONS)
@pytest.mark.parametrize("who,school,words", [
    (None, "BAO", "no one is signed in"),
    (STRANGER, "BAO", "not recorded as a teacher"),
    (REVOKED, "BAO", "not recorded as a teacher"),
    (DEVELOPER, "BAO", "not recorded as a teacher"),
    (TEACHER, None, "no school is attached"),
    (TEACHER, "", "no school is attached"),
], ids=["unsigned", "stranger", "revoked teacher", "developer only", "no school", "blank school"])
def test_every_function_refuses_unsigned_calls_strangers_revoked_teachers_and_no_school(
    world, runner, name, who, school, words
):
    which = demo_version(world)
    result = call(world, who, name, school, **arguments_for(name, which))
    assert result["status"] == "refused" and words in result["reason"]
    assert status_of(world, which)["status"] == "awaiting_demo"
    assert status_of(world, which)["decision"] is None
    assert len(runner) == 0, "nothing reaches the sandbox for a person who may not act"


# =============================================================================== the list
def test_the_list_holds_only_versions_waiting_at_the_demo_and_says_how_many(world):
    waiting = demo_version(world)
    held = demo_version(world, review="hold")  # stays at awaiting_review
    approved = demo_version(world, stop_after="approved")
    got = call(world, TEACHER, "list_awaiting")
    assert got["status"] == "ok"
    ids = {(m["measure_id"], m["version"]) for m in got["measures"]}
    assert (waiting["measure_id"], waiting["version"]) in ids
    assert (held["measure_id"], held["version"]) not in ids
    assert (approved["measure_id"], approved["version"]) not in ids
    assert got["waiting"] == len(got["measures"]) >= 1
    mine = next(m for m in got["measures"] if m["measure_id"] == waiting["measure_id"])
    assert set(mine) == {"measure_id", "version", "name", "method_ref", "description"}
    assert "source_code" not in str(got)


def test_a_list_is_one_diary_row_with_the_signed_in_teacher(world):
    before = len(log_rows(world, "measure_demo_list"))
    call(world, TEACHER, "list_awaiting")
    rows = log_rows(world, "measure_demo_list")
    assert len(rows) == before + 1
    assert rows[-1][3] == "ok" and rows[-1][4:] == ("u-teacher", "Ms Teacher")


# =============================================================================== opening the demo
def test_the_demo_shows_the_quote_the_clauses_the_checks_and_the_worked_example_run_for_real(
    world, runner
):
    which = demo_version(world)
    got = call(world, TEACHER, "open_demo", **{k: which[k] for k in ("measure_id", "version")})
    assert got["status"] == "ok"
    assert got["name"].startswith("Demo measure") and got["description"]
    assert got["version_status"] == "awaiting_demo" and got["method_ref"] == "6.4"
    (quote,) = got["origin"]["quotes"]
    assert quote["quote"] == demokit.RULE_QUOTE and quote["section_id"] == "6.4"
    assert (quote["title"], quote["page"]) == ("Moved", 3)
    assert quote["section_sha256"] == got["origin"]["section_sha256"]
    assert got["origin"]["methodology_document_id"] == which["document_id"]
    clauses = {c["clause_id"]: c for c in got["clauses"]}
    assert set(clauses) == {"MP-1", "TR-1"}
    assert clauses["MP-1"]["state"] == "placeholder" and clauses["MP-1"]["value"] == "10"
    assert clauses["MP-1"]["confirmed_by"] is None and clauses["MP-1"]["unit"] == "points"
    assert {r["key"]: r["value"] for r in clauses["TR-1"]["reads"]} == {
        "TR-1.min_scores": "2", "TR-1.window_terms": "3"}
    assert clauses["TR-1"]["kind"] == "rulebook"
    assert got["patch"] == {"patch_id": which["patch_id"], "status": "proposed",
                            "applied_as": None}
    assert got["review"] == {"result": "pass", "reasons": ["agrees with the quoted section"],
                             "cleared_by": None}
    assert got["checks"]["static"]["passed"] is True
    assert got["checks"]["tests"]["passed"] is True and got["checks"]["tests"]["ran"] == 2
    assert got["checks"]["tests"]["by_source"] == {"build": 1, "document_example": 1}
    (example,) = got["examples"]
    assert example["inputs"] == ["52", "58", "63"] and example["quote"] == demokit.QUOTE
    assert numbers(example["returned"]) == {
        "change": "11", "enough_scores": "1", "scores_used": "3", "threshold_met": "1"}
    assert numbers(example["expected"]) == numbers(example["returned"])
    assert example["match"] is True
    assert got["approval"] == {"can_confirm": True, "gaps": []}
    assert got["try"]["stages"] == ["Form 1", "Form 2", "Form 3", "Form 4"]
    assert sorted(got["try"]["subjects"]) == ["English", "Humanities", "Mathematics", "Science"]
    assert got["try"]["scale_max"] == "100" and got["try"]["scope"] == "learner_skill"
    # the numbers came from the sandbox, with the values built from the template and the patch
    (sent,) = runner
    assert sent["params"] == VALUES_USED
    assert sent["records"] == [{"score": "52"}, {"score": "58"}, {"score": "63"}]
    assert sent["source"] == SOURCE


def test_opening_the_demo_changes_nothing_and_is_one_diary_row_with_the_teacher(world, runner):
    which = demo_version(world)
    before = status_of(world, which)
    rows = len(log_rows(world, "measure_demo_open"))
    call(world, TEACHER, "open_demo", **{k: which[k] for k in ("measure_id", "version")})
    assert status_of(world, which) == before
    new = log_rows(world, "measure_demo_open")[rows:]
    assert len(new) == 1 and new[0][3] == "ok" and new[0][4:] == ("u-teacher", "Ms Teacher")
    assert "source_code" not in str(new[0][2]) and SOURCE not in str(new[0][2])


def test_the_skipped_sections_and_the_recorded_build_it_anyway_are_shown(world, runner):
    which = demo_version(world)
    skipped = [{"section_id": "6.2", "title": "Pass mark", "change": "added",
                "clause_ids": ["SC-3"], "tool": "compute_trend", "reason": "SC-3 already",
                "build_anyway": False}]
    build_plan_row(world, which["document_id"], skipped, ["6.4"])
    got = call(world, TEACHER, "open_demo", **{k: which[k] for k in ("measure_id", "version")})
    assert [s["section_id"] for s in got["skipped"]] == ["6.2"]
    assert got["skipped"][0]["clause_ids"] == ["SC-3"]
    assert got["build_anyway"] == ["6.4"]


def test_with_no_build_plan_on_record_the_lists_are_empty_not_invented(world, runner):
    which = demo_version(world)
    got = call(world, TEACHER, "open_demo", **{k: which[k] for k in ("measure_id", "version")})
    assert got["skipped"] == [] and got["build_anyway"] == []


@pytest.mark.parametrize("make,words", [
    (lambda url: demo_version(url, review="hold"), "not_at_demo"),
    (lambda url: demo_version(url, stop_after="approved"), "not_at_demo"),
])
def test_a_version_that_is_not_waiting_at_the_demo_has_no_demo(world, runner, make, words):
    which = make(world)
    got = call(world, TEACHER, "open_demo", **{k: which[k] for k in ("measure_id", "version")})
    assert got["status"] == "refused" and words in got["reason"]
    assert len(runner) == 0


def test_an_unknown_measure_or_version_is_a_plain_refusal(world, runner):
    got = call(world, TEACHER, "open_demo", measure_id="MS-9999", version=1)
    assert got["status"] == "refused" and "there is no version" in got["reason"]
    which = demo_version(world)
    got = call(world, TEACHER, "open_demo", measure_id=which["measure_id"], version=7)
    assert got["status"] == "refused" and "there is no version" in got["reason"]


def test_a_hold_stays_a_hold_until_a_developer_clears_it_and_then_the_demo_says_who(world, runner):
    held = demo_version(world, review="hold", reasons=("the quote does not say 10",))
    assert status_of(world, held)["status"] == "awaiting_review"
    refused = call(world, TEACHER, "confirm", **arguments_for("confirm", held))
    assert refused["status"] == "refused"
    assert status_of(world, held)["status"] == "awaiting_review"
    assert status_of(world, held)["decision"] is None
    cleared = demo_version(world, review="hold", clear_hold=True)
    got = call(world, TEACHER, "open_demo", **{k: cleared[k] for k in ("measure_id", "version")})
    assert got["review"]["result"] == "hold" and got["review"]["cleared_by"] == "Dev One"
    assert got["review"]["reasons"] == ["agrees with the quoted section"]


# =============================================================================== try your own
CASES = [
    # (subject, scores, hand answer: change, enough_scores, scores_used, threshold_met)
    ("English", ("52", "58", "63"), {"change": "11", "enough_scores": "1", "scores_used": "3",
                                      "threshold_met": "1"}),
    ("English", ("60", "70"), {"change": "10", "enough_scores": "1", "scores_used": "2",
                                "threshold_met": "1"}),
    ("Mathematics", ("40", "62", "66", "64"), {"change": "2", "enough_scores": "1",
                                               "scores_used": "3", "threshold_met": "0"}),
    ("Science", ("64",), {"enough_scores": "0", "scores_used": "1"}),
    ("English", ("52.5", "58", "63.5"), {"change": "11", "enough_scores": "1",
                                          "scores_used": "3", "threshold_met": "1"}),
]


IDS = ["B-worked", "B-06", "B-08", "B-07", "decimals"]


@pytest.mark.parametrize("subject,scores,hand", CASES, ids=IDS)
def test_a_try_gives_the_exact_answer_the_clauses_and_the_values_used(
    world, runner, subject, scores, hand
):
    which = demo_version(world)
    got = call(world, TEACHER, "try_measure", measure_id=which["measure_id"],
               version=which["version"], **FORM_1, entries=entries(subject, *scores))
    assert got["status"] == "ok"
    assert numbers(got["answer"]) == hand
    assert got["values_used"] == VALUES_USED
    states = {c["clause_id"]: c["state"] for c in got["clauses"]}
    assert states == {"MP-1": "placeholder", "TR-1": "in_rulebook"}
    assert got["measure_id"] == which["measure_id"] and got["version"] == which["version"]
    assert got["notice"] and "synthetic" in got["notice"].lower()
    (sent,) = runner
    assert [r["subject"] for r in sent["records"]] == [subject] * len(scores)
    wanted_ids = [f"TRY-{i}" for i in range(1, len(scores) + 1)]
    assert [r["record_id"] for r in sent["records"]] == wanted_ids
    assert all(r["stage"] == "Form 1" for r in sent["records"])
    assert sent["params"] == VALUES_USED


def test_decimal_scores_reach_the_function_as_exact_fractions(world, runner):
    which = demo_version(world)
    call(world, TEACHER, "try_measure", measure_id=which["measure_id"], version=which["version"],
         **FORM_1, entries=entries("English", "52.5", "58", "63.5"))
    assert [r["score"] for r in runner[0]["records"]] == ["105/2", "58", "127/2"]


def test_the_same_try_gives_the_same_answer_and_the_same_hash(world, runner):
    which = demo_version(world)
    args = dict(measure_id=which["measure_id"], version=which["version"], **FORM_1,
                entries=entries("English", "52", "58", "63"))
    first = call(world, TEACHER, "try_measure", **args)
    second = call(world, SECOND, "try_measure", **args)
    assert first["answer"] == second["answer"]
    assert first["result_hash"] == second["result_hash"]


def test_a_try_is_a_diary_row_with_the_scores_the_answer_and_the_signed_in_teacher(world, runner):
    which = demo_version(world)
    before = len(log_rows(world, "measure_demo_try"))
    call(world, SECOND, "try_measure", measure_id=which["measure_id"], version=which["version"],
         **FORM_1, entries=entries("English", "60", "70"))
    new = log_rows(world, "measure_demo_try")[before:]
    assert len(new) == 1
    tool, inputs, outputs, outcome, who_id, who_name = new[0]
    assert (outcome, who_id, who_name) == ("ok", "u-second", "Mr Second")
    assert inputs["entries"] == entries("English", "60", "70") and inputs["stage"] == "Form 1"
    assert numbers(outputs["answer"])["change"] == "10"
    assert not {"approver", "learner_id", "name", "school"} & set(inputs)


def test_a_try_changes_nothing_in_the_registry(world, runner):
    which = demo_version(world)
    before = status_of(world, which)
    call(world, TEACHER, "try_measure", measure_id=which["measure_id"], version=which["version"],
         **FORM_1, entries=entries("English", "60", "70"))
    assert status_of(world, which) == before


@pytest.mark.parametrize("change,code", [
    ({"stage": "Form 9"}, "unknown_stage"),
    ({"entries": entries("English", "101")}, "score_off_scale"),
    ({"entries": entries("English", "abc")}, "bad_score"),
    ({"entries": entries("Art", "50")}, "unknown_subject"),
    ({"entries": [{"subject": "English", "term": 1, "score": "5", "learner_id": "L-1"}]},
     "bad_entry"),
    ({"entries": entries("English", "50") + entries("English", "60")}, "duplicate_entry"),
    ({"entries": [{"subject": "English", "term": 1, "score": "50"},
                  {"subject": "Science", "term": 2, "score": "50"}]}, "one_subject_needed"),
    ({"entries": []}, "no_scores"),
])
def test_a_try_that_is_not_valid_is_refused_with_a_typed_reason_and_never_reaches_the_sandbox(
    world, runner, change, code
):
    which = demo_version(world)
    args = {"measure_id": which["measure_id"], "version": which["version"], **FORM_1,
            "entries": entries("English", "52", "58"), **change}
    got = call(world, TEACHER, "try_measure", **args)
    assert got["status"] == "refused" and got["reason"].startswith(f"[{code}]")
    assert len(runner) == 0
    rows = log_rows(world, "measure_demo_try")
    assert rows[-1][3] == "refused" and rows[-1][4] == "u-teacher"


def test_a_try_on_a_version_that_is_not_at_the_demo_is_refused(world, runner):
    which = demo_version(world, stop_after="approved")
    got = call(world, TEACHER, "try_measure", measure_id=which["measure_id"],
               version=which["version"], **FORM_1, entries=entries("English", "52", "58"))
    assert got["status"] == "refused" and "[not_at_demo]" in got["reason"]
    assert len(runner) == 0


def test_a_sandbox_that_cannot_be_reached_is_its_own_reason_and_is_asked_once(world, monkeypatch):
    calls = []

    def down(*a, **k):
        calls.append(1)
        raise client.RunnerRefusal(UNAVAILABLE, "the runner could not be reached")

    monkeypatch.setattr(client, "run", down)
    which = demo_version(world)
    got = call(world, TEACHER, "try_measure", measure_id=which["measure_id"],
               version=which["version"], **FORM_1, entries=entries("English", "52", "58"))
    assert got["status"] == "refused" and got["reason"].startswith("[runner_unavailable]")
    assert calls == [1], "one call, never a second try"


@pytest.mark.parametrize("kind", ["timeout", "memory", "crash", "runtime", "static"])
def test_a_function_that_fails_is_one_typed_refusal_with_its_kind_and_no_number(
    world, monkeypatch, kind
):
    def fails(*a, **k):
        raise client.RunnerRefusal(kind, "short detail")

    monkeypatch.setattr(client, "run", fails)
    which = demo_version(world)
    got = call(world, TEACHER, "try_measure", measure_id=which["measure_id"],
               version=which["version"], **FORM_1, entries=entries("English", "52", "58"))
    assert got["status"] == "refused" and got["reason"].startswith("[measure_failed]")
    assert kind in got["reason"] and "answer" not in got


def test_an_answer_that_is_not_exact_numbers_is_refused_and_not_shown(world, monkeypatch):
    def odd(source, digest, records, params, **k):
        return {"output_kind": "number", "output": {"change": True}}

    monkeypatch.setattr(client, "run", odd)
    which = demo_version(world)
    got = call(world, TEACHER, "try_measure", measure_id=which["measure_id"],
               version=which["version"], **FORM_1, entries=entries("English", "52", "58"))
    assert got["status"] == "refused" and "answer" not in got


def test_the_demo_runs_a_proposed_value_that_no_teacher_has_confirmed_and_says_so(world, runner):
    # at run time the same function is refused (placeholder_value); at the demo the value is shown
    # as a placeholder and used, so the teacher can see what confirming it would mean
    which = demo_version(world)
    got = call(world, TEACHER, "try_measure", measure_id=which["measure_id"],
               version=which["version"], **FORM_1, entries=entries("English", "52", "58", "63"))
    mp = next(c for c in got["clauses"] if c["clause_id"] == "MP-1")
    assert mp["state"] == "placeholder" and got["answer"]["threshold_met"]["exact"] == "1"


# =============================================================================== a teacher case
def test_a_teacher_case_is_stored_as_a_test_marked_teacher_and_the_tests_still_pass(world, runner):
    which = demo_version(world)
    args = dict(measure_id=which["measure_id"], version=which["version"], **FORM_1,
                entries=entries("English", "60", "70"))
    got = call(world, TEACHER, "save_case", **args,
               expected={"change": "10", "threshold_met": "1"})
    assert got["status"] == "saved" and got["name"] == "teacher case 1"
    stored = status_of(world, which)
    assert stored["status"] == "awaiting_demo"
    case = stored["tests"][-1]
    assert case["name"] == "teacher case 1" and case["source"] == "teacher"
    assert case["expected"] == {"change": "10", "threshold_met": "1"}
    assert case["basis"] == "typed"
    assert [r["score"] for r in case["records"]] == ["60", "70"]
    with psycopg.connect(world) as conn:
        result = conn.execute(
            "SELECT test_result FROM measure_versions WHERE measure_id = %s AND version = %s",
            (which["measure_id"], which["version"])).fetchone()[0]
    assert result["passed"] is True and result["ran"] == 3 and result["failed"] == []
    second = call(world, SECOND, "save_case", **args, expected={"change": "10"}, agreed=True)
    assert second["name"] == "teacher case 2"
    assert status_of(world, which)["tests"][-1]["basis"] == "agreed_with_answer"


def test_a_case_whose_expected_answer_differs_from_the_functions_is_not_stored_and_shows_both(
    world, runner
):
    which = demo_version(world)
    got = call(world, TEACHER, "save_case", measure_id=which["measure_id"],
               version=which["version"], **FORM_1, entries=entries("English", "52", "58", "63"),
               expected={"change": "10"})
    assert got["status"] == "refused" and got["reason"].startswith("[case_mismatch]")
    assert got["compare"]["match"] is False
    assert got["answer"]["change"]["exact"] == "11"
    assert got["compare"]["fields"][0]["expected"]["exact"] == "10"
    assert len(status_of(world, which)["tests"]) == 2, "nothing was stored"


@pytest.mark.parametrize(
    "expected", [{}, {"nope": "1"}, {"change": "eleven"}, {"change": 1.5}, "11"]
)
def test_an_expected_answer_that_is_not_exact_numbers_for_the_measures_fields_is_refused(
    world, runner, expected
):
    which = demo_version(world)
    got = call(world, TEACHER, "save_case", measure_id=which["measure_id"],
               version=which["version"], **FORM_1, entries=entries("English", "52", "58", "63"),
               expected=expected)
    assert got["status"] == "refused" and got["reason"].startswith("[bad_expected]")
    assert len(status_of(world, which)["tests"]) == 2


def test_a_case_cannot_be_added_to_a_version_that_is_not_at_the_demo(world, runner):
    which = demo_version(world, stop_after="approved")
    got = call(world, TEACHER, "save_case", measure_id=which["measure_id"],
               version=which["version"], **FORM_1, entries=entries("English", "60", "70"),
               expected={"change": "10"})
    assert got["status"] == "refused" and "[not_at_demo]" in got["reason"]


# =============================================================================== the decision
def test_a_version_with_no_test_from_outside_the_build_cannot_be_confirmed_and_the_screen_says_why(
    world, runner
):
    which = demo_version(world, independent=False)
    opened = call(world, TEACHER, "open_demo", **{k: which[k] for k in ("measure_id", "version")})
    assert opened["approval"]["can_confirm"] is False
    assert [g["code"] for g in opened["approval"]["gaps"]] == ["no_independent_test"]
    got = call(world, TEACHER, "confirm", **arguments_for("confirm", which))
    assert got["status"] == "refused" and "[approval_conditions_not_met]" in got["reason"]
    assert [g["code"] for g in got["gaps"]] == ["no_independent_test"]
    after = status_of(world, which)
    assert after["status"] == "awaiting_demo" and after["decision"] is None
    rows = log_rows(world, "measure_demo_confirm")
    assert rows[-1][3] == "refused" and rows[-1][4] == "u-teacher"


def test_a_teacher_case_is_the_test_from_outside_the_build_and_then_confirmation_approves(
    world, runner
):
    which = demo_version(world, independent=False)
    args = {"measure_id": which["measure_id"], "version": which["version"]}
    call(world, TEACHER, "try_measure", **args, **FORM_1, entries=entries("English", "60", "70"))
    saved = call(world, TEACHER, "save_case", **args, **FORM_1,
                 entries=entries("English", "60", "70"), expected={"change": "10"})
    assert saved["status"] == "saved"
    opened = call(world, TEACHER, "open_demo", **args)
    assert opened["approval"] == {"can_confirm": True, "gaps": []}
    done = call(world, TEACHER, "confirm", **args, note="matches my own marks")
    assert done["status"] == "confirmed"
    after = status_of(world, which)
    assert after["status"] == "approved" and after["decision"] == "confirmed"
    assert after["teacher"] == "Ms Teacher" and after["note"] == "matches my own marks"


def test_confirming_records_the_teacher_from_the_session_and_the_values_take_that_name(
    world, runner
):
    which = demo_version(world)
    args = {"measure_id": which["measure_id"], "version": which["version"]}
    done = call(world, SECOND, "confirm", **args, note=None)
    assert done["status"] == "confirmed" and done["to_status"] == "approved"
    assert done["teacher_name"] == "Mr Second" and done["values_confirmed_by"] == "Mr Second"
    assert done["values"][0]["clause_id"] == "MP-1" and done["values"][0]["value"] == "10"
    assert done["patch"]["patch_id"] == which["patch_id"] and done["patch"]["status"] == "proposed"
    assert done["next_step"] and "apply-template-patch" in done["next_step"]
    after = status_of(world, which)
    assert (after["status"], after["decision"], after["teacher"]) == (
        "approved", "confirmed", "Mr Second")
    # the patch is applied later, by an administrator; the database copies THIS teacher's name
    with psycopg.connect(world) as conn:
        conn.execute(
            "UPDATE template_patches SET status = 'applied', applied_by = 'admin', "
            "applied_at = now(), applied_as = 'baobab-v1' WHERE patch_id = %s",
            (which["patch_id"],))
        name = conn.execute("SELECT confirmed_by_name FROM template_patches WHERE patch_id = %s",
                            (which["patch_id"],)).fetchone()[0]
        conn.rollback()
    assert name == "Mr Second"
    rows = [r for r in log_rows(world, "registry_record_teacher_decision") if r[4] == "u-second"]
    assert rows and rows[-1][3] == "ok"
    ours = log_rows(world, "measure_demo_confirm")
    assert ours[-1][3] == "ok" and ours[-1][4:] == ("u-second", "Mr Second")


def test_confirming_twice_is_refused_and_changes_nothing(world, runner):
    which = demo_version(world)
    args = {"measure_id": which["measure_id"], "version": which["version"], "note": None}
    assert call(world, TEACHER, "confirm", **args)["status"] == "confirmed"
    again = call(world, SECOND, "confirm", **args)
    assert again["status"] == "refused" and "[not_at_demo]" in again["reason"]
    assert status_of(world, which)["teacher"] == "Ms Teacher"


def test_rejecting_needs_a_note_and_leaves_the_version_where_it_was_without_one(world, runner):
    which = demo_version(world)
    args = {"measure_id": which["measure_id"], "version": which["version"]}
    for note in (None, "", "   "):
        got = call(world, TEACHER, "reject", **args, note=note)
        assert got["status"] == "refused" and "[note_needed]" in got["reason"]
    assert status_of(world, which)["status"] == "awaiting_demo"


def test_rejecting_records_the_note_and_the_teacher_and_keeps_the_function_in_force(world, runner):
    first = demo_version(world, stop_after="approved")
    newer = at_demo(world, name=demokit.NAME, measure_id=first["measure_id"],
                    source=SOURCE.replace(">= threshold", "> threshold"))
    assert newer["measure_id"] == first["measure_id"] and newer["version"] == 2
    done = call(world, SECOND, "reject", measure_id=newer["measure_id"],
                version=newer["version"], note="the quote says 10 or more, this says more than 10")
    assert done["status"] == "rejected"
    after = status_of(world, newer)
    assert after["status"] == "rejected" and after["decision"] == "rejected"
    assert after["teacher"] == "Mr Second" and "10 or more" in after["note"]
    assert status_of(world, first)["status"] == "approved", "the previous function stays in force"


def test_confirming_a_newer_version_retires_the_old_one_in_the_same_step(world, runner):
    first = demo_version(world, stop_after="approved")
    newer = at_demo(world, name=demokit.NAME, measure_id=first["measure_id"],
                    source=SOURCE.replace(">= threshold", "> threshold"))
    done = call(world, TEACHER, "confirm", measure_id=newer["measure_id"],
                version=newer["version"], note=None)
    assert done["status"] == "confirmed"
    assert status_of(world, first)["status"] == "retired"
    assert status_of(world, newer)["status"] == "approved"


def test_the_system_never_approves_on_its_own_opening_trying_and_saving_leave_the_decision_empty(
    world, runner
):
    which = demo_version(world)
    args = {"measure_id": which["measure_id"], "version": which["version"]}
    call(world, TEACHER, "open_demo", **args)
    call(world, TEACHER, "try_measure", **args, **FORM_1, entries=entries("English", "52", "58"))
    call(world, TEACHER, "save_case", **args, **FORM_1, entries=entries("English", "60", "70"),
         expected={"change": "10"})
    after = status_of(world, which)
    assert after["status"] == "awaiting_demo" and after["decision"] is None
    assert after["teacher"] is None


def test_every_function_writes_one_diary_row_under_its_own_name_with_the_person(world, runner):
    which = demo_version(world)
    args = {"measure_id": which["measure_id"], "version": which["version"]}
    names = {
        "measure_demo_list": lambda: call(world, TEACHER, "list_awaiting"),
        "measure_demo_open": lambda: call(world, TEACHER, "open_demo", **args),
        "measure_demo_try": lambda: call(world, TEACHER, "try_measure", **args, **FORM_1,
                                         entries=entries("English", "52", "58")),
        "measure_demo_case": lambda: call(world, TEACHER, "save_case", **args, **FORM_1,
                                          entries=entries("English", "60", "70"),
                                          expected={"change": "10"}),
        "measure_demo_confirm": lambda: call(world, TEACHER, "confirm", **args, note=None),
    }
    for tool, act in names.items():
        before = len(log_rows(world, tool))
        act()
        rows = log_rows(world, tool)[before:]
        assert len(rows) == 1 and rows[0][4:] == ("u-teacher", "Ms Teacher"), tool
    other = demo_version(world)
    before = len(log_rows(world, "measure_demo_reject"))
    call(world, TEACHER, "reject", measure_id=other["measure_id"], version=other["version"],
         note="no")
    rows = log_rows(world, "measure_demo_reject")[before:]
    assert len(rows) == 1 and rows[0][4:] == ("u-teacher", "Ms Teacher")


def test_nothing_in_the_logged_rows_holds_the_function_source_or_a_secret(world, runner):
    which = demo_version(world)
    args = {"measure_id": which["measure_id"], "version": which["version"]}
    call(world, TEACHER, "open_demo", **args)
    call(world, TEACHER, "try_measure", **args, **FORM_1, entries=entries("English", "52", "58"))
    for tool in ("measure_demo_open", "measure_demo_try"):
        for row in log_rows(world, tool):
            assert "def measure" not in str(row[2]) and "def measure" not in str(row[1])
