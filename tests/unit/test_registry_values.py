"""The numbers a template holds, by clause ID, for the static check's duplicate rule (no database).

Expected values are worked out by hand from templates/v1.yaml: the scale is out of 20 (SC-1), the
pass mark is 10 (SC-3), the grade bands start at 18, 16, 14, 12, 10 and 0 (SC-2), TR-1 asks for
3, 3 and 3, the move threshold is 1.0 (TR-2) and the gap threshold is 0.5 (TR-4), and the stage
weights are 1, 1, 2, 2, 2, 2.5 and 2.5 (SW-1).
"""

from pathlib import Path

import yaml

from mdeis.registry.values import template_values

V1 = yaml.safe_load(
    (Path(__file__).resolve().parents[2] / "templates" / "v1.yaml").read_text(encoding="utf-8")
)


def test_the_numbers_of_the_school_template_are_read_by_clause_with_exact_values():
    got = template_values(V1)
    assert got["SC-1"] == ["20"]
    assert got["SC-3"] == ["10"]
    assert got["SC-2"] == ["18", "16", "14", "12", "10", "0"]
    assert got["TR-1"] == ["3", "3", "3"]
    assert got["TR-2"] == ["1"]
    assert got["TR-4"] == ["1/2"]
    assert got["SW-1"] == ["1", "1", "2", "2", "2", "5/2", "5/2"]


def test_a_rule_with_no_number_is_left_out_and_the_rules_words_are_not_read():
    content = {
        "scales": [
            {"id": "SC-1", "text": "Scores are out of 20.", "max": 20},
            {"id": "SC-9", "text": "A rule with a note only.", "note": "none"},
        ]
    }
    assert template_values(content) == {"SC-1": ["20"]}


def test_a_float_is_read_as_the_exact_decimal_and_a_true_or_false_is_not_a_number():
    content = {"trend": [{"id": "TR-7", "text": "t", "bar": 0.6, "flag": True, "off": False}]}
    assert template_values(content) == {"TR-7": ["3/5"]}


def test_nested_numbers_are_found_and_a_section_that_is_missing_is_fine():
    content = {"levels": [{"id": "LV-1", "text": "t", "bands": [{"low": 4, "high": {"x": 7}}]}]}
    assert template_values(content) == {"LV-1": ["4", "7"]}
    assert template_values({}) == {}


def test_a_value_the_template_holds_is_what_the_static_check_reports_as_a_duplicate():
    from mdeis.sandbox import static_check
    from mdeis.sandbox.config import load_rules

    source = (
        'OUTPUT_KIND = "number"\n\n\ndef measure(records, params):\n'
        '    return {"value": str(len(records) * 3)}\n'
    )
    report = static_check.check_source(
        source, output_kind="number", rules=load_rules(), template_values=template_values(V1)
    )
    found = [f for f in report.findings if f.code == "duplicates_template_value"]
    # 3 is the number of terms in a year (CA-1) and the minimum for a trend (TR-1)
    assert [f.clause_ids for f in found] == [("CA-1", "TR-1")]


# --------------------------------------------------------------------------- T-070: more sections
V11 = yaml.safe_load(
    (Path(__file__).resolve().parents[2] / "templates" / "v1.1.yaml").read_text(encoding="utf-8")
)


def mp(rule_id, value_type, value, unit="points"):
    return {
        "id": rule_id, "name": "n_" + rule_id.split("-")[1], "value_type": value_type,
        "value": value, "unit": unit, "source_document": "BAO-METH-1.0", "method_ref": "6.4",
        "confirmed_by": "Ada Teacher", "text": "t",
    }  # fmt: skip


def test_the_numbers_of_the_lessons_rule_are_now_seen():
    # LS-1 in v1.1.yaml: 8000 characters of notes, 12 edits, 1500 characters per edit. The old
    # loop over the required sections only never saw them.
    got = template_values(V11)
    assert got["LS-1"] == ["8000", "12", "1500"]
    assert "LS-1" not in template_values(V1), "v1.yaml has no lessons section"


def test_the_class_view_numbers_of_v1_1_are_still_seen():
    assert template_values(V11)["GM-2"] == ["5"]


def test_a_measure_parameter_is_read_by_its_clause_id_with_an_exact_value():
    content = {
        "measure_params": [
            mp("MP-1", "integer", 10),
            mp("MP-2", "rational", "3/5", "percent"),
            mp("MP-3", "rational", "0.6", "percent"),
            mp("MP-4", "integer", 0),
        ]
    }
    assert template_values(content) == {
        "MP-1": ["10"], "MP-2": ["3/5"], "MP-3": ["3/5"], "MP-4": ["0"],
    }  # fmt: skip


def test_a_names_value_holds_no_number_and_the_other_fields_of_a_parameter_are_not_values():
    levels = mp("MP-5", "names", ["Starting", "Practising"], "none")
    levels["unit"] = "none"
    levels["method_ref"] = "3"
    assert template_values({"measure_params": [levels]}) == {}
    # the document name (BAO-METH-1.0) and the method_ref (6.4) are text, not values of the rule
    assert template_values({"measure_params": [mp("MP-6", "integer", 7)]}) == {"MP-6": ["7"]}


def test_a_literal_equal_to_a_measure_parameter_is_reported_with_its_clause_id():
    from mdeis.sandbox import static_check
    from mdeis.sandbox.config import load_rules

    source = (
        'OUTPUT_KIND = "number"\n\n\ndef measure(records, params):\n'
        '    return {"value": str(len(records) * 10)}\n'
    )
    content = {"measure_params": [mp("MP-1", "integer", 10)]}
    report = static_check.check_source(
        source, output_kind="number", rules=load_rules(), template_values=template_values(content)
    )
    found = [f for f in report.findings if f.code == "duplicates_template_value"]
    assert [f.clause_ids for f in found] == [("MP-1",)]
