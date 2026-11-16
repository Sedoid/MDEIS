"""The class baseline's pure core (mdeis.core.class_baseline). No database, no names.

Every expected value is worked out by hand from templates/v1.1.yaml and templates/v2.1-strict.yaml
(v1.1: group minimum 10, smallest count shown 5; v2.1-strict: 15 and 10; both PLACEHOLDER values).
Where a test needs a different minimum it builds a second `Rules` with `dataclasses.replace`, so no
literal in the code can be acting as the rule.
"""

import ast
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from mdeis.core import class_baseline as cb
from mdeis.core.rules import rules_from_content
from mdeis.core.template import load_template
from mdeis.core.trend import RulebookGap

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "templates"
FIVE = (
    "below_expected_range",
    "within_expected_range",
    "above_expected_range",
    "not_enough_evidence",
    "blocked",
)
BELOW, WITHIN, ABOVE, NOT_ENOUGH, BLOCKED = FIVE


def rules_of(name):
    template = load_template(TEMPLATES / f"{name}.yaml")
    return rules_from_content(template.model_dump(mode="json"), name, "0" * 64)


@pytest.fixture(scope="module")
def v11():
    return rules_of("v1.1")


@pytest.fixture(scope="module")
def v21():
    return rules_of("v2.1-strict")


def counts(*five):
    return dict(zip(FIVE, five, strict=True))


def cohort(literacy, numeracy):
    """One placement per learner, built from five counts per area (in the order of FIVE)."""
    n = sum(literacy)
    assert n == sum(numeracy)
    lit = [b for b, c in zip(FIVE, literacy, strict=True) for _ in range(c)]
    num = [b for b, c in zip(FIVE, numeracy, strict=True) for _ in range(c)]
    return [{"literacy": a, "numeracy": b} for a, b in zip(lit, num, strict=True)]


def NO_LITERACY(n):  # noqa: N802  every learner of the group is short of evidence in literacy
    return (0, 0, 0, n, 0)


def build(rules, literacy, numeracy, **kw):
    args = {
        "school": "OCC", "stage": "Form 1", "term": 3, "year": "2025/2026",
        "placements": cohort(literacy, numeracy), "rules": rules,
    }  # fmt: skip
    return cb.build_result(**{**args, **kw})


def numeracy_of(result):
    return next(a for a in result["areas"] if a["area"] == "numeracy")


def hidden_of(area):
    return [b["bin"] for b in area["bins"] if not b["shown"]]


def shown_of(area):
    return {b["bin"]: b["count"] for b in area["bins"] if b["shown"]}


# --------------------------------------------------------------------------- the five bins
@pytest.mark.parametrize(
    "result,expected",
    [
        ({"status": "blocked"}, BLOCKED),
        ({"status": "ok", "trend": "not_enough_evidence", "stage_gap": None}, NOT_ENOUGH),
        (
            {"status": "ok", "trend": "steady", "stage_gap": {"level_name": BELOW}},
            BELOW,
        ),
        ({"status": "ok", "trend": "steady", "stage_gap": {"level_name": WITHIN}}, WITHIN),
        ({"status": "ok", "trend": "improving", "stage_gap": {"level_name": ABOVE}}, ABOVE),
    ],
    ids=["blocked", "not enough", "below", "within", "above"],
)
def test_each_outcome_of_the_trend_code_goes_in_its_own_bin(result, expected):
    assert cb.bin_of(result) == expected


def test_a_result_with_a_level_the_code_does_not_know_is_refused():
    odd = {"status": "ok", "trend": "steady", "stage_gap": {"level_name": "excellent"}}
    with pytest.raises(cb.ClassBaselineError, match="class_rules_unknown"):
        cb.bin_of(odd)


def test_blocked_wins_over_whatever_else_the_result_holds():
    assert cb.bin_of({"status": "blocked", "trend": "not_enough_evidence"}) == BLOCKED


# ------------------------------------------------------------------ what is hidden (GM-2, GM-3)
def test_a_count_of_zero_is_shown_and_a_count_below_the_cell_size_is_hidden(v11):
    # v1.1: the smallest count shown is 5. 0 is shown, 4 is hidden, 5 is shown.
    assert cb.hidden_bins(counts(15, 5, 0, 0, 0), v11) == []
    assert cb.hidden_bins(counts(0, 0, 0, 20, 0), v11) == []
    # 4 is hidden; the other shown counts are 16 only, so 16 is hidden with it.
    assert cb.hidden_bins(counts(4, 16, 0, 0, 0), v11) == [BELOW, WITHIN]


def test_the_cell_size_is_read_from_the_rules_not_written_in_the_code(v11):
    same = counts(5, 15, 0, 0, 0)
    assert cb.hidden_bins(same, v11) == []
    stricter = replace(v11, min_cell=10)
    assert cb.hidden_bins(same, stricter) == [BELOW, WITHIN], "5 is now small; 15 goes with it"
    looser = replace(v11, min_cell=1)
    assert cb.hidden_bins(counts(1, 19, 0, 0, 0), looser) == []


def test_one_hidden_count_also_hides_the_smallest_other_count_above_zero(v11):
    # Group 20, counts 3, 9, 8, 0, 0. 3 is hidden. The others above 0 are 9 and 8: 8 goes too.
    # Two zeros are smaller, and are never chosen. Shown: within 9, and the zeros.
    assert cb.hidden_bins(counts(3, 9, 8, 0, 0), v11) == [BELOW, ABOVE]


def test_a_tie_for_the_smallest_goes_to_the_one_first_in_the_template_order(v11):
    # Group 40, counts 12, 12, 3, 13, 0. Above (3) is hidden. 12 and 12 tie as the smallest
    # shown above 0: below comes first in the order, so below is hidden. The 0 is not chosen.
    assert cb.hidden_bins(counts(12, 12, 3, 13, 0), v11) == [BELOW, ABOVE]


def test_the_order_in_the_template_decides_a_tie(v11):
    # Order reversed: blocked, not enough, above, within, below. Above (3) is hidden. Below and
    # within tie at 12; within now comes first in the order, so within is hidden. The answer lists
    # the hidden counts in the template's order: above, then within.
    flipped = replace(v11, bin_order=list(reversed(FIVE)))
    assert cb.hidden_bins(counts(12, 12, 3, 13, 0), flipped) == [ABOVE, WITHIN]


def test_a_zero_is_never_the_one_chosen(v11):
    # Group 12, counts 3, 9, 0, 0, 0. The only other count above 0 is 9, so 9 is hidden, not a 0.
    assert cb.hidden_bins(counts(3, 9, 0, 0, 0), v11) == [BELOW, WITHIN]
    # With a smallest-shown size of 3: counts 2, 0, 0, 0, 5. 2 is hidden; 5 is the only other
    # count above 0, so 5 goes with it (blocked), and not any of the three zeros.
    assert cb.hidden_bins(counts(2, 0, 0, 0, 5), replace(v11, min_cell=3)) == [BELOW, BLOCKED]


def test_no_extra_count_is_hidden_when_none_is_hidden_or_when_two_already_are(v11):
    assert cb.hidden_bins(counts(10, 5, 5, 0, 0), v11) == []
    # Two small counts: 2 and 3 are hidden together; 15 is not, and no third is added.
    assert cb.hidden_bins(counts(2, 3, 15, 0, 0), v11) == [BELOW, WITHIN]
    # Three small counts: all three, and no fourth.
    assert cb.hidden_bins(counts(1, 2, 3, 14, 0), v11) == [BELOW, WITHIN, ABOVE]


def test_the_extra_count_is_chosen_from_the_counts_still_shown(v11):
    # Group 22: 6, 6, 6, 4, 0. The 4 (not enough evidence) is hidden. The others above 0 are
    # 6, 6 and 6, a three-way tie, which goes to the first in the order: below.
    assert cb.hidden_bins(counts(6, 6, 6, 4, 0), v11) == [BELOW, NOT_ENOUGH]


def test_a_class_with_no_other_count_to_hide_with_is_refused_not_shown_unprotected(v11):
    # A cell size of 50 with one bin holding all 20 learners: the count is hidden and nothing else
    # above 0 is left to hide with it. That cannot happen under a valid template (the loader
    # refuses a cell size larger than the group minimum), and the code refuses rather than guess.
    with pytest.raises(cb.ClassBaselineError):
        cb.hidden_bins(counts(20, 0, 0, 0, 0), replace(v11, min_cell=50))


# ------------------------------------------------------------------- the group minimum (GM-1)
def test_a_group_of_exactly_the_minimum_is_shown_and_one_fewer_is_not_under_v1_1(v11):
    shown = build(v11, NO_LITERACY(10), (4, 6, 0, 0, 0))
    assert shown["status"] == "ok" and shown["group_size"] == 10
    small = build(v11, NO_LITERACY(9), (4, 5, 0, 0, 0))
    assert small["status"] == "suppressed"


def test_a_group_of_exactly_the_minimum_is_shown_and_one_fewer_is_not_under_v2_1_strict(v21):
    assert build(v21, NO_LITERACY(15), (0, 15, 0, 0, 0))["status"] == "ok"
    assert build(v21, NO_LITERACY(14), (0, 14, 0, 0, 0))["status"] == "suppressed"


def test_a_suppressed_answer_gives_no_group_size_no_counts_and_no_areas(v11):
    result = build(v11, NO_LITERACY(9), (4, 5, 0, 0, 0))
    assert set(result) == {
        "school_id", "stage", "term", "academic_year", "template_version", "minimum_group_size",
        "status", "reason", "clause_ids", "result_hash", "finding_id",
    }  # fmt: skip
    assert result["clause_ids"] == ["GM-1"]
    assert result["minimum_group_size"] == 10
    assert result["reason"] == "the group is smaller than the minimum that may be shown"


def test_an_empty_class_is_suppressed_in_the_same_way_as_a_small_one(v11):
    empty = cb.build_result(
        school="OCC", stage="Form 1", term=3, year=None, placements=[], rules=v11
    )
    small = build(v11, NO_LITERACY(3), (1, 2, 0, 0, 0))
    assert empty["status"] == small["status"] == "suppressed"
    assert set(empty) == set(small)
    assert "group_size" not in empty


# --------------------------------------------------------------------------- the answer for a class
def test_a_class_of_twelve_worked_by_hand(v11):
    # Group 12. Literacy: all 12 short of evidence. Numeracy: 3 below, 9 within, none else.
    # 3 is hidden (more than 0, fewer than 5). The only other count above 0 is 9: hidden too.
    result = build(v11, NO_LITERACY(12), (3, 9, 0, 0, 0))
    assert result["status"] == "ok" and result["group_size"] == 12
    assert result["school_id"] == "OCC" and result["stage"] == "Form 1"
    assert result["term"] == 3 and result["academic_year"] == "2025/2026"
    assert result["template_version"] == "v1.1"
    assert (result["minimum_group_size"], result["minimum_cell_size"]) == (10, 5)
    literacy, numeracy = result["areas"]
    assert literacy["area"] == "literacy" and literacy["status"] == "no_usable_evidence"
    assert "bins" not in literacy
    assert literacy["coverage"] == {"with_enough_evidence": 0, "group_size": 12}
    assert literacy["statement"].startswith("There are too few term scores")
    assert literacy["clause_ids"] == ["SK-1", "TR-1", "LV-4", "GM-1"]
    assert numeracy["status"] == "ok"
    assert [b["bin"] for b in numeracy["bins"]] == list(FIVE)
    assert hidden_of(numeracy) == [BELOW, WITHIN]
    assert shown_of(numeracy) == {ABOVE: 0, NOT_ENOUGH: 0, BLOCKED: 0}
    assert numeracy["suppressed"] is True
    assert numeracy["coverage"] is None, "coverage is withheld whenever a count is hidden"
    assert numeracy["suppression"]["clause_ids"] == ["GM-2", "GM-3"]


def test_a_class_with_nothing_hidden_shows_five_counts_and_coverage(v11):
    # Group 20. Numeracy: 6 below, 8 within, 6 above, none short, none blocked. All are 5 or more.
    result = build(v11, NO_LITERACY(20), (6, 8, 6, 0, 0))
    numeracy = numeracy_of(result)
    assert shown_of(numeracy) == {BELOW: 6, WITHIN: 8, ABOVE: 6, NOT_ENOUGH: 0, BLOCKED: 0}
    assert hidden_of(numeracy) == [] and numeracy["suppressed"] is False
    assert numeracy["suppression"] is None
    assert numeracy["coverage"] == {"with_enough_evidence": 20, "group_size": 20}


def test_coverage_counts_the_learners_placed_below_within_or_above(v11):
    # Group 20: 5 below, 5 within, 5 above, 5 short of evidence, none blocked. Nothing is hidden
    # (5 is not fewer than 5). 15 learners have enough evidence.
    result = build(v11, NO_LITERACY(20), (5, 5, 5, 5, 0))
    assert numeracy_of(result)["coverage"] == {"with_enough_evidence": 15, "group_size": 20}


def test_coverage_is_withheld_when_a_count_is_hidden_so_it_cannot_give_it_away(v11):
    # Group 30: 0 below, 10 within, 10 above, 6 short of evidence, 4 blocked. Only the 4 is small.
    # Coverage would be 30 minus (6 + 4): a second total from which the 4 could be found. So the
    # 4 is hidden, the 6 (the smallest other count above 0) goes with it, and coverage is withheld.
    area = numeracy_of(build(v11, NO_LITERACY(30), (0, 10, 10, 6, 4)))
    assert hidden_of(area) == [NOT_ENOUGH, BLOCKED]
    assert area["coverage"] is None
    assert area["suppressed"] is True


def test_the_hidden_count_itself_is_in_no_field_of_the_answer(v11):
    result = build(v11, NO_LITERACY(20), (3, 9, 8, 0, 0))
    area = numeracy_of(result)
    assert hidden_of(area) == [BELOW, ABOVE]
    for entry in area["bins"]:
        if not entry["shown"]:
            assert entry["count"] is None
    assert [e["count"] for e in area["bins"] if e["shown"]] == [9, 0, 0]


def test_the_reason_is_the_same_whichever_counts_were_hidden(v11):
    a = numeracy_of(build(v11, NO_LITERACY(20), (3, 9, 8, 0, 0)))["suppression"]
    b = numeracy_of(build(v11, NO_LITERACY(20), (0, 9, 8, 0, 3)))["suppression"]
    assert a == b
    assert not re.search(r"[0-9]", a["reason"]), "the reason names no number"
    assert a["reason"].endswith("No hidden count is given.")


def test_two_classes_that_differ_only_in_what_is_hidden_give_the_same_answer_and_hash(v11):
    # Numeracy 3, 9, 8 and 4, 9, 7: below and above are hidden in both, the shown 9 and the zeros
    # are the same, and the group is 20 in both. Nothing in the answer can tell them apart.
    first = build(v11, NO_LITERACY(20), (3, 9, 8, 0, 0))
    second = build(v11, NO_LITERACY(20), (4, 9, 7, 0, 0))
    assert first == second
    assert first["result_hash"] == second["result_hash"]
    third = build(v11, NO_LITERACY(20), (3, 10, 7, 0, 0))
    assert third["result_hash"] != first["result_hash"], "a shown count changes the hash"


def test_the_rules_decide_what_is_hidden_so_another_rulebook_gives_another_answer(v11, v21):
    # Same 20 learners, numeracy 6, 8, 6. Under v1.1 (smallest count shown 5) all three are shown.
    # Under v2.1-strict (10) the group of 20 is shown (the minimum is 15), but 6, 8 and 6 are each
    # fewer than 10, so all three are hidden.
    under_v11 = numeracy_of(build(v11, NO_LITERACY(20), (6, 8, 6, 0, 0)))
    under_v21 = numeracy_of(build(v21, NO_LITERACY(20), (6, 8, 6, 0, 0)))
    assert hidden_of(under_v11) == []
    assert hidden_of(under_v21) == [BELOW, WITHIN, ABOVE]


def test_the_template_order_of_the_five_counts_is_the_order_shown(v11):
    flipped = replace(v11, bin_order=list(reversed(FIVE)))
    area = numeracy_of(build(flipped, NO_LITERACY(20), (6, 8, 6, 0, 0)))
    assert [b["bin"] for b in area["bins"]] == list(reversed(FIVE))


# ------------------------------------------------------------------ areas, clauses, findings
def test_foundation_areas_come_first_in_the_templates_order_then_the_others(v11):
    result = build(v11, NO_LITERACY(12), (0, 12, 0, 0, 0))
    assert [(a["area"], a["foundation"]) for a in result["areas"]] == [
        ("literacy", True),
        ("numeracy", True),
    ]
    # A rulebook where numeracy is the only foundation area and literacy an extra one.
    odd = replace(v11, foundation_areas=["numeracy"])
    assert cb.area_names(odd) == ["numeracy", "literacy"]
    result = build(odd, NO_LITERACY(12), (0, 12, 0, 0, 0))
    assert [(a["area"], a["foundation"]) for a in result["areas"]] == [
        ("numeracy", True),
        ("literacy", False),
    ]
    both = replace(v11, foundation_areas=["numeracy", "literacy"])
    assert cb.area_names(both) == ["numeracy", "literacy"], "the template's own order"


def test_an_area_with_evidence_cites_its_area_the_evidence_rule_the_range_and_the_group_rules(v11):
    area = numeracy_of(build(v11, NO_LITERACY(20), (6, 8, 6, 0, 0)))
    assert area["clause_ids"] == [
        "SK-2", "TR-1", "TR-3", "ST-4", "CA-5", "LV-1", "LV-2", "LV-3", "GM-1", "GM-2", "GM-3",
    ]  # fmt: skip
    top = build(v11, NO_LITERACY(20), (6, 8, 6, 0, 0))
    assert top["clause_ids"] == ["GM-1", "GM-2", "GM-3"]
    assert cb.all_clause_ids(top)[:3] == ["GM-1", "GM-2", "GM-3"]
    assert {"SK-1", "LV-4", "ST-4"} <= set(cb.all_clause_ids(top))


def test_the_range_cited_is_the_one_for_the_stage_asked_about(v11):
    form3 = build(v11, NO_LITERACY(20), (6, 8, 6, 0, 0), stage="Form 3")
    assert "ST-6" in numeracy_of(form3)["clause_ids"]  # Form 3 numeracy
    assert "ST-4" not in numeracy_of(form3)["clause_ids"]


def test_a_stage_with_no_range_for_an_area_refuses_the_whole_answer(v11):
    with pytest.raises(RulebookGap, match="Form 9 literacy"):
        build(v11, NO_LITERACY(20), (6, 8, 6, 0, 0), stage="Form 9")
    gap = replace(v11, ranges={k: v for k, v in v11.ranges.items() if k != ("Form 1", "numeracy")})
    with pytest.raises(RulebookGap, match="Form 1 numeracy"):
        build(gap, NO_LITERACY(20), (6, 8, 6, 0, 0))


def test_every_area_and_the_whole_answer_carry_a_finding_number_from_their_own_hash(v11):
    result = build(v11, NO_LITERACY(20), (6, 8, 6, 0, 0))
    for block in (result, *result["areas"]):
        assert re.fullmatch(r"[0-9a-f]{64}", block["result_hash"])
        assert re.fullmatch(r"CF-[0-9a-f]{8}", block["finding_id"])
        assert block["finding_id"] == "CF-" + block["result_hash"][:8]
    ids = {result["finding_id"], *(a["finding_id"] for a in result["areas"])}
    assert len(ids) == 3, "the two areas and the whole answer each have their own"


def test_the_same_input_gives_the_same_answer_every_time(v11):
    first = build(v11, NO_LITERACY(20), (6, 8, 6, 0, 0))
    again = build(v11, NO_LITERACY(20), (6, 8, 6, 0, 0))
    assert first == again and json.dumps(first) == json.dumps(again)
    other_school = build(v11, NO_LITERACY(20), (6, 8, 6, 0, 0), school="ZZZ")
    assert other_school["result_hash"] != first["result_hash"]
    other_term = build(v11, NO_LITERACY(20), (6, 8, 6, 0, 0), term=2)
    assert other_term["result_hash"] != first["result_hash"]


def test_the_hash_is_worked_out_the_same_way_as_every_other_result():
    from mdeis.tools._shared import with_hash

    content = {"b": [1, 2, {"x": None}], "a": "text"}
    assert cb.digest(content) == with_hash(dict(content))["result_hash"]


# ------------------------------------------------------------------ nothing about a person
def test_the_answer_holds_no_name_no_learner_number_and_no_record_number(v11):
    result = build(v11, NO_LITERACY(20), (3, 9, 8, 0, 0))
    text = json.dumps(result)
    assert not re.search(r"\bL-\d", text), "no learner number"
    assert not re.search(r"\bA-\d", text), "no record number"
    assert not re.search(r"[A-Z]{2,}/\d{4}/\d+", text), "no school reference"
    assert "learner_id" not in text and "display_name" not in text and "record_id" not in text


def test_the_key_set_of_an_answer_is_fixed(v11):
    result = build(v11, NO_LITERACY(20), (6, 8, 6, 0, 0))
    assert set(result) == {
        "school_id", "stage", "term", "academic_year", "template_version", "minimum_group_size",
        "minimum_cell_size", "status", "group_size", "areas", "clause_ids", "result_hash",
        "finding_id",
    }  # fmt: skip
    ok, short = result["areas"][1], result["areas"][0]
    assert set(ok) == {
        "area", "foundation", "status", "bins", "coverage", "suppressed", "suppression",
        "clause_ids", "result_hash", "finding_id",
    }  # fmt: skip
    assert set(short) == {
        "area", "foundation", "status", "statement", "coverage", "suppressed", "suppression",
        "clause_ids", "result_hash", "finding_id",
    }  # fmt: skip
    assert all(set(b) == {"bin", "shown", "count"} for b in ok["bins"])


def test_no_label_ranking_or_pathway_word_is_in_the_text_the_code_adds(v11):
    banned = [*v11.banned_words, "rank", "best", "worst", "top", "bottom", "track", "pathway"]
    texts = [cb.REASON_GROUP, cb.REASON_HIDDEN]
    for text in texts:
        words = set(re.findall(r"[a-z]+", text.lower()))
        assert not words & set(banned), text


# ------------------------------------------------------------------ the rulebook has the rules
@pytest.mark.parametrize("name", ["v1", "v2-strict"])
def test_a_rulebook_without_the_class_view_rules_is_refused_with_a_typed_reason(name):
    old = rules_of(name)
    with pytest.raises(cb.ClassBaselineError, match="class_rules_missing"):
        build(old, NO_LITERACY(20), (6, 8, 6, 0, 0))


def test_a_rulebook_with_a_hiding_rule_the_code_does_not_know_is_refused(v11):
    for broken in (
        replace(v11, complementary="hide_the_largest"),
        replace(v11, coverage_when_hidden="shown"),
        replace(v11, bin_order=list(FIVE[:-1])),
        replace(v11, min_cell=v11.min_group + 1),
    ):
        with pytest.raises(cb.ClassBaselineError, match="class_rules_unknown"):
            build(broken, NO_LITERACY(20), (6, 8, 6, 0, 0))


def test_the_refusal_is_a_rulebook_problem_so_the_tool_wrapper_logs_it_as_refused():
    from mdeis.core.rules import RulebookProblem

    assert issubclass(cb.ClassBaselineError, RulebookProblem)


# ------------------------------------------------------------------ no school number in the code
def test_the_core_holds_no_number_other_than_0_and_1_and_the_finding_number_length():
    tree = ast.parse((ROOT / "src" / "mdeis" / "core" / "class_baseline.py").read_text("utf-8"))
    named = {
        id(n)
        for stmt in tree.body
        if isinstance(stmt, ast.Assign)
        and any(getattr(t, "id", "") == "FINDING_HEX" for t in stmt.targets)
        for n in ast.walk(stmt)
    }
    found = {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, (int, float))
        and not isinstance(n.value, bool)
        and id(n) not in named
    }
    assert found <= {0, 1}, f"numbers in the core: {sorted(found - {0, 1})}"
    assert not any(isinstance(n, ast.Constant) and n.value == "OCC" for n in ast.walk(tree))
