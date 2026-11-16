"""evidence_sentence: composed by code from the class finding and the template (T-060, slice 2).

E-50: the sentence equals the hand-worked text for a given class result. The expected strings are
typed here from the inline test rules in tests/unit/lessonkit.py (the level statements, the clause
ids and the group-minimum text are the ones written there).
"""

import copy
import re

import pytest
from lessonkit import BINS, FINDING_ID, VERSION, finding, finding_mapping, rules, with_below

from mdeis.lessons.evidence import (
    EvidenceError,
    evidence_sentence,
    no_gap_sentence,
    not_enough_evidence_sentence,
)
from mdeis.lessons.findings import FindingError, finding_from_mapping, findings_from_class_result

BELOW = (
    "Evidence (literacy, Form 3, term 2): 14 of 22 learners. The average of the last 3 term "
    "scores is below the expected range for the stage. "
    f"[LV-1, ST-3, GM-1, template {VERSION}, {FINDING_ID}]"
)
WITHIN = BELOW.replace("14 of", "6 of").replace("below", "within").replace("LV-1", "LV-2")
ABOVE = BELOW.replace("14 of", "2 of").replace("below", "above").replace("LV-1", "LV-3")


def test_the_sentence_for_the_hand_worked_class_is_exactly_this_text():
    assert evidence_sentence(finding(), rules()) == "\n".join([BELOW, WITHIN, ABOVE])


def test_a_bin_with_a_count_of_zero_or_a_hidden_count_gets_no_line():
    bins = copy.deepcopy(BINS)
    bins[1] = {"bin": "within_expected_range", "shown": False, "count": None}
    text = evidence_sentence(finding(bins=bins), rules())
    assert text == "\n".join([BELOW, ABOVE])
    assert "not_enough_evidence" not in text and "0 of" not in text


def test_two_bins_give_two_lines_in_the_order_of_the_template_not_of_the_finding():
    bins = [
        {"bin": "above_expected_range", "shown": True, "count": 2},
        {"bin": "below_expected_range", "shown": True, "count": 14},
        {"bin": "within_expected_range", "shown": True, "count": 0},
    ]
    text = evidence_sentence(finding(bins=bins), rules())
    assert text == "\n".join([BELOW, ABOVE])


def test_the_numbers_are_printed_from_the_finding_and_change_with_it():
    text = evidence_sentence(with_below(9, group_size=30), rules())
    assert text.splitlines()[0].startswith("Evidence (literacy, Form 3, term 2): 9 of 30 learners.")


def test_the_wording_comes_from_the_template_a_changed_statement_changes_the_sentence():
    def change(c):
        c["levels"][0]["statement"] = "The class average is under the expected range."
        c["group_minimum"][0]["id"] = "GM-5"

    text = evidence_sentence(finding(), rules(change))
    assert text.splitlines()[0] == (
        "Evidence (literacy, Form 3, term 2): 14 of 22 learners. "
        "The class average is under the expected range. "
        f"[LV-1, ST-3, GM-5, template {VERSION}, {FINDING_ID}]"
    )


def test_a_bin_may_name_its_level_clause_when_its_own_name_is_not_the_levels_name():
    bins = [
        {"bin": "not_enough_evidence", "shown": True, "count": 3, "level_clause": "LV-4"},
        {"bin": "below_expected_range", "shown": True, "count": 14},
    ]
    lines = evidence_sentence(finding(bins=bins, group_size=17), rules()).splitlines()
    assert lines[0].startswith("Evidence (literacy, Form 3, term 2): 14 of 17 learners.")
    assert lines[1] == (
        "Evidence (literacy, Form 3, term 2): 3 of 17 learners. There are too few term scores to "
        "say where the result sits against the range. "
        f"[LV-4, ST-3, GM-1, template {VERSION}, {FINDING_ID}]"
    )


def test_a_bin_with_no_level_in_the_template_gets_no_line():
    only_blocked = [{"bin": "blocked", "shown": True, "count": 4}]
    text = evidence_sentence(finding(bins=only_blocked), rules())
    assert text == not_enough_evidence_sentence(finding(bins=only_blocked), rules())


# --------------------------------------------------------------------------- no usable evidence
def test_a_suppressed_finding_gives_the_plain_statement_with_the_template_text_and_no_counts():
    f = finding(status="suppressed", group_size=None, bins=None, area=None)
    text = evidence_sentence(f, rules())
    assert text == (
        "There is not enough class evidence to adapt this lesson. "
        f"Groups smaller than 10 are never shown. [GM-1, template {VERSION}, {FINDING_ID}]"
    )
    assert "22" not in text and "14" not in text


def test_a_finding_with_no_usable_evidence_says_so_and_cites_the_range_not_the_group_minimum():
    f = finding(status="no_usable_evidence", bins=None, group_size=20)
    assert evidence_sentence(f, rules()) == (
        "There is not enough class evidence to adapt this lesson. "
        f"[ST-3, template {VERSION}, {FINDING_ID}]"
    )


def test_with_no_finding_at_all_the_statement_cites_nothing_it_does_not_have():
    assert not_enough_evidence_sentence(None, rules()) == (
        "There is not enough class evidence to adapt this lesson."
    )


def test_the_no_gap_statement_is_cited_and_carries_no_count():
    text = no_gap_sentence(with_below(0), rules())
    assert text == (
        "No class gap against the expected range is shown for this selection, so no "
        f"evidence-based edit is made. [ST-3, template {VERSION}, {FINDING_ID}]"
    )


def test_no_sentence_made_by_code_holds_a_digit_that_is_not_from_the_finding_or_the_template():
    for text in (
        not_enough_evidence_sentence(None, rules()),
        no_gap_sentence(with_below(0), rules()),
    ):
        assert not re.search(r"[0-9]", re.sub(r"\[.*\]", "", text))


# --------------------------------------------------------------------------- the checks it makes
def test_a_finding_read_under_another_rulebook_version_is_refused():
    with pytest.raises(EvidenceError, match="version"):
        evidence_sentence(finding(template_version="other"), rules())


def test_a_finding_that_does_not_cite_the_range_rule_for_its_stage_and_area_is_refused():
    with pytest.raises(EvidenceError, match="ST-3"):
        evidence_sentence(finding(clause_ids=["SK-1", "GM-1"]), rules())


def test_a_finding_for_a_stage_and_area_the_template_has_no_range_for_is_refused():
    with pytest.raises(EvidenceError, match="range"):
        evidence_sentence(finding(stage="Form 2"), rules())


# --------------------------------------------------------------------------- the finding shape
def test_a_finding_must_have_the_id_form_the_class_tool_makes():
    for bad in ("CF-123", "cf-a1b2c3d4", "R-000001", "", None):
        with pytest.raises(FindingError):
            finding_from_mapping(finding_mapping(finding_id=bad))


@pytest.mark.parametrize(
    "over",
    [
        {"status": "great"},
        {"term": "2"},
        {"term": True},
        {"group_size": -1},
        {"group_size": 2.5},
        {"bins": [{"bin": "x", "shown": True, "count": None}]},
        {"bins": [{"bin": "x", "shown": False, "count": 3}]},
        {"bins": [{"bin": "x", "shown": True, "count": -2}]},
        {"bins": [{"bin": "x", "shown": "yes", "count": 2}]},
        {"clause_ids": "ST-3"},
    ],
)
def test_a_malformed_finding_is_refused(over):
    with pytest.raises(FindingError):
        finding_from_mapping(finding_mapping(**over))


def test_a_finding_holds_no_name_a_learner_number_or_a_per_learner_list():
    for key in ("learners", "names", "learner_ids", "record_ids"):
        with pytest.raises(FindingError):
            finding_from_mapping(finding_mapping(**{key: ["x"]}))


def test_a_class_result_is_split_into_one_finding_per_area_with_the_top_level_context():
    result = {
        "status": "ok",
        "stage": "Form 3",
        "term": 2,
        "academic_year": "2025/2026",
        "template_version": VERSION,
        "group_size": 22,
        "finding_id": "CF-00000001",
        "clause_ids": ["GM-1"],
        "areas": [
            {
                "area": "literacy",
                "status": "ok",
                "bins": copy.deepcopy(BINS),
                "finding_id": FINDING_ID,
                "clause_ids": ["ST-3", "GM-1"],
            },
            {
                "area": "numeracy",
                "status": "no_usable_evidence",
                "finding_id": "CF-0f0f0f0f",
                "clause_ids": ["ST-6"],
            },
        ],
    }
    found = findings_from_class_result(result)
    assert [(f.finding_id, f.area, f.stage, f.term, f.group_size, f.status) for f in found] == [
        (FINDING_ID, "literacy", "Form 3", 2, 22, "ok"),
        ("CF-0f0f0f0f", "numeracy", "Form 3", 2, 22, "no_usable_evidence"),
    ]
    assert all(f.template_version == VERSION for f in found)


def test_a_suppressed_class_result_is_one_finding_with_no_area_and_no_counts():
    result = {
        "status": "suppressed",
        "stage": "Form 3",
        "term": 2,
        "template_version": VERSION,
        "finding_id": FINDING_ID,
        "clause_ids": ["GM-1"],
    }
    (only,) = findings_from_class_result(result)
    assert (only.status, only.area, only.group_size, only.bins) == ("suppressed", None, None, None)
