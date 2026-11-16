"""The lesson rules, read from a template's content (T-060, slice 1).

The content is inline YAML in tests/unit/lessonkit.py. Expected values are typed by hand from that
YAML. Nothing under templates/ is read for a value; the two real rulebook files are read only to
show that a rulebook without the lesson fields makes the lesson tools refuse.
"""

from pathlib import Path

import pytest
from lessonkit import VERSION, content, limits, rules

from mdeis.core.rules import RulebookProblem
from mdeis.core.template import load_template
from mdeis.lessons.rules import LessonsUnavailable, lesson_rules_from_content

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"


def test_the_rules_are_read_by_the_field_each_rule_carries():
    r = rules()
    assert (r.version, r.sha256) == (VERSION, "a" * 64)
    assert r.foundation_areas == ("literacy", "numeracy")
    assert r.terms_per_year == 3
    assert r.range_clause[("Form 3", "literacy")] == "ST-3"
    assert (r.min_group, r.group_clause) == (10, "GM-1")
    assert r.group_text == "Groups smaller than 10 are never shown."
    assert (r.min_cell, r.cell_clause) == (5, "GM-2")


def test_the_level_statements_keep_the_template_order_and_text():
    r = rules()
    assert [(lv.clause_id, lv.name) for lv in r.levels] == [
        ("LV-1", "below_expected_range"),
        ("LV-2", "within_expected_range"),
        ("LV-3", "above_expected_range"),
        ("LV-4", "insufficient_evidence"),
    ]
    assert r.levels[0].statement == (
        "The average of the last 3 term scores is below the expected range for the stage."
    )


def test_every_wording_clause_is_kept_with_its_words_and_phrases():
    r = rules()
    by_clause = {w.clause_id: w for w in r.wording}
    assert by_clause["WD-1"].words[:2] == ("weak", "remedial")
    assert by_clause["WD-1"].phrases == ()
    assert by_clause["WD-2"].phrases == ("top group", "bottom group", "ability group", "fast track")
    assert by_clause["WD-2"].words == ()
    assert by_clause["WD-3"].phrases == ("career path", "subject choice")


def test_the_limits_come_from_the_template_and_follow_a_change():
    r = rules()
    assert (r.max_notes_chars, r.max_edits, r.max_edit_chars) == (8000, 12, 1500)
    assert r.limits_clause == "LS-1"
    changed = rules(limits(max_notes_chars=40, max_edits=3, max_edit_chars=60))
    assert (changed.max_notes_chars, changed.max_edits, changed.max_edit_chars) == (40, 3, 60)


def test_a_changed_value_in_the_template_is_a_changed_value_in_the_rules():
    def change(c):
        c["group_minimum"][1]["min_cell_size"] = 7
        c["levels"][0]["statement"] = "The class average is under the range."
        c["wording"][2]["banned_phrases"] = ["a new phrase"]

    r = rules(change)
    assert r.min_cell == 7
    assert r.levels[0].statement == "The class average is under the range."
    assert {w.clause_id: w.phrases for w in r.wording}["WD-3"] == ("a new phrase",)


def test_a_renumbered_rulebook_still_works_because_rules_are_found_by_field():
    def renumber(c):
        c["group_minimum"][1]["id"] = "GM-9"
        c["lessons"][0]["id"] = "LS-4"
        c["wording"][1]["id"] = "WD-7"

    r = rules(renumber)
    assert r.cell_clause == "GM-9" and r.limits_clause == "LS-4"
    assert "WD-7" in {w.clause_id for w in r.wording}


def test_null_values_in_a_dumped_template_are_the_same_as_absent_ones():
    # A template parsed by the loader and dumped to JSON carries None for the fields a rule lacks.
    def dumped(c):
        for section in c.values():
            if isinstance(section, list) and section and isinstance(section[0], dict):
                for rule in section:
                    for field in ("banned", "banned_phrases", "min_group_size", "min_cell_size"):
                        rule.setdefault(field, None)
                    rule.setdefault("method_ref", None)

    r = rules(dumped)
    assert r.min_cell == 5 and r.min_group == 10


def test_a_selection_is_valid_only_for_a_stage_area_and_term_the_template_knows():
    r = rules()
    assert r.selection_problem(("Form 3", "literacy", 2)) is None
    assert r.selection_problem(("Form 3", "literacy", 3)) is None
    assert "stage" in r.selection_problem(("Form 9", "literacy", 2))
    assert "area" in r.selection_problem(("Form 3", "poetry", 2))
    assert "term" in r.selection_problem(("Form 3", "literacy", 0))
    assert "term" in r.selection_problem(("Form 3", "literacy", 4))
    assert "term" in r.selection_problem(("Form 3", "literacy", True))


# ------------------------------------------------------------------------- a rulebook without them
@pytest.mark.parametrize(
    ("remove", "word"),
    [
        (lambda c: c.pop("lessons"), "lessons"),
        (lambda c: c.__setitem__("lessons", []), "lessons"),
        (lambda c: c["lessons"][0].pop("max_edits"), "max_edits"),
        (lambda c: c["lessons"][0].pop("max_notes_chars"), "max_notes_chars"),
        (lambda c: c["lessons"][0].pop("max_edit_chars"), "max_edit_chars"),
        (lambda c: c["wording"].__delitem__(slice(1, 3)), "banned_phrases"),
        (lambda c: c["group_minimum"].pop(1), "min_cell_size"),
        (lambda c: c["group_minimum"].pop(0), "min_group_size"),
        (lambda c: c["levels"].pop(0), "below_expected_range"),
        (lambda c: c["calculations"].pop(0), "terms_per_year"),
    ],
)
def test_a_rulebook_that_lacks_a_lesson_rule_makes_the_lesson_tools_refuse(remove, word):
    c = content()
    remove(c)
    with pytest.raises(LessonsUnavailable, match=word):
        lesson_rules_from_content(c, "no-lessons", "b" * 64)


def test_the_refusal_is_a_rulebook_problem_so_every_tool_wrapper_logs_it_as_a_refusal():
    assert issubclass(LessonsUnavailable, RulebookProblem)


@pytest.mark.parametrize("name", ["v1", "v2-strict"])
def test_the_two_existing_rulebook_files_have_no_lesson_rules_so_the_tools_refuse_on_them(name):
    template = load_template(TEMPLATES / f"{name}.yaml")
    with pytest.raises(LessonsUnavailable):
        lesson_rules_from_content(template.model_dump(mode="json"), name, "0" * 64)
