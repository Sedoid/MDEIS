"""check_edits: the deterministic checks LC-1 to LC-10 on a model's edit list (T-060, slice 2).

Each check has an accept case and a reject case. A failing edit is dropped and listed with its LC
ids and a plain reason; the rest of the plan goes on. Ids of tests: E-51 (objective untouched),
E-53 (no valid finding id), E-54 (no gap or no evidence), E-55 (no digits, no name), E-56 (wording).
The rules are the inline test rules of tests/unit/lessonkit.py.
"""

import dataclasses
import hashlib
import json

import pytest
from lessonkit import (
    A_ASK,
    A_PAIRS,
    A_PLENARY,
    A_STARTER,
    A_SUM,
    FINDING_ID,
    LESSON,
    OBJECTIVE,
    OTHER_ID,
    add_after,
    edit,
    finding,
    lesson,
    limits,
    roster,
    rules,
    with_below,
)

from mdeis.lessons import checks
from mdeis.lessons.apply import Revised
from mdeis.lessons.checks import PlanError, Selection, check_edits
from mdeis.lessons.guard import Roster
from mdeis.lessons.parse import parse_lesson

SELECTION = Selection("Form 3", "literacy", 2)


def run(edits, findings=None, selection=SELECTION, r=None, parsed=None, people=None):
    return check_edits(
        parsed if parsed is not None else lesson(),
        edits,
        [finding()] if findings is None else findings,
        r or rules(),
        selection,
        roster() if people is None else people,
    )


def verdict(checked, edit_id="e1"):
    return next(v for v in checked.verdicts if v.edit_id == edit_id)


def dropped_for(checked, lc_id, edit_id="e1"):
    v = verdict(checked, edit_id)
    return (not v.kept) and lc_id in v.lc_ids


# --------------------------------------------------------------------------- a good edit
def test_a_good_edit_is_kept_with_no_reason_and_the_revised_text_is_built():
    checked = run([edit()])
    v = verdict(checked)
    assert v.kept and v.lc_ids == () and v.reasons == ()
    assert checked.kept == (edit(),)
    assert checked.revised.text == LESSON.replace(A_ASK, "Ask what the passage tells us and why.")
    assert checked.usable_evidence and checked.statement is None and checked.passed


def test_the_selection_may_be_given_as_a_plain_tuple():
    assert run([edit()], selection=("Form 3", "literacy", 2)).passed


# --------------------------------------------------------------------------- LC-1, E-51
def test_lc1_an_edit_in_a_body_section_is_accepted_and_the_objective_is_byte_identical():
    checked = run([edit(anchor=A_PLENARY, replacement="Share the sentences and say why.")])
    assert checked.revised.objective == OBJECTIVE
    assert checked.revised.objective_sha256 == hashlib.sha256(OBJECTIVE.encode()).hexdigest()
    assert checked.objective_unchanged


def test_lc1_an_edit_that_quotes_the_objective_field_is_refused_and_listed():
    notes = "## Starter\nRead the passage aloud.\n"
    parsed = parse_lesson(notes, "Learners explain the main idea.")
    checked = run(
        [edit(anchor="explain the main idea", replacement="explain the idea")], parsed=parsed
    )
    assert dropped_for(checked, "LC-1")
    assert not verdict(checked).repairable
    assert "objective" in verdict(checked).reasons[0]
    assert checked.revised.text == parsed.text


def test_lc1_an_edit_inside_the_objective_line_of_the_notes_is_refused():
    for anchor in ("Objective: Learners", "Learners explain the main idea of a short text."):
        checked = run([edit(anchor=anchor, replacement="Changed")])
        assert dropped_for(checked, "LC-1"), anchor


def test_lc1_an_edit_that_reaches_into_the_objective_from_the_next_section_is_refused():
    anchor = "short text.\n\n## Starter"
    checked = run([edit(anchor=anchor, replacement="short text.\n\n## Begin")])
    assert dropped_for(checked, "LC-1")


def test_lc1_a_repeat_of_the_objective_inside_the_notes_is_locked_too():
    notes = f"{OBJECTIVE}\n\n## Activities\n1. Say the aim: {OBJECTIVE}\n2. Work in pairs.\n"
    parsed = parse_lesson(notes, OBJECTIVE)
    repeat = edit(anchor=f"Say the aim: {OBJECTIVE}", replacement="Say the aim.")
    locked = run([repeat], parsed=parsed)
    free = run([edit(id="e2", anchor="2. Work in pairs.", replacement="2. Work in threes.")],
               parsed=parsed)
    assert dropped_for(locked, "LC-1")
    assert verdict(free, "e2").kept


# --------------------------------------------------------------------------- LC-2, E-53
def test_lc2_a_finding_returned_in_this_run_is_accepted():
    assert verdict(run([edit(finding_ids=[FINDING_ID])])).kept


def test_lc2_an_unknown_finding_id_drops_the_edit():
    checked = run([edit(finding_ids=["CF-deadbeef"])])
    v = verdict(checked)
    assert not v.kept and "LC-2" in v.lc_ids and not v.repairable
    assert "CF-deadbeef" in v.reasons[0] or "finding" in v.reasons[0]


def test_lc2_one_unknown_id_among_known_ones_drops_the_edit():
    assert dropped_for(run([edit(finding_ids=[FINDING_ID, "CF-deadbeef"])]), "LC-2")


def test_lc2_an_edit_with_no_finding_ids_is_dropped():
    assert dropped_for(run([edit(finding_ids=())]), "LC-2")


def test_lc2_a_finding_that_is_not_ok_cannot_be_cited():
    suppressed = finding(status="suppressed", group_size=None, bins=None)
    checked = run([edit()], findings=[suppressed])
    assert dropped_for(checked, "LC-2")


def test_lc2_resource_ids_must_be_empty_in_this_version():
    assert dropped_for(run([edit(resource_ids=("RS-1",))]), "LC-2")


# --------------------------------------------------------------------------- LC-3
def test_lc3_a_unique_anchor_inside_one_section_is_accepted():
    assert verdict(run([edit(anchor=A_PAIRS, replacement="Learners read in pairs.")])).kept


def test_lc3_an_anchor_that_is_not_in_the_notes_is_dropped_and_may_be_repaired():
    v = verdict(run([edit(anchor="This is not in the notes.")]))
    assert not v.kept and "LC-3" in v.lc_ids and v.repairable
    assert "not found" in v.reasons[0]


def test_lc3_an_anchor_has_to_match_exactly_no_case_or_space_folding():
    assert dropped_for(run([edit(anchor=A_ASK.lower())]), "LC-3")
    assert dropped_for(run([edit(anchor=A_ASK.replace(" ", "  "))]), "LC-3")


def test_lc3_a_blank_anchor_is_dropped():
    assert dropped_for(run([edit(anchor="  \n")]), "LC-3")


def test_lc3_an_anchor_that_occurs_twice_is_dropped_even_when_the_matches_overlap():
    twice = parse_lesson("Say it again.\n\n## B\nSay it again.\n", "the aim")
    assert "more than once" in verdict(run([edit(anchor="Say it again.")], parsed=twice)).reasons[0]
    overlapping = parse_lesson("aaa\n", "the aim")
    assert dropped_for(run([edit(anchor="aa", replacement="bb")], parsed=overlapping), "LC-3")


def test_lc3_an_anchor_across_two_sections_is_dropped_and_may_be_repaired():
    anchor = "about.\n\n## Main activity\nLearners"
    v = verdict(run([edit(anchor=anchor, replacement="about.\n\n## Main activity\nLearners")]))
    assert "LC-3" in v.lc_ids and v.repairable
    assert "section" in " ".join(v.reasons)


def test_lc3_two_overlapping_edits_are_both_dropped():
    a = edit(id="e1", anchor="Read the short passage aloud", replacement="Read the passage aloud")
    b = edit(id="e2", anchor="passage aloud to the class.", replacement="text aloud to the class.")
    checked = run([a, b])
    assert dropped_for(checked, "LC-3", "e1") and dropped_for(checked, "LC-3", "e2")
    assert "overlap" in " ".join(verdict(checked, "e1").reasons)
    assert checked.kept == ()


def test_lc3_edits_that_only_touch_are_both_kept():
    a = edit(id="e1", anchor="Read the short passage aloud", replacement="Read the passage aloud")
    b = edit(id="e2", anchor=" to the class.", replacement=" to everyone.")
    checked = run([a, b])
    assert verdict(checked, "e1").kept and verdict(checked, "e2").kept


def test_lc3_an_edit_that_failed_another_check_does_not_make_its_neighbour_overlap():
    bad = edit(id="e1", anchor="Read the short passage aloud",
               replacement="Read the weak passage aloud")  # LC-6
    good = edit(id="e2", anchor="passage aloud to the class.",
                replacement="text aloud to the class.")
    checked = run([bad, good])
    assert not verdict(checked, "e1").kept and "LC-6" in verdict(checked, "e1").lc_ids
    assert verdict(checked, "e2").kept


def test_lc3_an_anchor_that_touches_a_table_row_or_a_code_fence_is_dropped():
    notes = (
        "Intro line.\n\n| word | meaning |\n| --- | --- |\n| tide | water rising |\n\n"
        "After the table.\n```\nprint('x')\n```\nEnd.\n"
    )
    parsed = parse_lesson(notes, "the aim")
    for anchor in ("| tide | water rising |", "water rising", "print('x')", "```\nprint"):
        v = verdict(run([edit(anchor=anchor, replacement=anchor + "!")], parsed=parsed))
        assert "LC-3" in v.lc_ids and not v.kept, anchor
    clear = run([edit(anchor="After the table.", replacement="After the table, discuss.")],
                parsed=parsed)
    assert verdict(clear).kept


# --------------------------------------------------------------------------- LC-4
def test_lc4_a_reword_that_equals_its_anchor_is_dropped_and_may_be_repaired():
    v = verdict(run([edit(anchor=A_ASK, replacement=A_ASK)]))
    assert "LC-4" in v.lc_ids and v.repairable and not v.kept


@pytest.mark.parametrize("kind", ["add_example", "add_check"])
def test_lc4_an_addition_must_begin_with_its_anchor_and_add_something(kind):
    ok = add_after(A_SUM, " Give an example.", type=kind)
    assert verdict(run([ok])).kept
    not_begin = edit(type=kind, anchor=A_SUM, replacement="Give an example. " + A_SUM)
    assert dropped_for(run([not_begin]), "LC-4")
    nothing_added = edit(type=kind, anchor=A_SUM, replacement=A_SUM)
    assert dropped_for(run([nothing_added]), "LC-4")
    removed = edit(type=kind, anchor=A_SUM, replacement=A_SUM[:-8])
    assert dropped_for(run([removed]), "LC-4")


def test_lc4_the_plan_rebuild_refuses_the_whole_call_if_the_applier_changes_other_text(monkeypatch):
    real = checks.apply_edits

    def broken(parsed, edits):
        good = real(parsed, edits)
        return dataclasses.replace(good, text=good.text.replace("## Plenary", "## Plenary!"))

    monkeypatch.setattr(checks, "apply_edits", broken)
    with pytest.raises(PlanError, match="outside"):
        run([edit()])


def test_lc4_the_plan_rebuild_refuses_the_whole_call_if_the_objective_is_not_the_same(monkeypatch):
    real = checks.apply_edits

    def broken(parsed, edits):
        return dataclasses.replace(real(parsed, edits), objective="Something else.")

    monkeypatch.setattr(checks, "apply_edits", broken)
    with pytest.raises(PlanError, match="objective"):
        run([edit()])


def test_lc4_the_check_result_says_what_was_compared():
    checked = run([edit()])
    assert checked.objective_unchanged and checked.outside_unchanged
    assert isinstance(checked.revised, Revised)


# --------------------------------------------------------------------------- LC-5, E-55
def test_lc5_a_name_or_number_in_a_suggestion_or_replacement_drops_the_edit_unrepeated():
    cases = [
        edit(suggestion="Let Amara lead the pair work."),
        edit(replacement="Ask Joy what the passage tells us."),
        edit(replacement="Ask what record L-0007 tells us."),
        edit(suggestion="Check occ/2025/014 first."),
    ]
    for case in cases:
        checked = run([case])
        v = verdict(checked)
        assert not v.kept and "LC-5" in v.lc_ids and not v.repairable
        flat = json.dumps(dataclasses.asdict(v), ensure_ascii=False).lower()
        for secret in ("amara", "joy", "l-0007", "occ/2025/014"):
            assert secret not in flat


def test_lc5_notes_that_name_a_learner_stop_the_whole_call():
    parsed = parse_lesson("## Starter\nLet Amara lead the reading.\n", "the aim")
    with pytest.raises(PlanError, match="learner"):
        run([edit(anchor="Let Amara lead the reading.", replacement="Let all read.")],
            parsed=parsed)


def test_lc5_without_a_roster_the_number_shapes_are_still_scanned():
    v = verdict(run([edit(suggestion="Look at A-000123 for a model.")], people=Roster.empty()))
    assert "LC-5" in v.lc_ids


# --------------------------------------------------------------------------- LC-6, E-56
def test_lc6_a_banned_word_is_dropped_and_the_reason_names_the_clause_and_the_word():
    v = verdict(run([edit(suggestion="Give the weak readers a short passage.")]))
    assert not v.kept and "LC-6" in v.lc_ids and v.repairable
    assert "WD-1" in v.reasons[0] and "weak" in v.reasons[0]


def test_lc6_a_banned_phrase_from_the_template_is_dropped_whatever_its_case_or_spacing():
    for phrase in ("Put them in the top group.", "Use the Top   Group for this.", "A FAST TRACK."):
        v = verdict(run([edit(replacement="Ask why. " + phrase)]))
        assert "LC-6" in v.lc_ids, phrase
        assert "WD-2" in v.reasons[0]
    pathway = verdict(run([edit(suggestion="Link this to a career path.")]))
    assert "WD-3" in pathway.reasons[0]


def test_lc6_words_match_as_whole_words_only_so_weaker_and_slowly_pass():
    assert verdict(run([edit(suggestion="Read slowly and weigh the weaker claim.")])).kept


def test_lc6_a_phrase_that_is_not_on_the_list_passes_that_is_the_stated_limit():
    sentence = "Pair the struggling readers with a partner."
    assert verdict(run([edit(suggestion=sentence)])).kept


def test_lc6_adding_the_phrase_to_the_template_makes_the_same_input_fail():
    def add(c):
        c["wording"][1]["banned_phrases"].append("struggling readers")

    v = verdict(run([edit(suggestion="Pair the struggling readers with a partner.")], r=rules(add)))
    assert "LC-6" in v.lc_ids and "WD-2" in v.reasons[0]


def test_lc6_taking_a_word_off_the_template_list_lets_it_pass():
    def off(c):
        c["wording"][0]["banned"].remove("weak")

    weak = edit(suggestion="Give the weak readers a short passage.")
    assert verdict(run([weak], r=rules(off))).kept


def test_lc6_the_tutors_own_words_in_an_anchor_are_not_scanned_for_an_addition():
    notes = "## A\nGo slow when you read aloud.\n"
    parsed = parse_lesson(notes, "the aim")
    anchor = "Go slow when you read aloud."
    nice = edit(type="add_check", anchor=anchor, replacement=anchor + " Ask for the main point.")
    clean = run([nice], parsed=parsed)
    assert verdict(clean).kept
    assert [n.clause_id for n in clean.notices] == ["WD-1"]  # a notice, no refusal
    bad = edit(type="add_check", anchor=anchor, replacement=anchor + " Then name the weak part.")
    dirty = run([bad], parsed=parsed)
    assert "LC-6" in verdict(dirty).lc_ids


# --------------------------------------------------------------------------- LC-7, E-55
def test_lc7_a_digit_in_a_suggestion_is_dropped_in_any_script():
    for suggestion in ("Spend 5 minutes on it.", "Spend ٣ minutes on it.", "Use a second text."):
        v = verdict(run([edit(suggestion=suggestion)]))
        assert v.kept == (suggestion == "Use a second text."), suggestion
    bad = verdict(run([edit(suggestion="Spend 5 minutes on it.")]))
    assert "LC-7" in bad.lc_ids and bad.repairable


def test_lc7_a_number_word_passes_that_is_the_stated_limit():
    assert verdict(run([edit(suggestion="Spend five minutes on it.")])).kept


NOTES_WITH_NUMBER = "## A\nSpend 20 minutes on the passage.\n\n## B\nAsk three questions.\n"


def test_lc7_a_new_number_in_a_replacement_is_dropped_but_a_number_already_in_the_notes_passes():
    parsed = parse_lesson(NOTES_WITH_NUMBER, "Learners answer 3 questions.")
    anchor = "Spend 20 minutes on the passage."
    same = edit(anchor=anchor, replacement="Spend 20 minutes on the story.")
    other = edit(anchor=anchor, replacement="Spend 30 minutes on the story.")
    from_objective = edit(anchor=anchor, replacement="Spend 3 minutes on the story.")
    assert verdict(run([same], parsed=parsed)).kept
    assert "LC-7" in verdict(run([other], parsed=parsed)).lc_ids
    assert verdict(run([from_objective], parsed=parsed)).kept  # the objective holds a 3


def test_lc7_digits_in_other_scripts_are_read_as_the_same_number():
    parsed = parse_lesson(NOTES_WITH_NUMBER, "Learners answer questions.")
    anchor = "Spend 20 minutes on the passage."
    same = edit(anchor=anchor, replacement="Spend ٢٠ minutes on it.")
    assert verdict(run([same], parsed=parsed)).kept
    v = verdict(run([edit(anchor=anchor, replacement="Spend ٣٠ minutes on it.")], parsed=parsed))
    assert "LC-7" in v.lc_ids


# --------------------------------------------------------------------------- LC-8, E-54
def test_lc8_with_no_finding_at_all_every_edit_is_dropped_and_the_statement_says_so():
    checked = run([edit(), edit(id="e2", anchor=A_PLENARY, replacement="Share and say why.")],
                  findings=[])
    assert all(not v.kept and "LC-8" in v.lc_ids and not v.repairable for v in checked.verdicts)
    assert checked.kept == () and not checked.usable_evidence and not checked.passed
    assert checked.statement == "There is not enough class evidence to adapt this lesson."
    assert checked.statement_kind == "insufficient_evidence"
    assert checked.revised.text == LESSON


def test_lc8_a_suppressed_group_gives_no_edit_and_a_statement_without_counts():
    suppressed = finding(status="suppressed", group_size=None, bins=None)
    checked = run([edit()], findings=[suppressed])
    assert dropped_for(checked, "LC-8")
    assert checked.statement == (
        "There is not enough class evidence to adapt this lesson. "
        f"Groups smaller than 10 are never shown. [GM-1, template t-lessons, {FINDING_ID}]"
    )
    assert "22" not in checked.statement and "14" not in checked.statement


def test_lc8_a_class_with_no_usable_evidence_gives_no_edit():
    f = finding(status="no_usable_evidence", bins=None)
    checked = run([edit()], findings=[f])
    assert dropped_for(checked, "LC-8") and checked.statement_kind == "insufficient_evidence"


def test_lc8_a_shown_count_of_zero_below_the_range_is_no_gap_and_gives_no_edit():
    checked = run([edit()], findings=[with_below(0)])
    assert dropped_for(checked, "LC-8")
    assert checked.statement_kind == "no_gap"
    assert checked.statement.startswith("No class gap against the expected range is shown")


def test_lc8_a_hidden_below_count_is_not_enough_evidence():
    checked = run([edit()], findings=[with_below(0, shown=False)])
    assert dropped_for(checked, "LC-8") and checked.statement_kind == "insufficient_evidence"


def test_lc8_a_gap_needs_a_count_at_or_above_the_templates_minimum_cell_size():
    # The inline rules set the minimum cell size to 5.
    assert dropped_for(run([edit()], findings=[with_below(4)]), "LC-8")
    assert verdict(run([edit()], findings=[with_below(5)])).kept
    assert verdict(run([edit()], findings=[with_below(6)])).kept


def test_lc8_the_minimum_cell_size_is_read_from_the_template_not_from_the_code():
    def seven(c):
        c["group_minimum"][1]["min_cell_size"] = 7

    assert dropped_for(run([edit()], findings=[with_below(6)], r=rules(seven)), "LC-8")
    assert verdict(run([edit()], findings=[with_below(7)], r=rules(seven))).kept


def test_lc8_a_finding_read_under_another_rulebook_version_is_not_usable_evidence():
    checked = run([edit()], findings=[finding(template_version="other")])
    assert dropped_for(checked, "LC-8") and not checked.usable_evidence


def test_lc8_an_edit_may_not_cite_a_finding_without_a_gap_even_if_another_finding_has_one():
    no_gap = with_below(0, finding_id=OTHER_ID)
    checked = run([edit(finding_ids=[OTHER_ID])], findings=[finding(), no_gap])
    assert dropped_for(checked, "LC-8")


# --------------------------------------------------------------------------- LC-9
def test_lc9_evidence_for_the_selected_stage_area_and_term_is_accepted():
    assert verdict(run([edit()])).kept


@pytest.mark.parametrize(
    "over",
    [{"area": "numeracy"}, {"stage": "Form 1"}, {"term": 3}],
    ids=["other area", "other stage", "other term"],
)
def test_lc9_evidence_for_another_area_stage_or_term_drops_the_edit(over):
    other = finding(finding_id=OTHER_ID, **over)
    checked = run([edit(finding_ids=[OTHER_ID])], findings=[finding(), other])
    v = verdict(checked)
    assert not v.kept and "LC-9" in v.lc_ids and not v.repairable


def test_lc9_one_wrong_finding_among_right_ones_drops_the_edit():
    other = finding(finding_id=OTHER_ID, area="numeracy")
    checked = run([edit(finding_ids=[FINDING_ID, OTHER_ID])], findings=[finding(), other])
    assert dropped_for(checked, "LC-9")


def test_lc9_a_selection_the_template_does_not_know_stops_the_call():
    unknown = (("Form 9", "literacy", 2), ("Form 3", "poetry", 2), ("Form 3", "literacy", 4))
    for selection in unknown:
        with pytest.raises(PlanError, match="selection|stage|area|term"):
            run([edit()], selection=selection)


# --------------------------------------------------------------------------- LC-10
def three_edits(n):
    anchors = [A_STARTER, A_PAIRS, A_PLENARY, A_SUM][:n]
    return [edit(id=f"e{i}", anchor=a, replacement=a + " Why?", type="add_check")
            for i, a in enumerate(anchors, start=1)]


def test_lc10_the_edit_count_at_the_templates_maximum_is_accepted_and_the_extra_one_is_dropped():
    small = rules(limits(max_edits=3))
    at_max = run(three_edits(3), r=small)
    assert all(v.kept for v in at_max.verdicts)
    over = run(three_edits(4), r=small)
    assert [v.kept for v in over.verdicts] == [True, True, True, False]
    assert "LC-10" in verdict(over, "e4").lc_ids and not verdict(over, "e4").repairable


def test_lc10_the_length_of_a_replacement_or_a_suggestion_is_limited_by_the_template():
    small = rules(limits(max_edit_chars=60))
    anchor = A_ASK
    at_limit = edit(anchor=anchor, replacement="x" * 60)
    over = edit(anchor=anchor, replacement="x" * 61)
    long_suggestion = edit(suggestion="s" * 61)
    assert verdict(run([at_limit], r=small)).kept
    for case in (over, long_suggestion):
        v = verdict(run([case], r=small))
        assert "LC-10" in v.lc_ids and v.repairable


def test_lc10_notes_over_the_templates_maximum_stop_the_call_and_notes_at_it_pass():
    notes = "## A\n" + "a" * 35 + "\n"
    parsed = parse_lesson(notes, "the aim")
    small = rules(limits(max_notes_chars=len(notes)))
    assert run([], parsed=parsed, r=small).revised.text == notes
    with pytest.raises(PlanError, match="long"):
        run([], parsed=parsed, r=rules(limits(max_notes_chars=len(notes) - 1)))


def test_lc10_an_empty_edit_list_is_a_result_not_an_error_but_it_does_not_pass():
    checked = run([])
    assert checked.verdicts == () and checked.kept == () and not checked.passed
    assert checked.revised.text == LESSON


# --------------------------------------------------------------------------- the whole result
def test_every_failing_check_is_listed_not_only_the_first():
    v = verdict(run([edit(suggestion="Give the weak readers 5 minutes.")]))
    assert set(v.lc_ids) == {"LC-6", "LC-7"}
    assert len(v.reasons) == 2


def test_the_result_can_be_written_down_and_holds_no_name():
    checked = run([edit(suggestion="Let Amara lead."), edit(id="e2", anchor=A_PLENARY,
                                                              replacement="Share and say why.")])
    stored = checked.stored_edits()
    assert [e["id"] for e in stored["kept"]] == ["e2"]
    assert stored["dropped"] == [
        {"id": "e1", "lc": ["LC-5"], "reason": verdict(checked).reasons[0]}
    ]
    assert "amara" not in json.dumps(stored).lower()
    assert set(stored["kept"][0]) >= {"id", "type", "anchor_quote", "replacement", "finding_ids",
                                      "suggestion"}
    # a dropped edit is stored as an id, the LC ids and a plain reason: never the model's text
    assert set(stored["dropped"][0]) == {"id", "lc", "reason"}
