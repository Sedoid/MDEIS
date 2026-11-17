"""The optional `measure_params` section (area MP) of the criteria template (T-070). No database.

One value per rule, typed (integer, rational, names), with a unit from a closed list, the source
document and section it comes from, and `confirmed_by`, null until a teacher confirms it. A template
without the section still loads (v1, v2-strict, v1.1, v2.1-strict).

Expected values are typed by hand from the T-069 proposal (the Baobab "moved" rule and the Savanna
"sustained" rules), never copied from the loader's output.
"""

import copy
import re
from pathlib import Path

import pytest
import yaml

from mdeis.core.rules import rules_from_content
from mdeis.core.template import (
    MEASURE_PARAM_TYPES,
    MEASURE_UNITS,
    OPTIONAL_SECTION_AREAS,
    TemplateError,
    load_template,
    parse_template,
)

ROOT = Path(__file__).resolve().parents[2]
T = ROOT / "templates"
V1, V11, V2, V21 = (T / n for n in ("v1.yaml", "v1.1.yaml", "v2-strict.yaml", "v2.1-strict.yaml"))
ALL_FOUR = [V1, V11, V2, V21]
SRC = ROOT / "src" / "mdeis"


def raw(path=V11) -> dict:
    return copy.deepcopy(yaml.safe_load(path.read_text(encoding="utf-8")))


def moved(**over) -> dict:
    rule = {
        "id": "MP-1",
        "name": "moved_at_or_above",
        "value_type": "integer",
        "value": 10,
        "unit": "points",
        "source_document": "BAO-METH-1.0",
        "method_ref": "6.4",
        "confirmed_by": None,
        "text": "The pattern is moved up when the change is 10 points or more.",
    }
    rule.update(over)
    return rule


def sustained() -> dict:
    return {
        "id": "MP-2",
        "name": "sustained_min_checks",
        "value_type": "integer",
        "value": 2,
        "unit": "checks",
        "source_document": "SAV-METH-1.0",
        "method_ref": "3",
        "confirmed_by": None,
        "text": (
            "Minimum checks for a sustained level: 2 recorded checks, set by the teaching staff."
        ),
    }


def order() -> dict:
    return {
        "id": "MP-3",
        "name": "checklist_order",
        "value_type": "names",
        "value": ["Starting", "Practising", "Confident", "Independent"],
        "unit": "none",
        "source_document": "SAV-METH-1.0",
        "method_ref": "3",
        "confirmed_by": None,
        "text": (
            "Checklist levels: Four levels in order, Starting, Practising, Confident and "
            "Independent, set by the teaching staff."
        ),
    }


def with_params(*rules, path=V11) -> dict:
    data = raw(path)
    data["measure_params"] = list(rules)
    return data


def rejects(data, message):
    with pytest.raises(TemplateError, match=re.escape(message)):
        parse_template(data)


# ----------------------------------------- the section is optional
@pytest.mark.parametrize("path", ALL_FOUR, ids=["v1", "v1.1", "v2-strict", "v2.1-strict"])
def test_the_shipped_templates_load_without_the_section_and_have_no_mp_clause(path):
    t = load_template(path)
    assert t.measure_params == []
    assert not [c for c in t.clauses() if c.area == "MP"]
    assert "measure_params" not in path.read_text(encoding="utf-8")


def test_the_section_is_registered_as_an_optional_area_after_lessons():
    assert OPTIONAL_SECTION_AREAS == {"lessons": "LS", "measure_params": "MP"}
    assert MEASURE_PARAM_TYPES == ("integer", "rational", "names")
    assert MEASURE_UNITS == (
        "points", "scores", "checks", "terms", "grades", "learners", "percent", "none",
    )  # fmt: skip


# ----------------------------------------- the three T-069 rules
def test_the_three_proposed_rules_load_and_are_citable_clauses():
    t = parse_template(with_params(moved(), sustained(), order()))
    assert [r.id for r in t.measure_params] == ["MP-1", "MP-2", "MP-3"]
    one, two, three = t.measure_params
    assert (one.name, one.value_type, one.value, one.unit) == (
        "moved_at_or_above", "integer", 10, "points",
    )  # fmt: skip
    assert (one.source_document, one.method_ref, one.confirmed_by) == ("BAO-METH-1.0", "6.4", None)
    assert (two.value, two.unit, three.unit) == (2, "checks", "none")
    assert three.value == ["Starting", "Practising", "Confident", "Independent"]
    clause = t.clause("MP-1")
    assert (clause.area, clause.method_ref) == ("MP", "6.4")
    assert clause.text == "The pattern is moved up when the change is 10 points or more."
    ids = [c.id for c in t.clauses()]
    assert ids[-3:] == ["MP-1", "MP-2", "MP-3"], "the optional section comes after the others"
    assert ids.index("LS-1") < ids.index("MP-1")


def test_a_rational_is_a_quoted_exact_number_and_the_text_says_it_as_written():
    cases = (("3/5", "A share of 3/5 or more counts."), ("0.6", "A share of 0.6 counts."))
    for value, text in cases:
        rule = moved(id="MP-4", name="share_at_or_above", value_type="rational", value=value)
        rule.update(unit="percent", text=text)
        t = parse_template(with_params(rule))
        assert t.measure_params[0].value == value


def test_the_same_name_from_two_different_documents_is_allowed():
    other = sustained()
    other.update(id="MP-5", name="moved_at_or_above", source_document="OTHER-1.0")
    other["text"] = "Another school counts a change of 2 as moved."
    t = parse_template(with_params(moved(), other))
    assert [r.id for r in t.measure_params] == ["MP-1", "MP-5"]


def test_a_confirmed_value_carries_the_name_of_the_teacher():
    t = parse_template(with_params(moved(confirmed_by="Ada Teacher")))
    assert t.measure_params[0].confirmed_by == "Ada Teacher"


def test_the_dumped_template_carries_the_section_for_the_database():
    dumped = parse_template(with_params(moved(), order())).model_dump(mode="json")
    assert dumped["measure_params"][0]["value"] == 10
    assert dumped["measure_params"][0]["confirmed_by"] is None
    assert dumped["measure_params"][1]["value"][1] == "Practising"
    assert load_template(V11).model_dump(mode="json")["measure_params"] == []


# ----------------------------------------- value and type
@pytest.mark.parametrize("bad", [10.0, 10.5, "10", True, False, -1, None, [10]])
def test_an_integer_must_be_a_whole_number_from_zero_up_and_unquoted(bad):
    rejects(with_params(moved(value=bad)), "value of MP-1 must be a whole number from 0 up")


def test_zero_is_a_valid_integer():
    rule = moved(value=0, text="A change of 0 points is not moved.")
    assert parse_template(with_params(rule)).measure_params[0].value == 0


@pytest.mark.parametrize("bad", [0.6, 3, "abc", "-1/2", "1e3", " 3/5", "3/", "1,5", "", True])
def test_a_rational_must_be_a_quoted_exact_number(bad):
    rule = moved(value_type="rational", value=bad, text="A share of 3/5 counts.")
    rejects(with_params(rule), "value of MP-1 must be a quoted exact number")


def test_a_rational_with_a_zero_bottom_is_refused():
    rule = moved(value_type="rational", value="1/0", text="A share of 1/0 counts.")
    rejects(with_params(rule), "has a zero bottom")


@pytest.mark.parametrize("bad", [[], "Starting", ["Starting", ""], ["A", 1], ["A\nB"], ["A", "A"]])
def test_names_must_be_a_list_of_different_one_line_texts(bad):
    rule = order()
    rule["value"] = bad
    rule["text"] = "Levels A and B and Starting and 1."
    data = with_params(rule)
    with pytest.raises(TemplateError, match="value of MP-3"):
        parse_template(data)


def test_a_names_value_must_have_the_unit_none():
    rule = order()
    rule["unit"] = "checks"
    rejects(with_params(rule), "unit of MP-3 must be none for a names value")


@pytest.mark.parametrize("bad", ["metres", "", "Points", None, 3])
def test_the_unit_must_be_in_the_closed_list(bad):
    rejects(with_params(moved(unit=bad)), "unit of MP-1 must be one of")


@pytest.mark.parametrize("bad", ["choice", "float", "", None])
def test_the_value_type_must_be_in_the_closed_list(bad):
    rejects(with_params(moved(value_type=bad)), "value_type of MP-1 must be one of")


# ----------------------------------------- the fixed fields
def test_an_unknown_field_is_an_error():
    rule = moved(extra_field="x")
    with pytest.raises(TemplateError, match="extra_field"):
        parse_template(with_params(rule))


def test_confirmed_by_must_be_present_and_null_or_a_name():
    rule = moved()
    del rule["confirmed_by"]
    rejects(with_params(rule), "must carry the key confirmed_by")
    for bad in ("", "  ", 5, True):
        rejects(with_params(moved(confirmed_by=bad)), "confirmed_by of MP-1 must be null or a name")


@pytest.mark.parametrize("key", ["method_ref", "source_document"])
def test_method_ref_and_source_document_are_required_quoted_and_non_empty(key):
    for bad in ("delete", "", "  ", 6.4, None):
        rule = moved()
        if bad == "delete":
            del rule[key]
        else:
            rule[key] = bad
        with pytest.raises(TemplateError, match=key):
            parse_template(with_params(rule))


@pytest.mark.parametrize(
    "bad", ["Moved", "moved at", "moved-at", "_moved", "1moved", "", "a__b", 5]
)
def test_the_name_is_lower_case_words_joined_by_underscores(bad):
    rejects(with_params(moved(name=bad)), "name of MP-1 must be lower case words")


# ----------------------------------------- the clause IDs
def test_the_id_must_start_with_mp_and_be_unique():
    rejects(with_params(moved(id="TR-9")), "clause ID TR-9 in 'measure_params' must start with MP-")
    second = sustained()
    second["id"] = "MP-1"
    rejects(with_params(moved(), second), "duplicate clause ID MP-1")
    rejects(with_params(moved(id="MP-01")), "is not of the form AREA-n")


def test_a_mp_id_is_refused_in_another_section():
    data = raw()
    data["trend"].append({"id": "MP-1", "min_scores": 3, "text": "x"})
    rejects(data, "clause ID MP-1 in 'trend' must start with TR-")


# ----------------------------------------- no second copy
def test_the_same_name_twice_in_one_document_is_refused():
    text = "The pattern is moved down when the change is 11 points."
    other = moved(id="MP-2", value=11, text=text)
    rejects(with_params(moved(), other), "BAO-METH-1.0 gives the setting moved_at_or_above twice")


@pytest.mark.parametrize(
    "field", ["move_threshold", "pass_mark", "min_group_size", "window_terms", "min_scores", "max"]
)
def test_a_name_that_is_a_field_of_a_hand_written_rule_is_refused(field):
    rejects(with_params(moved(name=field)), "is a field of a hand-written rule")


def test_a_text_equal_to_the_text_of_another_clause_is_refused():
    same = raw()["trend"][0]["text"]  # "A trend is stated only with at least 3 term scores. ..."
    rule = moved(value=3, text=same, name="three_scores")
    rejects(with_params(rule), "repeats the text of another clause")


# ----------------------------------------- the text says the value
@pytest.mark.parametrize(
    "text",
    [
        "The pattern is moved up when the change is large.",
        "The pattern is moved up when the change is 100 points or more.",
        "The pattern is moved up when the change is 10.5 points or more.",
        "The pattern is moved up when the change is 3/10 or more.",
        "The pattern is moved up when the change is 1.10 or more.",
    ],
)
def test_the_text_must_say_the_integer_as_a_separate_number(text):
    rejects(with_params(moved(text=text)), "does not say the value 10")


def test_a_rational_not_said_as_written_is_refused():
    rule = moved(value_type="rational", value="3/5", text="A share of 0.6 counts.")
    rejects(with_params(rule), "does not say the value 3/5")


def test_every_name_must_appear_in_the_text():
    rule = order()
    rule["text"] = "Checklist levels: Starting, Practising and Confident."
    rejects(with_params(rule), "does not say the name 'Independent'")


@pytest.mark.parametrize(
    "text", ["The change is 10.", "A change of 10, or more.", "Moved at 10 points."]
)
def test_a_number_at_the_end_of_a_sentence_or_before_a_comma_counts(text):
    assert parse_template(with_params(moved(text=text))).measure_params[0].value == 10


# ----------------------------------------- the loader stores them
def test_the_rulebook_loader_makes_a_clause_row_for_each_mp_rule():
    from mdeis.rulebook import _rule_rows

    t = parse_template(with_params(moved(), order()))
    rows = {r[0]: r for r in _rule_rows(t)}
    clause_id, area, text, method_ref, params = rows["MP-1"]
    assert (clause_id, area, method_ref) == ("MP-1", "MP", "6.4")
    assert text == "The pattern is moved up when the change is 10 points or more."
    assert params == {
        "name": "moved_at_or_above",
        "value_type": "integer",
        "value": 10,
        "unit": "points",
        "source_document": "BAO-METH-1.0",
        "confirmed_by": None,
    }
    assert rows["MP-3"][4]["value"] == ["Starting", "Practising", "Confident", "Independent"]
    count = len(_rule_rows(load_template(V11)))
    assert len(_rule_rows(t)) == count + 2


# ----------------------------------------- hand-written code unchanged
def test_the_hand_written_rules_are_exactly_the_same_with_an_mp_section_present():
    plain = parse_template(raw()).model_dump(mode="json")
    added = parse_template(with_params(moved(), sustained(), order())).model_dump(mode="json")
    assert added["measure_params"] and not plain["measure_params"]
    sha = "a" * 64
    assert rules_from_content(added, "v1.1", sha) == rules_from_content(plain, "v1.1", sha)


def test_no_hand_written_code_reads_the_measure_params_section():
    own = {"template.py", "measure_params.py"}
    files = [p for p in sorted((SRC / "core").glob("*.py")) if p.name not in own]
    files += sorted((SRC / "tools").glob("*.py"))
    assert files
    for path in files:
        assert "measure_params" not in path.read_text(encoding="utf-8"), path.name
