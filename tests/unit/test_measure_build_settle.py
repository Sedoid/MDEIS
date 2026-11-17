"""One home per setting, and the quote check on extracted rules (T-071). Pure code: the Baobab
fixture and the two templates are read from disk; there is no database and no model.

Expected values are read by hand: Baobab (BAO-METH-1.0) states a moved threshold of 10 points (6.4,
3.2), a window of 3 (3.2), a minimum evidence of 2 (3.2) and a score scale of 0 to 100 (3.2).
templates/baobab-v1.yaml holds window 3 and minimum 2 in TR-1, scale 100 in SC-1.
templates/v1.1.yaml (another school) holds scale 20 in SC-1, window 3 and minimum 3 in TR-1."""

import copy

import pytest
from build_kit import (
    EVIDENCE_QUOTE,
    MOVED_QUOTE,
    RULES,
    WINDOW_QUOTE,
    Counter,
    baobab_content,
    inputs,
    occ_content,
    rule_evidence,
    rule_moved,
    rule_window,
)

from mdeis.measure_build import engine

SCALE_QUOTE = "Score scale | 0 to 100, whole numbers"


def rule_scale(**over):
    return {
        "setting": "score_scale_maximum", "name": "score_scale", "value_type": "integer",
        "value": 100, "unit": "points", "quote": SCALE_QUOTE, **over,
    }  # fmt: skip


def settle(rules, content=None, section="6.4", allocate=None):
    return engine.settle_rules(inputs(content), RULES, section, rules, allocate or Counter())


def reasons(got):
    return {d["name"]: d["reason"] for d in got["dropped"]}


def test_the_three_baobab_rules_settle_into_two_reads_and_one_proposed_clause():
    got = settle([rule_moved(), rule_window(), rule_evidence()])
    assert got["status"] == "ok" and got["conflicts"] == [] and got["dropped"] == []
    reads = {r["key"]: r for r in got["reads"]}
    assert set(reads) == {"MP-1", "TR-1.window_terms", "TR-1.min_scores"}
    held = {k: (r["value_text"], r["source"]) for k, r in reads.items()}
    assert held == {
        "TR-1.window_terms": ("3", "template"),
        "TR-1.min_scores": ("2", "template"),
        "MP-1": ("10", "proposed"),
    }
    assert reads["TR-1.window_terms"]["clause_id"] == "TR-1"


def test_the_proposed_clause_is_a_measure_params_rule_nobody_has_confirmed():
    (mp,) = settle([rule_moved(), rule_window(), rule_evidence()])["mp_rules"]
    assert mp == {
        "id": "MP-1", "name": "moved_at_or_above", "value_type": "integer", "value": 10,
        "unit": "points", "source_document": "BAO-METH-1.0", "method_ref": "6.4",
        "confirmed_by": None, "text": MOVED_QUOTE,
    }  # fmt: skip


def test_a_value_that_has_a_home_never_becomes_a_second_clause():
    got = settle([rule_window(), rule_evidence()])
    assert got["mp_rules"] == []
    assert {r["key"] for r in got["reads"]} == {"TR-1.window_terms", "TR-1.min_scores"}


def test_a_home_is_found_by_the_name_even_when_the_model_says_other():
    got = settle([rule_window(setting="other", name="window_terms")])
    assert [r["key"] for r in got["reads"]] == ["TR-1.window_terms"] and got["mp_rules"] == []


def test_the_clause_numbers_come_from_the_allocator_and_only_for_kept_clauses():
    allocate = Counter(start=7)
    got = settle(
        [rule_moved(), rule_moved(name="other_one", quote="not in the section at all, no"),
         rule_window()],
        allocate=allocate,
    )  # fmt: skip
    assert [m["id"] for m in got["mp_rules"]] == ["MP-7"]
    assert allocate.n == 7, "one number asked for, for the one clause kept"
    assert "MP-7" in {r["key"] for r in got["reads"]}


def test_a_quote_that_is_not_in_the_section_drops_the_rule_and_no_clause_is_made():
    liar = rule_moved(
        quote="The pattern is moved up when the change is 12 points or more.", value=12
    )
    allocate = Counter()
    got = settle([liar], allocate=allocate)
    assert reasons(got) == {"moved_at_or_above": "quote_not_in_section"}
    assert got["mp_rules"] == [] and got["reads"] == [] and allocate.n == 0


def test_a_quote_that_is_in_another_section_not_pointed_at_is_not_accepted():
    # Section 5 says "Window: the last 3 term scores recorded in that subject"; 6.4 does not point
    # at section 5, so a quote from it is not "in the section".
    other = rule_window(quote="the last 3 term scores recorded in that subject, in date order")
    assert reasons(settle([other])) == {"window": "quote_not_in_section"}
    # but the same quote is accepted when the section that holds it is the one being built
    assert settle([other], section="5")["reads"][0]["key"] == "TR-1.window_terms"


def test_a_value_that_is_not_said_in_its_quote_is_dropped():
    wrong = rule_moved(value=12)  # the real quote says 10
    assert reasons(settle([wrong])) == {"moved_at_or_above": "value_not_in_quote"}


def test_a_quote_too_short_to_mean_anything_is_dropped():
    assert reasons(settle([rule_moved(quote="10 points", value=10)])) == {
        "moved_at_or_above": "quote_too_short"
    }


@pytest.mark.parametrize(
    "bad",
    [
        "not a rule",
        {"name": "x", "value_type": "integer", "value": 1, "unit": "points"},  # no quote
        {**rule_moved(), "value_type": "float"},
        {**rule_moved(), "name": ""},
        {**rule_moved(), "quote": 5},
    ],
)
def test_a_malformed_rule_is_dropped_by_name_and_the_rest_go_on(bad):
    got = settle([bad, rule_window()])
    assert [r["key"] for r in got["reads"]] == ["TR-1.window_terms"]
    assert len(got["dropped"]) == 1 and got["dropped"][0]["reason"] == "rule_malformed"


def test_more_rules_than_the_limit_are_dropped_past_the_limit():
    many = [rule_window()] * (RULES.max_rules_per_section + 1)
    got = settle(many)
    assert [d["reason"] for d in got["dropped"]].count("too_many_rules") == 1


def test_a_clause_the_template_validator_would_refuse_is_dropped_before_a_number_is_used():
    allocate = Counter()
    for bad in (rule_moved(unit="furlongs"), rule_moved(value=10.5), rule_moved(name="Moved Up")):
        got = settle([bad], allocate=allocate)
        assert reasons(got) == {bad["name"]: "rule_invalid"}, bad
    assert allocate.n == 0


def test_a_different_value_for_a_setting_that_has_a_home_stops_the_build_with_both_quoted():
    got = settle([rule_scale(), rule_evidence(), rule_window()], content=occ_content())
    assert got["status"] == "setting_conflict"
    found = {c["setting"]: c for c in got["conflicts"]}
    assert set(found) == {"score_scale_maximum", "minimum_evidence"}, "the window agrees (3 and 3)"
    scale = found["score_scale_maximum"]
    assert (scale["clause_id"], scale["field"]) == ("SC-1", "max")
    assert (scale["template_value"], scale["document_value"]) == ("20", "100")
    assert scale["quote"] == SCALE_QUOTE and scale["quote_section"] == "3.2"
    evidence = found["minimum_evidence"]
    assert (evidence["clause_id"], evidence["template_value"], evidence["document_value"]) == (
        "TR-1", "3", "2",
    )  # fmt: skip
    assert evidence["quote"] == EVIDENCE_QUOTE


def test_a_conflict_never_reads_the_other_value_and_never_makes_a_second_copy():
    got = settle([rule_scale()], content=occ_content())
    assert got["reads"] == [] and got["mp_rules"] == []


def test_the_same_value_with_the_baobab_template_is_not_a_conflict():
    got = settle([rule_scale(), rule_evidence(), rule_window()])
    assert got["status"] == "ok" and got["conflicts"] == []
    assert {r["key"] for r in got["reads"]} == {"SC-1.max", "TR-1.min_scores", "TR-1.window_terms"}


def test_a_home_the_template_has_no_field_for_becomes_a_clause():
    content = baobab_content()
    for rule in content["trend"]:
        rule.pop("window_terms", None)
        rule["window_terms"] = None  # no rule holds the window now
    got = settle([rule_window()], content=content)
    assert [m["name"] for m in got["mp_rules"]] == ["window"]
    assert got["reads"][0]["source"] == "proposed"


def test_two_rules_with_one_name_and_one_value_make_one_clause():
    got = settle([rule_moved(), rule_moved()])
    assert len(got["mp_rules"]) == 1
    assert [d["reason"] for d in got["dropped"]] == ["duplicate_of_a_rule_kept"]


def test_two_rules_with_one_name_and_two_values_stop_the_build():
    # The steady band row of section 3.2 says 8 points, not 10, but it is given the same name.
    eight = rule_moved(value=8, quote="Steady band | 8 points")
    got = settle([rule_moved(), eight])
    assert got["status"] == "setting_conflict"
    (conflict,) = got["conflicts"]
    assert conflict["name"] == "moved_at_or_above" and conflict["clause_id"] is None
    assert (conflict["template_value"], conflict["document_value"]) == ("10", "8")


def test_a_clause_already_in_the_active_template_from_this_document_is_read_not_made_again():
    content = baobab_content()
    content["measure_params"] = [{
        "id": "MP-4", "name": "moved_at_or_above", "value_type": "integer", "value": 10,
        "unit": "points", "source_document": "BAO-METH-1.0", "method_ref": "6.4",
        "confirmed_by": "Ms Teacher", "text": MOVED_QUOTE,
    }]  # fmt: skip
    got = settle([rule_moved()], content=content)
    assert got["mp_rules"] == [] and got["reads"][0]["key"] == "MP-4"
    assert got["reads"][0]["source"] == "existing"
    changed = copy.deepcopy(content)
    changed["measure_params"][0]["value"] = 9
    changed["measure_params"][0]["text"] = "The pattern is moved up at 9 points or more."
    again = settle([rule_moved()], content=changed)
    assert again["status"] == "setting_conflict"
    assert again["conflicts"][0]["clause_id"] == "MP-4"


def test_a_section_the_document_does_not_have_is_refused():
    got = settle([rule_moved()], section="99")
    assert got["status"] == "refused"


def test_settling_changes_nothing_it_was_given():
    given = inputs()
    before = copy.deepcopy(given.content)
    rules = [rule_moved(), rule_window(), rule_evidence()]
    first = engine.settle_rules(given, RULES, "6.4", copy.deepcopy(rules), Counter())
    second = engine.settle_rules(given, RULES, "6.4", copy.deepcopy(rules), Counter())
    assert first == second and given.content == before
    assert WINDOW_QUOTE in {q["quote"] for q in first["rules_quoted"]}
