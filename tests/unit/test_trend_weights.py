"""A stage the rulebook has no weight for. Expected values are worked out by hand from
templates/v1.yaml: scale out of 20, three scores needed (TR-1), the window is the last three terms,
a move is more than 1 point (TR-2), stage weights 1, 1, 2, 2, 2 for Form 1 to Form 5 (SW-1), and no
weight at all for "Form 5 Science"."""

from fractions import Fraction
from pathlib import Path

from mdeis.core import trend
from mdeis.core.rules import rules_from_content
from mdeis.core.template import load_template

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"
V1 = rules_from_content(
    load_template(TEMPLATES / "v1.yaml").model_dump(mode="json"), "v1", "0" * 64
)


def record(number, year, term, stage, score):
    return {
        "record_id": f"A-{number:06d}", "year": year, "term": term, "stage": stage,
        "subject": "Mathematics", "score": Fraction(score), "denominator": Fraction(20),
    }


def math(records):
    return trend.subject_result(records, "Mathematics", V1, set())


def test_a_window_inside_one_stage_needs_no_weight_and_assumes_nothing():
    unknown = [record(n, "2026/2027", n, "Form 5 Science", s)
               for n, s in ((1, "17.8"), (2, "18.0"), (3, "18.2"))]
    result = math(unknown)
    assert (result["level"], result["change"], result["trend"]) == ("18.00", "0.40", "steady")
    assert "assumed" not in result, "one weight on every term cancels out: nothing is assumed"


def test_a_window_across_stages_counts_the_unweighted_stage_once_and_says_so():
    mixed = [record(1, "2025/2026", 3, "Form 4", 16),
             record(2, "2026/2027", 1, "Form 5 Science", 18),
             record(3, "2026/2027", 2, "Form 5 Science", 18)]
    result = math(mixed)
    # Form 4 counts twice (SW-1), the unweighted stage once: (2 x 16 + 1 x 18 + 1 x 18) / 4 = 17
    assert (result["level"], result["change"], result["trend"]) == ("17.00", "2.00", "improving")
    assert result["assumed"] == [
        {"what": "stage weight", "stage": "Form 5 Science", "used": "1.00"}
    ]
    assert "SW-1" in result["clause_ids"]


def test_the_rulebooks_own_weights_are_used_where_it_has_them_and_nothing_is_assumed():
    known = [record(1, "2023/2024", 3, "Form 2", 14),
             record(2, "2024/2025", 1, "Form 3", 16),
             record(3, "2024/2025", 2, "Form 3", 16)]
    result = math(known)
    # Form 2 counts once, Form 3 twice: (1 x 14 + 2 x 16 + 2 x 16) / 5 = 15.60
    assert result["level"] == "15.60" and "assumed" not in result


def test_an_unweighted_stage_listed_once_even_when_it_has_several_terms():
    mixed = [record(1, "2025/2026", 3, "Form 4", 16),
             record(2, "2026/2027", 1, "Form 5 Science", 18),
             record(3, "2026/2027", 2, "Form 5 Science", 18)]
    assert len(math(mixed)["assumed"]) == 1


def test_the_weights_function_gives_unit_weight_and_names_the_assumed_stages():
    weights, assumed = trend.window_weights(V1, ["Form 3", "Form 6"])
    assert weights == [Fraction(2), Fraction(1)] and assumed == ["Form 6"]
    weights, assumed = trend.window_weights(V1, ["Form 6", "Form 6"])
    assert weights == [Fraction(1), Fraction(1)] and assumed == []
