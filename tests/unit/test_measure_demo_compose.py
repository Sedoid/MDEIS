"""The pure part of the teacher demo (T-073): what a teacher sees, how a "try your own" is checked,
what is still missing before a confirmation. No database, no sandbox, no network.

Expected values are worked by hand from the Baobab methodology
(data/synthetic/methodology/methodology-baobab.md, sections 5 and 6.4), not from the code's output:

    English 52, 58, 63          window 3, change 63 - 52 = 11, threshold 10 met, 3 scores used
    English 52.5, 58, 63.5      the same change, 11, with decimal scores (exact: 105/2, 58, 127/2)
    English 60, 70              2 scores meet the minimum, change 10, met (case B-06)
"""

import ast
import json
from fractions import Fraction
from pathlib import Path

import pytest

from mdeis.core.measures import banned_hits
from mdeis.measure_demo import compose

ROOT = Path(__file__).resolve().parents[2]
SUBJECTS = ["Mathematics", "English", "Science", "Humanities"]
STAGES = ["Form 1", "Form 2", "Form 3", "Form 4"]
SCALE = Fraction(100)
BANNED = ["weak", "remedial", "gifted", "lazy", "slow", "failing", "brilliant"]
BANNED_PHRASES = ["top group", "bottom group", "ability group", "career path", "subject choice"]


def rows(*scores, subject="English"):
    return [{"subject": subject, "term": i, "score": s} for i, s in enumerate(scores, start=1)]


def good(**over):
    return {"stage": "Form 1", "entries": rows("52", "58", "63"), **over}


def records(body, scope="learner_skill", fields=compose.TRY_FIELDS):
    return compose.try_records(
        body, fields=fields, subjects=SUBJECTS, stages=STAGES, scale_max=SCALE, scope=scope
    )


def code_of(body, scope="learner_skill", fields=compose.TRY_FIELDS):
    with pytest.raises(compose.TryRefused) as refused:
        records(body, scope, fields)
    return refused.value.code


# --------------------------------------------------------------------------- the records made
def test_a_good_try_becomes_records_in_the_shape_the_runner_gets_oldest_first():
    made = records(good())
    assert [r["score"] for r in made] == ["52", "58", "63"]
    assert [r["term"] for r in made] == [1, 2, 3]
    assert [r["record_id"] for r in made] == ["TRY-1", "TRY-2", "TRY-3"]
    for record in made:
        assert set(record) == {"record_id", "year", "term", "stage", "subject", "score"}
        assert record["stage"] == "Form 1" and record["subject"] == "English"
        assert record["year"] == compose.RECORD_YEAR


def test_scores_are_made_exact_and_entries_are_put_in_term_order():
    body = {"stage": "Form 2", "entries": [
        {"subject": "English", "term": 3, "score": "63.5"},
        {"subject": "English", "term": 1, "score": "52.5"},
        {"subject": "English", "term": 2, "score": 58},
    ]}
    made = records(body)
    assert [r["score"] for r in made] == ["105/2", "58", "127/2"]  # 52.5, 58, 63.5
    assert [r["term"] for r in made] == [1, 2, 3]


def test_a_fraction_is_accepted_as_text_and_both_ends_of_the_scale_are_allowed():
    made = records({"stage": "Form 1", "entries": rows("0", "100", "61/4")})
    assert [r["score"] for r in made] == ["0", "100", "61/4"]


def test_the_same_try_always_gives_the_same_records():
    assert records(good()) == records(json.loads(json.dumps(good())))


def test_with_a_whole_learner_measure_several_subjects_are_allowed_in_term_then_subject_order():
    body = {"stage": "Form 1", "entries": [
        {"subject": "Science", "term": 1, "score": "64"},
        {"subject": "English", "term": 1, "score": "52"},
        {"subject": "English", "term": 2, "score": "58"},
    ]}
    made = records(body, scope="learner")
    assert [(r["term"], r["subject"]) for r in made] == [
        (1, "English"), (1, "Science"), (2, "English")]
    assert code_of(body, scope="learner_skill") == "one_subject_needed"


@pytest.mark.parametrize("body,code", [
    ([], "bad_body"),
    ("text", "bad_body"),
    (None, "bad_body"),
    ({"entries": rows("52", "58")}, "bad_body"),
    ({"stage": "Form 1"}, "bad_body"),
    (good(stage="Form 9"), "unknown_stage"),
    (good(stage=""), "unknown_stage"),
    (good(stage=3), "unknown_stage"),
    (good(entries=[]), "no_scores"),
    (good(entries="52"), "no_scores"),
    (good(entries=None), "no_scores"),
    (good(entries=["52"]), "bad_entry"),
    (good(entries=[{"subject": "English", "term": 1}]), "bad_entry"),
    (good(entries=[{"subject": "English", "term": 1, "score": "5", "learner_id": "L-1"}]),
     "bad_entry"),
    (good(entries=[{"subject": "English", "term": 1, "score": "5", "name": "A"}]), "bad_entry"),
    (good(entries=rows("52") + [{"subject": "Art", "term": 2, "score": "58"}]), "unknown_subject"),
    (good(entries=[{"subject": 5, "term": 1, "score": "5"}]), "unknown_subject"),
])
def test_a_try_that_is_not_in_the_closed_shape_is_refused_with_a_typed_code(body, code):
    assert code_of(body) == code


@pytest.mark.parametrize("field", [
    "learner_id", "learner", "name", "school", "school_code", "approver", "approver_name",
    "user", "user_id", "teacher", "status", "token", "measure_id", "version", "source",
])
def test_no_field_can_name_a_learner_a_person_a_school_or_change_the_measure(field):
    assert code_of(good(**{field: "x"})) == "unknown_field"


@pytest.mark.parametrize("term", [0, -1, "2", True, 2.5, None])
def test_a_term_is_a_whole_number_from_one(term):
    body = good(entries=[{"subject": "English", "term": term, "score": "50"}])
    assert code_of(body) == "bad_term"


@pytest.mark.parametrize("score", [52.5, "abc", "", "-5", "1e2", "3/0", True, None, "5 ", " 5",
                                   "1,5", [5], {"a": 1}])
def test_a_score_is_plain_exact_text_or_a_whole_number_never_a_float_or_a_word(score):
    body = good(entries=[{"subject": "English", "term": 1, "score": score}])
    assert code_of(body) == "bad_score"


@pytest.mark.parametrize("score", ["100.01", "101", 101, "1000", "201/2"])
def test_a_score_above_the_rulebooks_scale_is_refused_never_clipped(score):
    body = good(entries=[{"subject": "English", "term": 1, "score": score}])
    assert code_of(body) == "score_off_scale"


def test_the_scale_comes_from_the_caller_not_from_the_code():
    body = good(entries=[{"subject": "English", "term": 1, "score": "30"}])
    with pytest.raises(compose.TryRefused) as refused:
        compose.try_records(body, fields=compose.TRY_FIELDS, subjects=SUBJECTS, stages=STAGES,
                            scale_max=Fraction(20), scope="learner_skill")
    assert refused.value.code == "score_off_scale"


def test_two_scores_for_the_same_subject_and_term_are_refused():
    body = good(entries=[{"subject": "English", "term": 1, "score": "50"},
                         {"subject": "English", "term": 1, "score": "60"}])
    assert code_of(body) == "duplicate_entry"


def test_a_case_may_carry_two_more_fields_and_a_try_may_not():
    extra = {"expected": {"change": "11"}, "agreed": False}
    assert code_of(good(**extra)) == "unknown_field"
    assert records(good(**extra), fields=compose.CASE_FIELDS)


def test_a_refusal_says_what_is_wrong_in_plain_words_without_repeating_what_was_sent():
    body = good(entries=[{"subject": "English", "term": 1, "score": "Ada Lovelace"}])
    with pytest.raises(compose.TryRefused) as refused:
        records(body)
    assert "Ada" not in refused.value.detail and refused.value.detail


# --------------------------------------------------------------------------- the teacher's answer
def test_an_expected_answer_is_exact_numbers_under_fields_the_measure_declares():
    got = compose.expected_numbers(
        {"change": "11.0", "threshold_met": 1, "scores_used": "3"},
        ["change", "enough_scores", "scores_used", "threshold_met"],
    )
    assert got == {"change": "11", "threshold_met": "1", "scores_used": "3"}
    assert compose.expected_numbers({"change": "-8"}, ["change"]) == {"change": "-8"}
    assert compose.expected_numbers({"change": "61/4"}, ["change"]) == {"change": "61/4"}


@pytest.mark.parametrize("expected", [
    {}, [], None, "11", {"change": 11.5}, {"change": True}, {"change": "eleven"}, {"change": ""},
    {"nope": "11"}, {"change": "11", "nope": "1"}, {"Change": "11"}, {"change": "1/0"},
])
def test_an_expected_answer_that_is_not_exact_numbers_for_declared_fields_is_refused(expected):
    with pytest.raises(compose.TryRefused) as refused:
        compose.expected_numbers(expected, ["change", "threshold_met"])
    assert refused.value.code == "bad_expected"


def test_the_answer_is_compared_field_by_field_with_exact_numbers():
    numbers = {"change": Fraction(11), "enough_scores": Fraction(1), "scores_used": Fraction(3),
               "threshold_met": Fraction(1)}
    same = compose.compare({"change": "11", "threshold_met": "1"}, numbers)
    assert same["match"] is True
    assert [f["field"] for f in same["fields"]] == ["change", "threshold_met"]
    assert all(f["match"] for f in same["fields"])
    off = compose.compare({"change": "10", "threshold_met": "1"}, numbers)
    assert off["match"] is False
    assert [f["match"] for f in off["fields"]] == [False, True]
    assert off["fields"][0]["expected"] == {"value": "10.00", "exact": "10"}
    assert off["fields"][0]["returned"] == {"value": "11.00", "exact": "11"}


def test_a_field_the_function_did_not_return_is_a_mismatch_not_an_error():
    got = compose.compare({"change": "11"}, {"scores_used": Fraction(1)})
    assert got["match"] is False and got["fields"][0]["returned"] is None


def test_the_answer_is_shown_to_two_places_with_the_exact_form_beside_it():
    shown = compose.answer_view({"change": Fraction(61, 4), "threshold_met": Fraction(1)})
    assert shown == {
        "change": {"value": "15.25", "exact": "61/4"},
        "threshold_met": {"value": "1.00", "exact": "1"},
    }


# --------------------------------------------------------------------------- worked examples
QUOTE = "The scores, oldest first, were 52, 58 and 63. The change was 63 minus 52, which is 11"
TESTS = [
    {"name": "build four scores", "source": "build", "records": [{"score": "40"}],
     "expected": {"change": "2"}},
    {"name": "worked 6.4", "source": "document_example", "quote": QUOTE, "quote_section": "6.4",
     "records": [{"score": "52"}, {"score": "58"}, {"score": "63"}],
     "expected": {"change": "11", "threshold_met": "1"}},
    {"name": "teacher case 1", "source": "teacher", "basis": "typed",
     "records": [{"record_id": "TRY-1", "year": "demo", "term": 1, "stage": "Form 1",
                  "subject": "English", "score": "60"}],
     "expected": {"change": "0"}},
]


def test_a_worked_example_shows_inputs_what_the_document_says_and_what_the_function_returned():
    answers = {"worked 6.4": {"numbers": {"change": Fraction(11), "threshold_met": Fraction(1),
                                          "scores_used": Fraction(3)}}}
    (row,) = compose.example_rows(TESTS, answers)
    assert row["name"] == "worked 6.4" and row["quote"] == QUOTE and row["quote_section"] == "6.4"
    assert row["inputs"] == ["52", "58", "63"]
    assert row["expected"] == {"change": {"value": "11.00", "exact": "11"},
                               "threshold_met": {"value": "1.00", "exact": "1"}}
    assert row["returned"]["change"] == {"value": "11.00", "exact": "11"}
    assert row["match"] is True and row["problem"] is None


def test_an_example_the_function_gets_different_is_marked_not_matching_never_hidden():
    answers = {"worked 6.4": {"numbers": {"change": Fraction(10), "threshold_met": Fraction(1)}}}
    (row,) = compose.example_rows(TESTS, answers)
    assert row["match"] is False
    assert [f["match"] for f in row["fields"]] == [False, True]


def test_an_example_that_could_not_be_run_says_so_and_is_neither_a_match_nor_a_mismatch():
    answers = {"worked 6.4": {"problem": "runner_unavailable"}}
    (row,) = compose.example_rows(TESTS, answers)
    assert row["match"] is None and row["returned"] is None
    assert row["problem"] == "runner_unavailable"
    (missing,) = compose.example_rows(TESTS, {})
    assert missing["match"] is None and missing["problem"]


def test_only_examples_taken_from_the_document_are_listed_as_worked_examples():
    assert [r["name"] for r in compose.example_rows(TESTS, {})] == ["worked 6.4"]


def test_teacher_cases_are_listed_apart_with_their_inputs_and_expected_numbers():
    (case,) = compose.case_rows(TESTS, {"results": [{"name": "teacher case 1", "passed": True}]})
    assert case["name"] == "teacher case 1" and case["inputs"] == ["60"]
    assert case["expected"] == {"change": {"value": "0.00", "exact": "0"}}
    assert case["passed"] is True and case["basis"] == "typed"


def test_the_checks_count_the_tests_by_where_they_came_from():
    result = {"passed": True, "ran": 3, "failed": [], "results": []}
    got = compose.test_summary(TESTS, result)
    assert got == {"passed": True, "ran": 3, "failed": [],
                   "by_source": {"build": 1, "document_example": 1, "teacher": 1}}
    assert compose.test_summary([], None)["passed"] is False


# --------------------------------------------------------------------------- clauses and values
CONTENT = {
    "trend": [{"id": "TR-1", "min_scores": 2, "window_terms": 3, "method_ref": "5",
               "text": "A pattern is stated only with at least 2 term scores in the window."}],
    "measure_params": [{
        "id": "MP-4", "name": "steady_band", "value_type": "integer", "value": 8,
        "unit": "points", "source_document": "BAO-METH-1.0", "method_ref": "6.3",
        "confirmed_by": "Ms Teacher", "text": "A spread of 8 points or fewer is steady."}],
}
PROPOSED = {
    "id": "MP-1", "name": "moved_at_or_above", "value_type": "integer", "value": 10,
    "unit": "points", "source_document": "BAO-METH-1.0", "method_ref": "6.4",
    "confirmed_by": None, "text": "A change of 10 points or more counts as moved."}


def test_a_proposed_value_is_shown_as_a_placeholder_until_a_teacher_confirms_it():
    rows_ = compose.clause_rows(
        ["MP-1", "TR-1"], CONTENT, [PROPOSED], {"MP-1": "10", "TR-1.window_terms": "3"})
    mp, tr = rows_
    assert mp["clause_id"] == "MP-1" and mp["kind"] == "measure_parameter"
    assert mp["state"] == compose.PLACEHOLDER and mp["confirmed_by"] is None
    assert (mp["value"], mp["unit"], mp["method_ref"]) == ("10", "points", "6.4")
    assert mp["source_document"] == "BAO-METH-1.0" and mp["text"].startswith("A change of 10")
    assert mp["reads"] == [{"key": "MP-1", "value": "10"}]
    assert tr["kind"] == "rulebook" and tr["state"] == compose.IN_RULEBOOK
    assert tr["reads"] == [{"key": "TR-1.window_terms", "value": "3"}]
    assert tr["text"].startswith("A pattern is stated")


def test_a_value_already_in_the_rulebook_and_confirmed_shows_who_confirmed_it():
    (mp,) = compose.clause_rows(["MP-4"], CONTENT, [], {"MP-4": "8"})
    assert mp["state"] == compose.CONFIRMED and mp["confirmed_by"] == "Ms Teacher"


def test_a_value_in_the_rulebook_with_no_confirmation_is_a_placeholder():
    content = {"measure_params": [{**CONTENT["measure_params"][0], "confirmed_by": None}]}
    (mp,) = compose.clause_rows(["MP-4"], content, [], {})
    assert mp["state"] == compose.PLACEHOLDER


def test_a_clause_found_nowhere_is_missing_and_the_screen_says_so():
    (row,) = compose.clause_rows(["MP-9"], CONTENT, [PROPOSED], {})
    assert row["state"] == compose.MISSING and row["text"] is None


def test_the_patch_does_not_override_a_clause_the_rulebook_already_has():
    other = {**PROPOSED, "id": "MP-4", "value": 99}
    (mp,) = compose.clause_rows(["MP-4"], CONTENT, [other], {})
    assert mp["value"] == "8" and mp["state"] == compose.CONFIRMED


def test_a_list_of_names_and_a_quoted_fraction_are_shown_as_text():
    names = {**PROPOSED, "id": "MP-2", "value_type": "names", "value": ["Ada", "Ben"],
             "unit": "names"}
    ratio = {**PROPOSED, "id": "MP-3", "value_type": "rational", "value": "3/5", "unit": "ratio"}
    got = compose.clause_rows(["MP-2", "MP-3"], {}, [names, ratio], {})
    assert [r["value"] for r in got] == ["Ada, Ben", "3/5"]


# --------------------------------------------------------------------------- what is missing
FACTS_OK = {"status": "awaiting_demo", "tests_passed": True, "independent_test": True,
            "static_passed": True, "review_cleared": True, "review_result": "pass"}


def test_nothing_is_missing_when_every_condition_the_registry_names_holds():
    assert compose.approval_gaps(FACTS_OK, []) == []


@pytest.mark.parametrize("key,code", [
    ("tests_passed", "tests_not_passed"),
    ("independent_test", "no_independent_test"),
    ("static_passed", "static_not_passed"),
    ("review_cleared", "review_holding"),
])
def test_each_missing_condition_is_named_in_plain_words(key, code):
    gaps = compose.approval_gaps({**FACTS_OK, key: False}, [])
    assert [g["code"] for g in gaps] == [code]
    assert gaps[0]["text"].endswith(".") and gaps[0]["text"][0].isupper()


def test_a_version_that_is_not_waiting_for_the_teacher_cannot_be_confirmed():
    assert [g["code"] for g in compose.approval_gaps({**FACTS_OK, "status": "approved"}, [])] == [
        "not_at_demo"]


def test_banned_wording_in_what_a_model_wrote_is_a_gap_and_names_the_words():
    gaps = compose.approval_gaps(FACTS_OK, ["weak"])
    assert [g["code"] for g in gaps] == ["banned_wording"] and "weak" in gaps[0]["text"]


def test_gaps_come_out_in_one_fixed_order_with_everything_missing():
    facts = {"status": "awaiting_review", "tests_passed": False, "independent_test": False,
             "static_passed": False, "review_cleared": False, "review_result": "hold"}
    assert [g["code"] for g in compose.approval_gaps(facts, ["weak"])] == [
        "not_at_demo", "tests_not_passed", "no_independent_test", "static_not_passed",
        "review_holding", "banned_wording"]


# --------------------------------------------------------------------------- the whole view
def version_row(**over):
    row = {
        "measure_id": "MS-0001", "version": 1, "status": "awaiting_demo",
        "name": "Change over the window",
        "description": "The change across the window of one subject.",
        "method_ref": "6.4", "input_scope": "learner_skill", "output_kind": "number",
        "output_schema": {"kind": "number", "fields": ["threshold_met", "change"]},
        "methodology_document_id": "D-000012", "section_sha256": "a" * 64,
        "code_sha256": "b" * 64, "template_version": "baobab-v1",
        "built_by_run": "BUILD-0001",
        "static_check": {"passed": True, "findings": []},
        "tests": TESTS, "test_result": {"passed": True, "ran": 3, "failed": [], "results": []},
        "review_result": "pass", "review_reasons": ["agrees with the quoted section"],
        "hold_cleared_name": None,
    }
    row.update(over)
    return row


def view(**over):
    base = dict(
        version=version_row(),
        quotes=[{"key": "MP-1", "quote": "10 points or more", "section_id": "6.4",
                 "title": "Moved", "page": 3, "section_sha256": "c" * 64}],
        clauses=[], patch={"patch_id": "TP-000001", "status": "proposed", "applied_as": None},
        skipped=[{"section_id": "6.2", "title": "Pass", "clause_ids": ["SC-3"], "reason": "x",
                  "tool": None}],
        build_anyway=["6.4"], examples=[], cases=[], facts=FACTS_OK, wording=[],
        form={"scope": "learner_skill", "stages": STAGES, "subjects": SUBJECTS, "scale_max": "100"},
    )
    base.update(over)
    return compose.demo_view(**base)


def test_the_view_holds_what_a_teacher_needs_to_decide():
    got = view()
    assert got["status"] == "ok" and (got["measure_id"], got["version"]) == ("MS-0001", 1)
    assert got["name"] == "Change over the window" and got["description"]
    assert got["output_fields"] == ["change", "threshold_met"]
    assert got["origin"]["methodology_document_id"] == "D-000012"
    first = got["origin"]["quotes"][0]
    assert first["page"] == 3 and first["section_id"] == "6.4"
    assert got["skipped"][0]["section_id"] == "6.2" and got["build_anyway"] == ["6.4"]
    assert got["review"] == {"result": "pass", "reasons": ["agrees with the quoted section"],
                             "cleared_by": None}
    assert got["checks"]["static"] == {"passed": True, "findings": []}
    assert got["checks"]["tests"]["by_source"]["teacher"] == 1
    assert got["checks"]["sensitivity"]["passed"] is True
    assert got["patch"]["patch_id"] == "TP-000001"
    assert got["approval"] == {"can_confirm": True, "gaps": []}
    assert got["try"]["stages"] == STAGES and got["try"]["subjects"] == SUBJECTS
    assert got["try"]["scale_max"] == "100" and got["try"]["notice"] == compose.SYNTHETIC_NOTICE


def test_the_sensitivity_check_is_claimed_only_for_a_version_the_build_made():
    made = view()["checks"]["sensitivity"]
    assert made["passed"] is True and made["how"] == compose.SENSITIVITY_CHECKED
    other = view(version=version_row(built_by_run="RUN-by-hand"))["checks"]["sensitivity"]
    assert other["passed"] is None and other["how"] == compose.SENSITIVITY_UNKNOWN


def test_the_view_says_what_is_missing_instead_of_offering_a_confirmation():
    got = view(facts={**FACTS_OK, "independent_test": False})
    assert got["approval"]["can_confirm"] is False
    assert [g["code"] for g in got["approval"]["gaps"]] == ["no_independent_test"]


def test_the_view_carries_the_notice_that_only_synthetic_scores_belong_here():
    assert "synthetic" in compose.SYNTHETIC_NOTICE.lower()
    assert "name" in compose.SYNTHETIC_NOTICE.lower()


def test_no_key_in_the_view_names_an_approver_or_ranks_or_labels_anyone():
    forbidden = {"approver", "approver_id", "approver_name", "rank", "ranking", "label", "grade",
                 "pathway", "recommendation", "learner_id", "student", "pupil"}
    found = set()

    def walk(node):
        if isinstance(node, dict):
            found.update(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(view())
    assert not found & forbidden, found & forbidden


def test_the_same_inputs_give_the_same_view():
    assert view() == view()


# --------------------------------------------------------------------------- wording
def string_constants(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.append(node.value)
    return found


def test_no_sentence_the_demo_can_show_uses_a_word_the_rulebook_bans():
    folder = ROOT / "src" / "mdeis" / "measure_demo"
    files = sorted(folder.glob("*.py")) + [ROOT / "src" / "mdeis" / "measure_routes.py"]
    assert files and all(f.exists() for f in files)
    for path in files:
        for text in string_constants(path):
            assert not banned_hits(text, BANNED), (path.name, text)
            for phrase in BANNED_PHRASES:
                assert phrase not in text.lower(), (path.name, text)


def test_the_words_in_this_test_are_the_ones_the_baobab_rulebook_bans():
    import yaml

    content = yaml.safe_load((ROOT / "templates" / "baobab-v1.yaml").read_text(encoding="utf-8"))
    words, phrases = set(), set()
    for rule in content["wording"]:
        words |= set(rule.get("banned") or [])
        phrases |= set(rule.get("banned_phrases") or [])
    assert set(BANNED) <= words, "the list used above must not be narrower than the rulebook's"
    assert set(BANNED_PHRASES) <= phrases | {"career path", "subject choice"}
    assert words <= set(BANNED)


def test_the_composer_holds_no_school_number_as_a_rule():
    tree = ast.parse((ROOT / "src" / "mdeis" / "measure_demo" / "compose.py").read_text("utf-8"))
    numbers = [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
        and not isinstance(n.value, bool)
    ]
    assert set(numbers) <= {0, 1}, numbers
