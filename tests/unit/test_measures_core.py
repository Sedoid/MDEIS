"""The pure part of the run-time measure tools (T-072). No database.

`src/mdeis/core/measures.py` holds the exact-text records a measure is given, what a measure's
answer must look like before anything of it is returned, and how a number is shown.
`mdeis.sandbox.with_template` holds whether a measure is in force under the active template (it
reads the measure parameters section, which core code and hand-written tools do not).
Every expected value is worked out by hand in the comments.
"""

from fractions import Fraction
from types import SimpleNamespace

import pytest

from mdeis.core import measures
from mdeis.core.measures import OutputRefusal
from mdeis.sandbox import with_template


def mp(clause="MP-1", value_type="integer", unit="points", confirmed="Ms Teacher", value=10):
    return {
        "id": clause, "name": "n_" + clause[3:], "value_type": value_type, "value": value,
        "unit": unit, "source_document": "BAO-METH-1.0", "method_ref": "6.4",
        "confirmed_by": confirmed, "text": "t",
    }  # fmt: skip


CONTENT = {
    "trend": [{"id": "TR-1", "min_scores": 2, "window_terms": 3, "text": "evidence"}],
    "lessons": [{"id": "LS-1", "text": "a lesson rule"}],
    "measure_params": [mp("MP-1"), mp("MP-2", confirmed=None)],
}


# ----------------------------------------------------------------------- in force
def test_a_measure_whose_clauses_are_all_present_and_confirmed_is_in_force():
    assert with_template.force_problem(CONTENT, ["MP-1", "TR-1"]) is None


def test_a_hand_written_clause_needs_no_confirmation_only_to_be_there():
    assert with_template.force_problem(CONTENT, ["TR-1"]) is None
    optional = with_template.force_problem(CONTENT, ["LS-1"])
    assert optional is None, "an optional section is searched too"


def test_a_clause_the_template_does_not_have_is_clause_missing_and_the_clause_is_named():
    kind, detail = with_template.force_problem(CONTENT, ["MP-1", "TR-9"])
    assert kind == "clause_missing" and "TR-9" in detail
    assert with_template.force_problem({"trend": CONTENT["trend"]}, ["MP-1"])[0] == "clause_missing"


def test_a_measure_parameter_nobody_has_confirmed_is_a_placeholder_value():
    kind, detail = with_template.force_problem(CONTENT, ["MP-2"])
    assert kind == "placeholder_value" and "MP-2" in detail


def test_a_changed_type_or_unit_is_clause_changed_when_the_recorded_form_is_known():
    recorded = {"MP-1": ("integer", "points")}
    assert with_template.force_problem(CONTENT, ["MP-1"], recorded) is None
    other_unit = {"MP-1": ("integer", "scores")}
    assert with_template.force_problem(CONTENT, ["MP-1"], other_unit)[0] == "clause_changed"
    other_type = {"MP-1": ("rational", "points")}
    assert with_template.force_problem(CONTENT, ["MP-1"], other_type)[0] == "clause_changed"
    nothing_recorded = with_template.force_problem(CONTENT, ["MP-1"], {})
    assert nothing_recorded is None, "nothing recorded, nothing to differ"


def test_a_changed_value_alone_keeps_the_measure_in_force():
    # Decision 10 of the owner (2026-11-17): the value is the school's rule, read at run time.
    moved = {**CONTENT, "measure_params": [mp("MP-1", value=12)]}
    assert with_template.force_problem(moved, ["MP-1"], {"MP-1": ("integer", "points")}) is None


def test_the_first_problem_is_the_same_each_time_whatever_the_order_given():
    first = with_template.force_problem(CONTENT, ["TR-9", "MP-2", "LS-8"])
    again = with_template.force_problem(CONTENT, ["LS-8", "TR-9", "MP-2"])
    assert first == again and first[0] == "clause_missing"
    assert "LS-8" in first[1], "LS-8 sorts first"


# ----------------------------------------------------------------------- what the template recorded
def test_the_recorded_form_is_read_from_the_template_and_from_a_patch_in_either_shape():
    base = {"measure_params": [mp("MP-1", "integer", "points")]}
    as_object = {"measure_params": [mp("MP-2", "rational", "percent")]}
    as_list = [mp("MP-3", "names", "none")]
    got = with_template.recorded_types(base, as_object, as_list, None, "not a template")
    assert got == {
        "MP-1": ("integer", "points"),
        "MP-2": ("rational", "percent"),
        "MP-3": ("names", "none"),
    }


def test_the_first_source_that_names_a_clause_wins():
    first = {"measure_params": [mp("MP-1", "integer", "points")]}
    second = {"measure_params": [mp("MP-1", "rational", "percent")]}
    assert with_template.recorded_types(first, second) == {"MP-1": ("integer", "points")}


# ----------------------------------------------------------------------- the records a measure gets
RULES = SimpleNamespace(scale_max=Fraction(100))


def record(rid, year, term, subject, score, denominator):
    return {
        "record_id": rid, "year": year, "term": term, "stage": "Form 1", "subject": subject,
        "score": score, "denominator": denominator,
    }  # fmt: skip


RECORDS = [
    record("A-000002", "2026/2027", 2, "Mathematics", Fraction(12), Fraction(20)),
    record("A-000001", "2025/2026", 3, "Mathematics", Fraction(31, 2), Fraction(20)),
    record("A-000004", "2026/2027", 1, "Mathematics", None, Fraction(20)),
    record("A-000003", "2026/2027", 2, "English", Fraction(10), Fraction(100)),
]


def test_records_are_exact_text_on_the_rulebook_scale_oldest_first_and_blank_terms_are_left_out():
    # 12 out of 20 on a scale of 100 is 12 * 100 / 20 = 60. 31/2 out of 20 is 31/2 * 100 / 20 =
    # 155/2 (77.5). 10 out of 100 stays 10. Order: year, term, then subject (English before
    # Mathematics in the same term), then the record number.
    got = measures.runner_records(RECORDS, RULES)
    assert [(r["record_id"], r["score"]) for r in got] == [
        ("A-000001", "155/2"),
        ("A-000003", "10"),
        ("A-000002", "60"),
    ]
    assert set(got[0]) == {"record_id", "year", "term", "stage", "subject", "score"}
    assert (got[0]["year"], got[0]["term"]) == ("2025/2026", 3)
    assert got[0]["subject"] == "Mathematics"


def test_one_subject_can_be_asked_for_and_nothing_is_a_float():
    got = measures.runner_records(RECORDS, RULES, subject="Mathematics")
    assert [r["record_id"] for r in got] == ["A-000001", "A-000002"]
    assert all(isinstance(r["score"], str) for r in got)
    assert measures.runner_records(RECORDS, RULES, subject="Science") == []


# ----------------------------------------------------------------------- the answer of a measure
SENT = {"A-000001", "A-000002", "A-000003"}


def good(**over):
    return {
        "change": "11", "threshold_met": "1", "scores_used": "3",
        "record_ids": ["A-000001", "A-000003"], **over,
    }  # fmt: skip


def refused(output, sent=SENT, schema=None, banned=()):
    with pytest.raises(OutputRefusal) as info:
        measures.check_number_output(output, sent, schema, banned)
    return str(info.value)


def test_a_good_answer_gives_exact_numbers_and_the_records_it_used():
    got = measures.check_number_output(good(change="31/2"), SENT)
    assert got["numbers"] == {
        "change": Fraction(31, 2), "threshold_met": Fraction(1), "scores_used": Fraction(3),
    }  # fmt: skip
    assert got["record_ids"] == ["A-000001", "A-000003"]


def test_no_field_has_to_be_called_value_and_one_number_is_enough():
    assert measures.check_number_output({"anything_at_all": "0"}, SENT)["numbers"] == {
        "anything_at_all": Fraction(0)
    }


def test_a_decimal_written_as_text_is_read_exactly():
    # "0.60" is 3/5 exactly, as the runner writes a decimal.
    three_fifths = measures.check_number_output(good(change="0.60"), SENT)
    assert three_fifths["numbers"]["change"] == Fraction(3, 5)
    minus = measures.check_number_output(good(change="-2.5"), SENT)
    assert minus["numbers"]["change"] == Fraction(-5, 2)


def test_an_answer_that_names_no_records_means_none_were_singled_out():
    out = good()
    del out["record_ids"]
    assert measures.check_number_output(out, SENT)["record_ids"] is None


@pytest.mark.parametrize(
    "bad",
    ["abc", "", "1e3", "1_000", "0x10", "1/0", "1.", ".5", " 3", "3 ", "3\n", "--1", "1/2/3", "NaN",
     "Infinity", "1e999999999"],
)
def test_text_that_is_not_a_plain_exact_number_is_refused(bad):
    assert "change" in refused(good(change=bad))


@pytest.mark.parametrize("bad", [3, 3.5, True, False, None, ["1"], {"a": "1"}])
def test_a_number_that_is_not_text_is_refused_and_a_yes_or_no_is_one_or_zero_not_true(bad):
    refused(good(change=bad))
    refused(good(scores_used=bad))


def test_an_answer_with_no_number_or_that_is_not_an_object_is_refused():
    assert "no number" in refused({})
    assert "no number" in refused({"record_ids": ["A-000001"]})
    for bad in ([], "11", None, 11):
        refused(bad)


def test_a_record_that_was_not_sent_is_refused_and_so_is_a_list_that_is_not_text():
    refused(good(record_ids=["A-000001", "A-000999"]))
    refused(good(record_ids="A-000001"))
    refused(good(record_ids=[1]))
    refused(good(record_ids=[["A-000001"]]))


def test_a_field_name_must_be_plain_lower_case_words_and_carry_no_banned_word():
    refused(good(**{"Bad Name": "1"}))
    refused(good(**{"1st": "1"}))
    refused(good(**{"a-b": "1"}))
    assert "weak" in refused(good(weak_flag="1"), banned=["weak"])
    assert "weak" in refused(good(**{"is_weak_here": "1"}), banned=["Weak"])
    measures.check_number_output(good(weakness="1"), SENT, None, ["weak"])  # another word


def test_a_declared_field_list_closes_the_answer_and_record_ids_is_always_allowed():
    schema = {"kind": "number", "fields": ["change", "scores_used", "threshold_met"]}
    assert measures.check_number_output(good(), SENT, schema)["record_ids"]
    assert "extra" in refused(good(extra="1"), schema=schema)
    only = {"kind": "number", "fields": []}
    assert "change" in refused(good(), schema=only)
    assert measures.check_number_output(good(), SENT, {"kind": "number"})["numbers"], "no list"


def test_each_refusal_carries_a_short_code_a_tool_can_show_without_repeating_what_was_returned():
    schema = {"kind": "number", "fields": ["scores_used"]}
    cases = [
        ([], None, (), "not_an_object"),
        ({}, None, (), "no_numbers"),
        (good(change="abc"), None, (), "not_exact"),
        (good(change=True), None, (), "not_exact"),
        (good(**{"Bad": "1"}), None, (), "field_name"),
        (good(weak_x="1"), None, ["weak"], "banned_word"),
        (good(extra="1"), schema, (), "unknown_field"),
        (good(record_ids=["A-000999"]), None, (), "unknown_record"),
        (good(record_ids="A-000001"), None, (), "bad_record_ids"),
    ]
    for output, given_schema, banned, code in cases:
        with pytest.raises(OutputRefusal) as info:
            measures.check_number_output(output, SENT, given_schema, banned)
        assert info.value.code == code, (output, info.value)


# ----------------------------------------------------------------------- how a number is shown
@pytest.mark.parametrize(
    ("number", "value", "exact"),
    [
        (Fraction(31, 2), "15.50", "31/2"),
        (Fraction(2, 3), "0.67", "2/3"),
        (Fraction(11), "11.00", "11"),
        (Fraction(-5, 2), "-2.50", "-5/2"),
        (Fraction(0), "0.00", "0"),
        (Fraction(1, 200), "0.01", "1/200"),  # a half of a hundredth rounds up
    ],
)
def test_a_number_is_shown_to_two_places_with_its_exact_form(number, value, exact):
    assert measures.number_view(number) == {"value": value, "exact": exact}


# ----------------------------------------------------------------------- wording
def test_banned_words_are_found_as_whole_words_in_any_case_and_inside_a_joined_name():
    banned = ["weak", "gifted"]
    assert measures.banned_hits("A Weak start", banned) == ["weak"]
    assert measures.banned_hits("is_gifted_flag", banned) == ["gifted"]
    assert measures.banned_hits("weakness and giftedness", banned) == []
    assert measures.banned_hits("", banned) == []
    assert measures.banned_hits("weak weak gifted", banned) == ["weak", "gifted"]


def test_only_a_number_kind_is_supported_in_the_first_version():
    assert measures.SUPPORTED_KINDS == ("number",)
