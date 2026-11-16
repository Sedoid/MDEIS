"""The class-view rules in the criteria template: GM-2 (smallest count shown) and GM-3 (the
complementary rule), in the new version files v1.1.yaml and v2.1-strict.yaml.

No database. Expected values are typed by hand from the four files. v1.yaml and v2-strict.yaml are
not changed by this work, and one test here proves they still load without the new rules.
"""

import copy
import re
from dataclasses import fields
from pathlib import Path

import pytest
import yaml

from mdeis.core.rules import rules_from_content
from mdeis.core.template import TemplateError, load_template, parse_template

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "templates"
V1, V11 = TEMPLATES / "v1.yaml", TEMPLATES / "v1.1.yaml"
V2, V21 = TEMPLATES / "v2-strict.yaml", TEMPLATES / "v2.1-strict.yaml"
NEW = [V11, V21]
NEW_IDS = ["v1.1", "v2.1-strict"]
CELL = {"v1.1": 5, "v2.1-strict": 10}  # hand-typed from the two files (PLACEHOLDER values)
GROUP = {"v1.1": 10, "v2.1-strict": 15}
FIVE = [
    "below_expected_range",
    "within_expected_range",
    "above_expected_range",
    "not_enough_evidence",
    "blocked",
]
OLD_METHOD = "smallest_shown_above_zero_then_first_in_order"


def raw(path=V11) -> dict:
    return copy.deepcopy(yaml.safe_load(path.read_text(encoding="utf-8")))


def rule(data, rule_id):
    return next(r for r in data["group_minimum"] if r["id"] == rule_id)


def rejects(data, message):
    with pytest.raises(TemplateError, match=re.escape(message)):
        parse_template(data)


def rules_of(path):
    template = load_template(path)
    return rules_from_content(template.model_dump(mode="json"), path.stem, "0" * 64)


# --------------------------------------------------------------------------- the files
@pytest.mark.parametrize("path", NEW, ids=NEW_IDS)
def test_the_new_files_load(path):
    template = load_template(path)
    assert template.institution.code == "OCC"
    assert [r.id for r in template.group_minimum] == ["GM-1", "GM-2", "GM-3"]


def test_the_old_files_are_untouched_and_still_load_without_the_new_rules():
    for path in (V1, V2):
        template = load_template(path)
        assert [r.id for r in template.group_minimum] == ["GM-1"], path.name
    assert "GM-2" not in V1.read_text(encoding="utf-8")
    assert "GM-2" not in V2.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "old,new", [(V1, V11), (V2, V21)], ids=["v1 to v1.1", "v2-strict to v2.1-strict"]
)
def test_a_new_file_differs_from_its_old_file_only_by_the_new_rules_and_its_own_record(old, new):
    before, after = raw(old), raw(new)
    changed = {k for k in before if before[k] != after[k]}
    # The lesson-adapter rules (WD-2, WD-3 and the `lessons` section) were merged into the same
    # files later; test_template_lessons.py pins those. Everything else is as it was.
    assert changed == {"version", "change_log", "group_minimum", "wording"}
    assert set(after) - set(before) == {"lessons"}
    assert after["wording"][0] == before["wording"][0], "WD-1 is the same"
    assert after["wording"][: len(before["wording"])] == before["wording"]
    assert after["group_minimum"][0] == before["group_minimum"][0], "GM-1 is the same"
    assert len(after["change_log"]) == len(before["change_log"]) + 1
    assert after["change_log"][: len(before["change_log"])] == before["change_log"]
    assert after["version"] != before["version"]


def test_the_clause_ids_are_the_old_ids_plus_gm_2_and_gm_3_and_the_lesson_rules():
    old = [c.id for c in load_template(V1).clauses()]
    new = [c.id for c in load_template(V11).clauses()]
    assert [i for i in new if i not in ("WD-2", "WD-3", "LS-1")] == old + ["GM-2", "GM-3"]
    assert [c.id for c in load_template(V21).clauses()] == new


@pytest.mark.parametrize("path", NEW, ids=NEW_IDS)
def test_gm_2_names_the_cell_size_and_its_text_states_the_same_number(path):
    template = load_template(path)
    gm2 = template.clause("GM-2")
    value = CELL[path.stem]
    assert next(r for r in template.group_minimum if r.id == "GM-2").min_cell_size == value
    assert f"fewer than {value} learners" in gm2.text
    assert "more than 0" in gm2.text and "hidden" in gm2.text


@pytest.mark.parametrize("path", NEW, ids=NEW_IDS)
def test_a_cell_is_never_larger_than_the_smallest_group_that_may_be_shown(path):
    template = load_template(path)
    by_id = {r.id: r for r in template.group_minimum}
    assert by_id["GM-1"].min_group_size == GROUP[path.stem]
    assert by_id["GM-2"].min_cell_size <= by_id["GM-1"].min_group_size


@pytest.mark.parametrize("path", NEW, ids=NEW_IDS)
def test_gm_3_carries_the_five_bins_in_order_and_its_text_says_the_zero_rule(path):
    template = load_template(path)
    gm3 = next(r for r in template.group_minimum if r.id == "GM-3")
    assert gm3.bin_order == FIVE
    assert gm3.complementary_suppression == OLD_METHOD
    assert gm3.coverage_when_hidden == "withheld"
    text = " ".join(gm3.text.split())
    # The text must say that an empty count is never the one chosen, and that it is shown.
    assert "A count of 0 is never chosen to be hidden" in text
    assert "A count of 0 is shown" in text
    assert "smallest of the other shown counts that is more than 0" in text
    assert "comes first in the order above" in text
    # It names the five bins in the order of bin_order.
    words = [
        "below the expected range",
        "within the expected range",
        "above the expected range",
        "not enough evidence",
        "blocked",
    ]
    positions = [text.index(w) for w in words]
    assert positions == sorted(positions)
    assert "never says what they were" in text
    assert "enough evidence" in text and "not shown either" in text, "coverage is withheld"


# ------------------------------------------------------------------- the rules the tools read
@pytest.mark.parametrize("path", NEW, ids=NEW_IDS)
def test_the_rules_carry_the_class_view_settings_from_the_file(path):
    r = rules_of(path)
    assert r.min_group == GROUP[path.stem]
    assert r.min_cell == CELL[path.stem]
    assert r.bin_order == FIVE
    assert r.complementary == OLD_METHOD
    assert r.coverage_when_hidden == "withheld"
    assert r.clause["group_minimum"] == "GM-1"
    assert r.clause["cell_minimum"] == "GM-2"
    assert r.clause["complementary"] == "GM-3"
    assert r.foundation_areas == ["literacy", "numeracy"]


@pytest.mark.parametrize("path", [V1, V2], ids=["v1", "v2-strict"])
def test_the_old_rulebooks_give_no_class_view_settings(path):
    r = rules_of(path)
    assert r.min_cell is None and r.bin_order is None and r.complementary is None
    assert r.coverage_when_hidden is None
    assert "cell_minimum" not in r.clause and "complementary" not in r.clause
    assert r.foundation_areas == ["literacy", "numeracy"], "foundation areas are read for every one"


def test_the_levels_are_found_by_name_with_their_clause_and_statement():
    r = rules_of(V11)
    assert r.level_clause == {
        "below_expected_range": "LV-1",
        "within_expected_range": "LV-2",
        "above_expected_range": "LV-3",
        "insufficient_evidence": "LV-4",
    }
    assert r.level_statement["insufficient_evidence"].startswith("There are too few term scores")
    assert "last 3 term scores" in r.level_statement["below_expected_range"]
    assert "last 4 term scores" in rules_of(V21).level_statement["below_expected_range"]


def test_everything_else_in_the_rules_is_the_same_as_the_old_file():
    skip = {
        "version", "sha256", "min_cell", "bin_order", "complementary", "clause",
        "coverage_when_hidden",
    }  # fmt: skip
    for old, new in ((V1, V11), (V2, V21)):
        a, b = rules_of(old), rules_of(new)
        for f in fields(a):
            if f.name in skip:
                continue
            assert getattr(a, f.name) == getattr(b, f.name), f.name


# ------------------------------------------------------------------- what the loader refuses
@pytest.mark.parametrize("bad", [0, -1, 2.5, "5", True, None])
def test_a_bad_cell_size_is_refused(bad):
    data = raw()
    rule(data, "GM-2")["min_cell_size"] = bad
    with pytest.raises(TemplateError):
        parse_template(data)


def test_a_cell_size_larger_than_the_group_minimum_is_refused():
    data = raw()
    rule(data, "GM-2")["min_cell_size"] = rule(data, "GM-1")["min_group_size"] + 1
    rejects(data, "min_cell_size")


def test_the_class_view_rules_come_together_or_not_at_all():
    no_cell = raw()
    no_cell["group_minimum"] = [r for r in no_cell["group_minimum"] if r["id"] != "GM-2"]
    rejects(no_cell, "min_cell_size")
    no_rule = raw()
    no_rule["group_minimum"] = [r for r in no_rule["group_minimum"] if r["id"] != "GM-3"]
    rejects(no_rule, "complementary_suppression")


def test_the_cell_size_and_the_complementary_rule_are_each_set_by_exactly_one_rule():
    twice = raw()
    twice["group_minimum"].append({"id": "GM-4", "min_cell_size": 6, "text": "A second cell rule."})
    rejects(twice, "min_cell_size is set by both")
    again = raw()
    extra = copy.deepcopy(rule(again, "GM-3"))
    extra["id"] = "GM-4"
    again["group_minimum"].append(extra)
    rejects(again, "complementary_suppression is set by both")


@pytest.mark.parametrize(
    "bins",
    [
        FIVE[:-1],  # one missing
        FIVE + ["extra"],  # one too many
        [*FIVE[:-1], "below_expected_range"],  # a name twice, one missing
        ["below", "within", "above", "not_enough_evidence", "blocked"],  # wrong names
        [],
        "below_expected_range",
    ],
    ids=["missing one", "extra", "duplicate", "wrong names", "empty", "not a list"],
)
def test_a_bin_order_that_is_not_the_five_names_once_each_is_refused(bins):
    data = raw()
    rule(data, "GM-3")["bin_order"] = bins
    with pytest.raises(TemplateError, match="bin_order"):
        parse_template(data)


def test_a_different_order_of_the_same_five_names_is_allowed_because_the_template_owns_it():
    data = raw()
    rule(data, "GM-3")["bin_order"] = list(reversed(FIVE))
    template = parse_template(data)
    assert template.group_minimum[2].bin_order == list(reversed(FIVE))
    rules = rules_from_content(template.model_dump(mode="json"), "x", "0" * 64)
    assert rules.bin_order == list(reversed(FIVE))


@pytest.mark.parametrize(
    "field,value",
    [("complementary_suppression", "smallest_first"), ("coverage_when_hidden", "shown")],
)
def test_a_method_the_code_does_not_know_is_refused(field, value):
    data = raw()
    rule(data, "GM-3")[field] = value
    with pytest.raises(TemplateError, match=field):
        parse_template(data)


def test_the_three_gm_3_fields_must_sit_in_one_rule():
    data = raw()
    moved = rule(data, "GM-3").pop("coverage_when_hidden")
    rule(data, "GM-2")["coverage_when_hidden"] = moved
    rejects(data, "coverage_when_hidden")


def test_a_misspelt_class_view_field_is_an_error_not_a_silent_rule():
    data = raw()
    rule(data, "GM-2")["min_cell_sise"] = 5
    with pytest.raises(TemplateError, match="min_cell_sise"):
        parse_template(data)
