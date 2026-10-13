"""What a chart shows, worked out from records and a rulebook (E-23). Expected values are typed by
hand from templates/v1.yaml: scale out of 20, three scores needed (TR-1), numeracy is Mathematics
alone, Form 1 numeracy is expected between 10 and 14 (ST-4) and Form 2 between 11 and 15 (ST-5)."""

from fractions import Fraction
from pathlib import Path

import pytest

from mdeis.core import chartdata
from mdeis.core.rules import rules_from_content
from mdeis.core.template import load_template

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"
V1 = rules_from_content(
    load_template(TEMPLATES / "v1.yaml").model_dump(mode="json"), "v1", "0" * 64
)


def record(number, year, term, stage, subject, score):
    return {
        "record_id": f"A-{number:06d}", "year": year, "term": term, "stage": stage,
        "subject": subject, "score": None if score is None else Fraction(score),
        "denominator": Fraction(20),
    }


RECORDS = [
    record(1, "2024/2025", 1, "Form 1", "Mathematics", 12),
    record(2, "2024/2025", 2, "Form 1", "Mathematics", 13),
    record(3, "2024/2025", 3, "Form 1", "Mathematics", 9),
    record(4, "2025/2026", 1, "Form 2", "Mathematics", 14),
    record(5, "2025/2026", 2, "Form 2", "Mathematics", None),  # a blank term
    record(6, "2025/2026", 3, "Form 2", "Mathematics", 16),
    record(7, "2024/2025", 1, "Form 1", "Chemistry", 8),
    record(8, "2024/2025", 2, "Form 1", "Chemistry", 10),
    record(9, "2024/2025", 3, "Form 1", "Chemistry", 11),
    record(10, "2024/2025", 1, "Form 1", "Physics", 15),
    record(11, "2024/2025", 2, "Form 1", "Physics", 16),
]


def chart(skills, blocked=()):
    return chartdata.chart_data(RECORDS, V1, skills, set(blocked))


def test_one_subject_over_the_years_leaves_the_blank_term_out_and_draws_no_band():
    data = chart(["Mathematics"])
    assert [t["label"] for t in data["terms"]] == ["T1", "T2", "T3", "T1", "T3"]
    groups = ["2024/25 · Form 1"] * 3 + ["2025/26 · Form 2"] * 2
    assert [t["group"] for t in data["terms"]] == groups
    (line,) = data["series"]
    assert line["name"] == "Mathematics"
    assert line["values"] == ["12.00", "13.00", "9.00", "14.00", "16.00"]
    assert [ids for ids in line["record_ids"]] == [["A-000001"], ["A-000002"], ["A-000003"],
                                                    ["A-000004"], ["A-000006"]]
    assert all(t["band"] is None for t in data["terms"]) and data["gap"] is None
    assert data["band_clauses"] == []
    assert data["scale_max"] == "20.00" and data["excluded"] == []


def test_two_subjects_share_one_axis_and_a_missing_term_is_a_gap_not_a_zero():
    data = chart(["Mathematics", "Chemistry"])
    assert [s["name"] for s in data["series"]] == ["Mathematics", "Chemistry"]
    assert data["series"][1]["values"] == ["8.00", "10.00", "11.00", None, None]
    assert data["series"][1]["record_ids"][3] == []
    assert all(t["band"] is None for t in data["terms"])  # no single range fits two subjects
    assert data["gap"] is None


def test_a_learning_area_gets_the_expected_range_of_each_stage_and_the_last_gap():
    data = chart(["numeracy"])
    (line,) = data["series"]
    assert line["values"] == ["12.00", "13.00", "9.00", "14.00", "16.00"]
    assert [t["band"] for t in data["terms"]] == (
        [["10.00", "14.00"]] * 3 + [["11.00", "15.00"]] * 2
    )
    assert data["gap"] == {"index": 4, "from_value": "16.00", "to_value": "15.00",
                           "text": "1.00 above"}
    assert data["band_clauses"] == ["ST-4", "ST-5"]
    assert {"ST-4", "ST-5", "SK-2", "TR-1"} <= set(data["clause_ids"])
    assert data["record_ids"] == sorted(data["record_ids"]) and "A-000006" in data["record_ids"]


def test_no_gap_is_drawn_when_the_last_score_is_inside_the_range():
    inside = [r for r in RECORDS if r["record_id"] != "A-000006"]
    data = chartdata.chart_data(inside, V1, ["numeracy"], set())
    assert data["series"][0]["values"][-1] == "14.00"  # Form 2 range is 11 to 15
    assert data["gap"] is None


def test_a_gap_below_the_range_says_below():
    low = [r for r in RECORDS if r["record_id"] not in ("A-000004", "A-000006")]
    low.append(record(20, "2025/2026", 1, "Form 2", "Mathematics", 9))
    data = chartdata.chart_data(low, V1, ["numeracy"], set())
    assert data["gap"]["text"] == "2.00 below"
    assert (data["gap"]["from_value"], data["gap"]["to_value"]) == ("9.00", "11.00")


def test_too_few_scores_means_no_line_and_a_reason():
    data = chart(["Physics"])
    assert data["series"] == [] and data["terms"] == []
    assert data["excluded"] == [{"skill": "Physics", "reason": "not_enough_evidence",
                                 "scores_available": 2, "scores_needed": 3}]


def test_a_subject_with_an_open_problem_is_left_out_and_the_others_still_draw():
    data = chart(["Mathematics", "Chemistry"], blocked={"Chemistry"})
    assert [s["name"] for s in data["series"]] == ["Mathematics"]
    assert data["excluded"] == [{"skill": "Chemistry", "reason": "blocked"}]


def test_an_area_with_a_blocked_subject_is_blocked():
    data = chart(["numeracy"], blocked={"Mathematics"})
    assert data["series"] == [] and data["excluded"][0]["reason"] == "blocked"


def test_an_unknown_name_and_too_many_lines_are_refused():
    with pytest.raises(chartdata.UnknownSkill):
        chart(["Astrology"])
    with pytest.raises(chartdata.TooManyLines):
        chart(["Mathematics"] * 1 + [f"Subject {n}" for n in range(chartdata.LINE_CAP)])
    with pytest.raises(chartdata.NothingAsked):
        chart([])


def test_the_same_name_asked_twice_is_drawn_once():
    assert len(chart(["Mathematics", "Mathematics"])["series"]) == 1


def test_the_scale_comes_from_the_rulebook():
    assert chart(["Mathematics"])["scale_max"] == "20.00"
