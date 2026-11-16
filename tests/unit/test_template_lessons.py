"""The lesson-adapter rules in the criteria template: WD-2 and WD-3 (`banned_phrases`) under
`wording`, and the optional `lessons` section with LS-1, in v1.1.yaml and v2.1-strict.yaml.

No database. Expected values are typed by hand from the two files (all PLACEHOLDER values, not the
school's). v1.yaml and v2-strict.yaml are not changed: they still load without a `lessons` section,
and the lesson reader (mdeis.lessons.rules) refuses them.
"""

import copy
import re
from pathlib import Path

import pytest
import yaml

from mdeis.core.template import TemplateError, load_template, parse_template
from mdeis.lessons.rules import LessonsUnavailable, lesson_rules_from_content

ROOT = Path(__file__).resolve().parents[2]
T = ROOT / "templates"
V1, V11, V2, V21 = (T / n for n in ("v1.yaml", "v1.1.yaml", "v2-strict.yaml", "v2.1-strict.yaml"))
NEW = [V11, V21]
NEW_IDS = ["v1.1", "v2.1-strict"]
GROUP = {"v1.1": 10, "v2.1-strict": 15}
CELL = {"v1.1": 5, "v2.1-strict": 10}
TRACKS = [
    "top group", "bottom group", "top set", "bottom set", "ability group", "fast track",
    "slow track", "low ability", "high ability",
]  # fmt: skip
PATHWAY = ["career path", "career pathway", "subject choice", "choose a career", "future career"]
LABELS = ["weak", "remedial", "gifted", "lazy", "slow", "failing", "brilliant"]


def raw(path=V11) -> dict:
    return copy.deepcopy(yaml.safe_load(path.read_text(encoding="utf-8")))


def rule(data, section, rule_id):
    return next(r for r in data[section] if r["id"] == rule_id)


def rejects(data, message):
    with pytest.raises(TemplateError, match=re.escape(message)):
        parse_template(data)


def reader(path):
    template = load_template(path)
    return lesson_rules_from_content(template.model_dump(mode="json"), path.stem, "a" * 64)


# ------------------------------------------------------------------ the files
@pytest.mark.parametrize("path", NEW, ids=NEW_IDS)
def test_the_new_files_load_with_the_wording_phrases_and_the_lessons_section(path):
    t = load_template(path)
    assert [r.id for r in t.wording] == ["WD-1", "WD-2", "WD-3"]
    wd1, wd2, wd3 = t.wording
    assert wd1.banned == LABELS and wd1.banned_phrases is None
    assert wd2.banned_phrases == TRACKS and wd2.banned is None
    assert wd3.banned_phrases == PATHWAY and wd3.banned is None
    assert [r.id for r in t.lessons] == ["LS-1"]
    ls = t.lessons[0]
    assert (ls.max_notes_chars, ls.max_edits, ls.max_edit_chars) == (8000, 12, 1500)


@pytest.mark.parametrize("path", [V1, V2], ids=["v1", "v2-strict"])
def test_the_old_files_still_load_without_a_lessons_section_and_are_unchanged(path):
    t = load_template(path)
    assert t.lessons == []
    assert [r.id for r in t.wording] == ["WD-1"]
    assert [r.id for r in t.group_minimum] == ["GM-1"]
    text = path.read_text(encoding="utf-8")
    for word in ("lessons:", "LS-1", "banned_phrases", "WD-2", "GM-2"):
        assert word not in text, word


@pytest.mark.parametrize("path", NEW, ids=NEW_IDS)
def test_the_clause_ids_are_unique_and_the_new_ones_are_there(path):
    t = load_template(path)
    ids = [c.id for c in t.clauses()]
    assert len(ids) == len(set(ids))
    assert {"WD-2", "WD-3", "LS-1", "GM-2", "GM-3"} <= set(ids)
    old = {c.id for c in load_template(V1).clauses()}
    assert set(ids) - old == {"WD-2", "WD-3", "LS-1", "GM-2", "GM-3"}
    assert len(ids) == len(old) + 5
    areas = {c.id: c.area for c in t.clauses()}
    assert (areas["WD-2"], areas["WD-3"], areas["LS-1"]) == ("WD", "WD", "LS")
    assert ids[-1] == "LS-1", "the optional section comes after the required ones"


@pytest.mark.parametrize("path", NEW, ids=NEW_IDS)
def test_the_only_difference_from_the_old_file_is_the_new_rules_and_its_own_record(path):
    old = V1 if path == V11 else V2
    before, after = raw(old), raw(path)
    changed = {k for k in before if before[k] != after[k]} | (set(after) - set(before))
    assert changed == {"version", "change_log", "wording", "group_minimum", "lessons"}
    assert after["wording"][:1] == before["wording"]
    assert after["group_minimum"][:1] == before["group_minimum"]


@pytest.mark.parametrize("path", NEW, ids=NEW_IDS)
def test_every_new_value_is_marked_placeholder_in_the_file(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    wanted = ("banned_phrases:", "max_notes_chars:", "max_edits:", "max_edit_chars:",
              "min_cell_size:")  # fmt: skip
    found = [ln for ln in lines if ln.lstrip(" -").startswith(wanted)]
    assert len(found) == 6, "two phrase lists, three limits, one cell size"
    for line in found:
        assert "# PLACEHOLDER: teacher to confirm" in line, line


@pytest.mark.parametrize("path", NEW, ids=NEW_IDS)
def test_the_text_of_each_new_rule_states_the_same_values_as_its_fields(path):
    t = load_template(path)
    assert "8000 characters" in t.clause("LS-1").text
    assert "12 edits" in t.clause("LS-1").text
    assert "1500 characters" in t.clause("LS-1").text
    assert "tracks" in t.clause("WD-2").text and "ability" in t.clause("WD-2").text
    assert "pathway" in t.clause("WD-3").text and "career" in t.clause("WD-3").text


@pytest.mark.parametrize("path", NEW, ids=NEW_IDS)
def test_the_dumped_template_carries_the_lessons_for_the_database(path):
    dumped = load_template(path).model_dump(mode="json")
    assert dumped["lessons"][0]["max_edits"] == 12
    assert dumped["wording"][1]["banned_phrases"] == TRACKS
    old = load_template(V1).model_dump(mode="json")
    assert old["lessons"] == [] and old["wording"][0]["banned_phrases"] is None


# ------------------------------------------------------------------ the lesson reader
@pytest.mark.parametrize("path", NEW, ids=NEW_IDS)
def test_the_lesson_reader_accepts_the_new_files_and_reads_every_value_from_them(path):
    r = reader(path)
    name = path.stem
    assert (r.max_notes_chars, r.max_edits, r.max_edit_chars) == (8000, 12, 1500)
    assert r.limits_clause == "LS-1"
    assert (r.min_group, r.group_clause) == (GROUP[name], "GM-1")
    assert (r.min_cell, r.cell_clause) == (CELL[name], "GM-2")
    assert r.terms_per_year == 3
    assert r.foundation_areas == ("literacy", "numeracy")
    assert r.level_named("below_expected_range").clause_id == "LV-1"
    by_clause = {w.clause_id: w for w in r.wording}
    assert by_clause["WD-1"].words == tuple(LABELS)
    assert by_clause["WD-2"].phrases == tuple(TRACKS)
    assert by_clause["WD-3"].phrases == tuple(PATHWAY)
    assert r.range_clause[("Form 3", "literacy")] == "ST-3"
    assert r.selection_problem(("Form 3", "literacy", 2)) is None


@pytest.mark.parametrize("path", [V1, V2], ids=["v1", "v2-strict"])
def test_the_lesson_reader_refuses_the_old_files(path):
    template = load_template(path)
    with pytest.raises(LessonsUnavailable, match="lessons"):
        lesson_rules_from_content(template.model_dump(mode="json"), path.stem, "a" * 64)
    # and as stored before the schema change: no `lessons` key at all
    stored = template.model_dump(mode="json")
    del stored["lessons"]
    with pytest.raises(LessonsUnavailable, match="lessons"):
        lesson_rules_from_content(stored, path.stem, "a" * 64)


# ------------------------------------------------------------------ the rows the loader stores
@pytest.mark.parametrize("path", NEW, ids=NEW_IDS)
def test_the_loader_stores_every_rule_including_the_lessons_rule(path):
    from mdeis import rulebook

    template = load_template(path)
    rows = rulebook._rule_rows(template)
    assert [r[0] for r in rows] == [c.id for c in template.clauses()]
    ls = next(r for r in rows if r[0] == "LS-1")
    assert ls[1] == "LS"
    assert ls[4]["max_notes_chars"] == 8000 and ls[4]["max_edits"] == 12
    assert ls[4]["max_edit_chars"] == 1500


# ------------------------------------------------------------------ what the loader refuses
def test_a_template_with_an_empty_lessons_section_still_loads():
    data = raw(V1)
    data["lessons"] = []
    assert parse_template(data).lessons == []


@pytest.mark.parametrize("param", ["max_notes_chars", "max_edits", "max_edit_chars"])
def test_a_lessons_section_must_set_each_limit(param):
    data = raw()
    del rule(data, "lessons", "LS-1")[param]
    rejects(data, f"no lessons rule sets {param}")


@pytest.mark.parametrize(
    "param,other", [("max_notes_chars", 9000), ("max_edits", 3), ("max_edit_chars", 99)]
)
def test_each_limit_is_set_by_exactly_one_rule(param, other):
    data = raw()
    data["lessons"].append({"id": "LS-2", param: other, "text": "A second limit rule."})
    rejects(data, f"{param} is set by both LS-1 and LS-2")


@pytest.mark.parametrize("param", ["max_notes_chars", "max_edits", "max_edit_chars"])
@pytest.mark.parametrize("bad", [0, -1, 2.5, "8000", True, None])
def test_a_bad_limit_is_refused(param, bad):
    data = raw()
    rule(data, "lessons", "LS-1")[param] = bad
    with pytest.raises(TemplateError):
        parse_template(data)


def test_a_misspelt_lesson_field_is_an_error():
    data = raw()
    rule(data, "lessons", "LS-1")["max_edit"] = 3
    with pytest.raises(TemplateError, match="max_edit"):
        parse_template(data)


def test_a_lesson_rule_needs_text_and_an_ls_clause_id():
    data = raw()
    del rule(data, "lessons", "LS-1")["text"]
    rejects(data, "has no text")
    wrong = raw()
    rule(wrong, "lessons", "LS-1")["id"] = "WD-9"
    rejects(wrong, "must start with LS-")
    clash = raw()
    clash["lessons"].append({"id": "LS-1", "text": "Again."})
    rejects(clash, "duplicate clause ID LS-1")


def test_a_clause_id_in_another_section_cannot_reuse_an_lessons_id():
    data = raw()
    rule(data, "wording", "WD-2")["id"] = "LS-1"
    rejects(data, "must start with WD-")


def test_an_empty_phrase_list_is_refused():
    data = raw()
    rule(data, "wording", "WD-2")["banned_phrases"] = []
    with pytest.raises(TemplateError, match="banned_phrases"):
        parse_template(data)


def test_a_wording_rule_must_carry_words_or_phrases():
    data = raw()
    del rule(data, "wording", "WD-2")["banned_phrases"]
    rejects(data, "WD-2 must carry banned or banned_phrases")


def test_a_rule_may_carry_both_words_and_phrases_and_banned_is_still_required_once():
    both = raw()
    rule(both, "wording", "WD-1")["banned_phrases"] = ["a phrase"]
    wd1 = parse_template(both).wording[0]
    assert wd1.banned == LABELS and wd1.banned_phrases == ["a phrase"]
    twice = raw()
    rule(twice, "wording", "WD-2")["banned"] = ["pet"]
    rejects(twice, "banned is set by both WD-1 and WD-2")
    none = raw()
    del rule(none, "wording", "WD-1")["banned"]
    rule(none, "wording", "WD-1")["banned_phrases"] = ["a phrase"]
    rejects(none, "no wording rule sets banned")


def test_the_class_view_checks_still_hold_in_a_file_that_also_has_lessons():
    data = raw()
    rule(data, "group_minimum", "GM-2")["min_cell_size"] = 11
    rejects(data, "min_cell_size")
    gone = raw()
    gone["group_minimum"] = [r for r in gone["group_minimum"] if r["id"] != "GM-3"]
    rejects(gone, "complementary_suppression")
