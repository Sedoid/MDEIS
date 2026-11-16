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
