"""guard_notes: notes or an objective that name a learner are refused, never cleaned (T-060).

E-55. The roster is given in memory; the names below are invented. A refusal carries positions and
kinds only. Nothing in a result may hold a roster name or a matched id.
"""

import dataclasses
import json

import pytest
from lessonkit import roster

from mdeis.lessons.guard import Roster, guard_notes, scan_text

CLEAN = "## Starter\nRead the passage aloud.\n\nAsk what it is about. Joyful voices help.\n"


def kinds(result):
    return [(f.kind, f.start, f.end) for f in result.findings]


def test_clean_notes_pass_and_come_back_byte_identical():
    result = guard_notes(CLEAN, roster())
    assert result.ok and result.reason is None and result.findings == ()
    assert result.text == CLEAN
    assert result.text is CLEAN or result.text.encode() == CLEAN.encode()


@pytest.mark.parametrize("ident", ["L-0007", "L-123456", "A-000123", "I-000045", "R-000007"])
def test_a_learner_record_issue_or_report_number_is_refused(ident):
    text = f"Look at {ident} before the lesson."
    result = guard_notes(text, roster())
    assert not result.ok and result.text is None
    assert result.reason == "learner_name_or_id"
    assert kinds(result) == [("id", 8, 8 + len(ident))]


@pytest.mark.parametrize(
    "text", ["see L-12", "see A-123", "see I-12345", "PL-0007 is a code", "A-0001"]
)
def test_text_that_only_looks_a_little_like_a_number_is_not_one(text):
    assert guard_notes(text, roster()).ok


@pytest.mark.parametrize(
    "text",
    [
        "Amara Nfor should lead the pair.",
        "Nfor Amara should lead the pair.",
        "Ask amara to read.",
        "Ask AMARA to read.",
        "Ask Nfor to read.",
        "Amara's book is open.",
        "Joy Mbeki and a friend.",
        "Mbeki helps.",
        "Zoe Tanda reads.",
        "ZOË reads.",
        "zoe reads.",
    ],
)
def test_a_name_from_the_roster_is_refused_in_any_order_case_or_accent(text):
    result = guard_notes(text, roster())
    assert not result.ok and result.reason == "learner_name_or_id"
    assert {k for k, _, _ in kinds(result)} == {"name"}


def test_a_school_reference_from_the_roster_is_refused():
    result = guard_notes("Check occ/2025/014 first.", roster())
    assert not result.ok
    assert kinds(result) == [("id", 6, 6 + len("occ/2025/014"))]


def test_a_name_is_matched_as_a_whole_word_only():
    assert guard_notes("Joyful voices, Joyce, Nforest, Amarant.", roster()).ok


def test_positions_point_into_the_original_text():
    text = "Hello Amara. Then Joy Mbeki."
    result = guard_notes(text, roster())
    spans = [(text[s:e], s, e) for _, s, e in kinds(result)]
    assert spans == [("Amara", 6, 11), ("Joy", 18, 21), ("Mbeki", 22, 27)]


def test_a_refusal_never_holds_a_name_or_a_matched_id():
    text = "Amara Nfor, Zoë Tanda, Joy, L-0007 and occ/2025/014 are named."
    result = guard_notes(text, roster())
    flat = json.dumps(dataclasses.asdict(result), ensure_ascii=False).lower() + repr(result).lower()
    secrets = ("amara", "nfor", "zoë", "zoe", "tanda", "joy", "mbeki", "l-0007", "occ/2025/014")
    for secret in secrets:
        assert secret not in flat, secret
    assert len(result.findings) == 7  # Amara, Nfor, Zoë, Tanda, Joy, L-0007, occ/2025/014
    assert str(result).lower().count("amara") == 0


def test_a_one_letter_part_of_a_name_is_an_initial_and_does_not_match_the_word_a():
    small = Roster(names=("A Bello",), refs=())
    assert guard_notes("a good lesson for a class", small).ok
    assert not guard_notes("Bello reads", small).ok


def test_with_no_roster_only_the_number_shapes_are_checked():
    assert guard_notes("Amara Nfor reads.", Roster.empty()).ok
    assert guard_notes("Amara Nfor reads.", None).ok
    assert not guard_notes("see L-0007", None).ok


def test_empty_or_blank_notes_are_refused_plainly():
    for text in ("", "  \n\t"):
        result = guard_notes(text, roster())
        assert not result.ok and result.reason == "empty"


def test_notes_over_the_given_length_are_refused_with_the_length_not_the_text():
    result = guard_notes("x" * 41, roster(), max_chars=40)
    assert not result.ok and result.reason == "too_long" and result.length == 41
    assert guard_notes("x" * 40, roster(), max_chars=40).ok
    assert guard_notes("x" * 4000, roster()).ok  # no limit given, no limit applied


def test_scan_text_returns_the_same_findings_without_a_verdict():
    found = scan_text("Joy and L-0007", roster())
    assert [(f.kind, f.start, f.end) for f in found] == [("name", 0, 3), ("id", 8, 14)]


def test_the_roster_values_are_never_part_of_what_the_roster_prints():
    assert "Amara" not in repr(roster()) and "OCC" not in repr(roster())
