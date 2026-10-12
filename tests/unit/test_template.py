"""Unit tests for the criteria template loader (T-001).

Expected values here are typed by hand from the sample report card
(data/synthetic/samples/amara_nfor_form1_2022-2023.pdf) and from docs/TEMPLATE.md.
They are never copied from the loader's output.
"""

from __future__ import annotations

import ast
import copy
import re
from pathlib import Path

import pytest
import yaml

from mdeis.core.template import Template, TemplateError, load_template, parse_template

ROOT = Path(__file__).resolve().parents[2]
V1 = ROOT / "templates" / "v1.yaml"
V2 = ROOT / "templates" / "v2-strict.yaml"
LOADER_SOURCE = ROOT / "src" / "mdeis" / "core" / "template.py"

# Section key in the YAML -> clause ID area (docs/TEMPLATE.md).
SECTIONS = {
    "scales": "SC",
    "coefficients": "WT",
    "areas": "SK",
    "stage_expectations": "ST",
    "levels": "LV",
    "trend": "TR",
    "exceptions": "EX",
    "wording": "WD",
    "group_minimum": "GM",
}

# Printed on the sample card: subject -> coefficient (14 subjects, total 30).
CARD_COEFFICIENTS = {
    "Mathematics": 4,
    "English": 4,
    "French": 4,
    "Biology": 2,
    "Physics": 2,
    "Chemistry": 2,
    "Geography": 2,
    "History": 2,
    "Citizenship": 1,
    "Literature in English": 2,
    "Home Economics": 2,
    "Logic": 1,
    "Physical Education": 1,
    "Manual Labor": 1,
}
# Printed on the sample card: grading scale letters and lowest score of each band.
CARD_BANDS = {"A+": 18, "A": 16, "B": 14, "C": 12, "D": 10, "E": 0}


def raw(path: Path = V1) -> dict:
    """A fresh, mutable copy of a template as plain data."""
    return copy.deepcopy(yaml.safe_load(path.read_text(encoding="utf-8")))


def rule(data: dict, section: str, rule_id: str) -> dict:
    return next(r for r in data[section] if r["id"] == rule_id)


def rejects(data: dict, message: str) -> None:
    with pytest.raises(TemplateError, match=re.escape(message)):
        parse_template(data)


# --- The shipped templates -------------------------------------------------


@pytest.mark.parametrize("path", [V1, V2], ids=["v1", "v2-strict"])
def test_shipped_template_loads(path):
    t = load_template(path)
    assert isinstance(t, Template)
    assert t.institution.code == "OCC"


def test_both_versions_load_side_by_side():
    a, b = load_template(V1), load_template(V2)
    assert a is not b
    assert a.version != b.version
    # loading the second did not disturb the first
    assert a.clause("TR-2").text != b.clause("TR-2").text


@pytest.mark.parametrize("path", [V1, V2], ids=["v1", "v2-strict"])
def test_every_rule_has_text_and_unique_id(path):
    t = load_template(path)
    clauses = t.clauses()
    ids = [c.id for c in clauses]
    assert len(ids) == len(set(ids))
    assert all(c.text.strip() for c in clauses)
    # every clause area of docs/TEMPLATE.md is present
    assert {c.area for c in clauses} == set(SECTIONS.values())


def test_clause_ids_identical_across_v1_and_v2_strict():
    ids1 = [c.id for c in load_template(V1).clauses()]
    ids2 = [c.id for c in load_template(V2).clauses()]
    assert ids1 == ids2


@pytest.mark.parametrize("path", [V1, V2], ids=["v1", "v2-strict"])
def test_card_subjects_coefficients_and_bands(path):
    t = load_template(path)
    merged: dict[str, int] = {}
    for wt in t.coefficients:
        merged.update(wt.subjects)
    assert merged == CARD_COEFFICIENTS
    assert len(merged) == 14
    assert sum(merged.values()) == 30  # TOTAL printed on the card

    bands = [b for s in t.scales if s.grade_bands for b in s.grade_bands]
    assert {b.grade: b.min for b in bands} == CARD_BANDS
    scale = next(s for s in t.scales if s.type == "out_of")
    assert scale.max == 20


@pytest.mark.parametrize("path", [V1, V2], ids=["v1", "v2-strict"])
def test_foundation_areas_and_stages(path):
    t = load_template(path)
    assert t.foundation_areas == ["literacy", "numeracy"]
    sk = {a.area: set(a.subjects) for a in t.areas}
    assert sk == {
        "literacy": {"English", "French", "Literature in English"},
        "numeracy": {"Mathematics"},
    }
    pairs = {(s.stage, s.area) for s in t.stage_expectations}
    assert pairs == {
        (stage, area)
        for stage in ("Form 1", "Form 2", "Form 3")
        for area in ("literacy", "numeracy")
    }


def test_method_refs_on_methodology_rules():
    for path in (V1, V2):
        t = load_template(path)
        assert t.clause("TR-1").method_ref == "6.4"
        assert t.clause("TR-2").method_ref == "6.3"
        assert t.clause("TR-3").method_ref == "4"
        assert t.clause("EX-1").method_ref == "6.2"
        assert t.clause("SC-1").method_ref is None


def test_v2_strict_is_stricter_than_v1():
    t1, t2 = load_template(V1), load_template(V2)

    def trend(t: Template) -> dict:
        out: dict = {}
        for r in t.trend:
            out.update(r.model_dump(exclude={"id", "text", "method_ref"}, exclude_none=True))
        return out

    p1, p2 = trend(t1), trend(t2)
    assert p2["min_scores"] > p1["min_scores"]
    assert p2["min_terms"] > p1["min_terms"]
    assert p2["move_threshold"] < p1["move_threshold"]
    assert p2["gap_direction_threshold"] < p1["gap_direction_threshold"]
    r1 = {(s.stage, s.area): s.range for s in t1.stage_expectations}
    r2 = {(s.stage, s.area): s.range for s in t2.stage_expectations}
    assert r1.keys() == r2.keys()
    for key in r1:
        assert r2[key][0] > r1[key][0] and r2[key][1] > r1[key][1], key
    assert t2.group_minimum[0].min_group_size > t1.group_minimum[0].min_group_size


@pytest.mark.parametrize("path", [V1, V2], ids=["v1", "v2-strict"])
def test_no_banned_word_in_any_rule_text(path):
    t = load_template(path)
    banned = [w for r in t.wording for w in (r.banned or [])]
    assert banned, "WD rules must list banned words"
    for c in t.clauses():
        if c.area == "WD":
            continue  # the wording rule may describe what it bans
        for word in banned:
            assert not re.search(rf"\b{re.escape(word)}\b", c.text, re.I), (c.id, word)


@pytest.mark.parametrize("path", [V1, V2], ids=["v1", "v2-strict"])
def test_placeholders_are_marked_in_the_file(path):
    text = path.read_text(encoding="utf-8")
    assert "# FROM SAMPLE CARD OCC 2022/23: teacher to confirm" in text
    assert "# PLACEHOLDER: teacher to confirm" in text


def test_clause_lookup():
    t = load_template(V1)
    assert t.clause("TR-2").id == "TR-2"
    with pytest.raises(KeyError):
        t.clause("TR-99")


# --- No threshold in Python ------------------------------------------------


def test_no_threshold_literals_in_loader_source():
    """Only 0 and 1 may appear as numbers (non-negative and positive checks)."""
    tree = ast.parse(LOADER_SOURCE.read_text(encoding="utf-8"))
    found = {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, (int, float))
        and not isinstance(n.value, bool)
    }
    assert found <= {0, 1}, f"unexpected numeric literals: {sorted(found - {0, 1})}"


# --- Rejections ------------------------------------------------------------


@pytest.mark.parametrize("section,area", list(SECTIONS.items()))
def test_rejects_missing_required_area(section, area):
    data = raw()
    del data[section]
    rejects(data, f"missing required area {area}")


@pytest.mark.parametrize("section,area", list(SECTIONS.items()))
def test_rejects_empty_required_area(section, area):
    data = raw()
    data[section] = []
    rejects(data, f"missing required area {area}")


def test_rejects_duplicate_clause_ids():
    data = raw()
    data["trend"][1]["id"] = data["trend"][0]["id"]
    rejects(data, "duplicate clause ID TR-1")


@pytest.mark.parametrize("bad", ["delete", "", "   "])
def test_rejects_rule_without_text(bad):
    data = raw()
    if bad == "delete":
        del rule(data, "levels", "LV-1")["text"]
    else:
        rule(data, "levels", "LV-1")["text"] = bad
    rejects(data, "rule LV-1 has no text")


def test_rejects_rule_without_text_in_every_section():
    for section in SECTIONS:
        data = raw()
        rid = data[section][0]["id"]
        del data[section][0]["text"]
        rejects(data, f"rule {rid} has no text")


def test_rejects_clause_id_with_wrong_prefix():
    data = raw()
    rule(data, "wording", "WD-1")["id"] = "TR-9"
    rejects(data, "clause ID TR-9 in 'wording' must start with WD-")


@pytest.mark.parametrize("bad", ["WD1", "wd-1", "WD-", "WD-x", "WD-0", "WD-01"])
def test_rejects_malformed_clause_id(bad):
    data = raw()
    rule(data, "wording", "WD-1")["id"] = bad
    rejects(data, f"clause ID {bad!r} is not of the form")


def test_rejects_unknown_scale_type():
    data = raw()
    rule(data, "scales", "SC-1")["type"] = "percent"
    rejects(data, "unknown scale type 'percent' in SC-1")


def test_rejects_scale_with_neither_type_nor_bands():
    data = raw()
    del rule(data, "scales", "SC-1")["type"]
    del rule(data, "scales", "SC-1")["max"]
    rejects(data, "scale SC-1 needs either a type with max, or grade_bands")


def test_rejects_scale_with_both_type_and_bands():
    data = raw()
    rule(data, "scales", "SC-1")["grade_bands"] = [{"grade": "A", "min": 0}]
    rejects(data, "scale SC-1 needs either a type with max, or grade_bands")


def test_rejects_scale_type_without_max():
    data = raw()
    del rule(data, "scales", "SC-1")["max"]
    rejects(data, "scale SC-1 needs either a type with max, or grade_bands")


def test_rejects_non_positive_scale_max():
    data = raw()
    rule(data, "scales", "SC-1")["max"] = 0
    rejects(data, "max")


def test_rejects_bands_that_do_not_start_at_zero():
    data = raw()
    bands = rule(data, "scales", "SC-2")["grade_bands"]
    bands[:] = [b for b in bands if b["min"] != 0]  # drop E: nothing covers 0 to 10
    rejects(data, "grade bands in SC-2 are not contiguous from 0")


def test_rejects_overlapping_bands():
    data = raw()
    bands = rule(data, "scales", "SC-2")["grade_bands"]
    bands.append({"grade": "A-", "min": 16})  # same lower edge as A
    rejects(data, "grade bands in SC-2 are not contiguous: two bands start at 16")


def test_rejects_band_above_scale_maximum():
    data = raw()
    rule(data, "scales", "SC-2")["grade_bands"].append({"grade": "S", "min": 21})
    rejects(data, "grade bands in SC-2 are not contiguous: band S starts at 21")


def test_rejects_negative_band_start():
    data = raw()
    rule(data, "scales", "SC-2")["grade_bands"].append({"grade": "F", "min": -1})
    rejects(data, "grade bands in SC-2 are not contiguous: band F starts at -1")


def test_rejects_repeated_grade_letter():
    data = raw()
    rule(data, "scales", "SC-2")["grade_bands"].append({"grade": "A", "min": 17})
    rejects(data, "grade A appears twice in SC-2")


def test_rejects_subject_in_two_areas():
    data = raw()
    rule(data, "areas", "SK-2")["subjects"].append("English")
    rejects(data, "subject English is in two areas: literacy and numeracy")


def test_rejects_subject_without_coefficient():
    data = raw()
    rule(data, "areas", "SK-2")["subjects"].append("Astronomy")
    rejects(data, "subject Astronomy in SK-2 has no coefficient")


@pytest.mark.parametrize("bad", [0, -1, 2.5, 2.0, "2", True, None])
def test_rejects_bad_coefficient(bad):
    data = raw()
    rule(data, "coefficients", "WT-1")["subjects"]["Biology"] = bad
    rejects(data, "coefficient for Biology in WT-1 must be a positive whole number")


def test_rejects_subject_with_two_coefficients():
    data = raw()
    data["coefficients"].append(
        {"id": "WT-2", "subjects": {"Biology": 3}, "text": "Biology has coefficient 3."}
    )
    rejects(data, "subject Biology has a coefficient in both WT-1 and WT-2")


def test_rejects_stage_range_outside_scale():
    data = raw()
    rule(data, "stage_expectations", "ST-1")["range"] = [10, 21]
    rejects(data, "range of ST-1 must lie between 0 and 20")


def test_rejects_stage_range_not_low_to_high():
    data = raw()
    rule(data, "stage_expectations", "ST-1")["range"] = [14, 10]
    rejects(data, "range of ST-1 must run from a lower to a higher score")


def test_rejects_stage_area_not_foundation():
    data = raw()
    rule(data, "stage_expectations", "ST-1")["area"] = "history"
    rejects(data, "ST-1 names area 'history', which is not a foundation area")


def test_rejects_repeated_stage_and_area():
    data = raw()
    first = rule(data, "stage_expectations", "ST-1")
    second = rule(data, "stage_expectations", "ST-2")
    second["stage"], second["area"] = first["stage"], first["area"]
    rejects(data, "ST-1 and ST-2 both set the range for Form 1 literacy")


def test_rejects_foundation_area_without_subjects():
    data = raw()
    data["foundation_areas"].append("science")
    rejects(data, "foundation area 'science' has no SK rule")


def test_rejects_sk_area_not_foundation():
    data = raw()
    data["foundation_areas"].remove("numeracy")
    rejects(data, "SK-2 names area 'numeracy', which is not in foundation_areas")


def test_rejects_missing_trend_parameter():
    data = raw()
    del rule(data, "trend", "TR-2")["move_threshold"]
    rejects(data, "no trend rule sets move_threshold")


def test_rejects_trend_parameter_set_twice():
    data = raw()
    rule(data, "trend", "TR-4")["min_terms"] = 3
    rejects(data, "min_terms is set by both TR-1 and TR-4")


@pytest.mark.parametrize(
    "field,bad",
    [("min_scores", 0), ("min_scores", 2.5), ("move_threshold", -1), ("min_terms", True)],
)
def test_rejects_bad_trend_values(field, bad):
    data = raw()
    for r in data["trend"]:
        if field in r:
            r[field] = bad
    with pytest.raises(TemplateError):
        parse_template(data)


def test_rejects_unknown_missing_term_handling():
    data = raw()
    rule(data, "trend", "TR-3")["missing_term"] = "treat_as_zero"
    with pytest.raises(TemplateError, match="missing_term"):
        parse_template(data)


def test_rejects_unknown_field_so_typos_are_caught():
    data = raw()
    rule(data, "trend", "TR-2")["move_treshold"] = 1.0
    with pytest.raises(TemplateError, match="move_treshold"):
        parse_template(data)


def test_rejects_bad_group_minimum():
    data = raw()
    rule(data, "group_minimum", "GM-1")["min_group_size"] = 0
    with pytest.raises(TemplateError, match="min_group_size"):
        parse_template(data)


def test_rejects_empty_banned_list():
    data = raw()
    rule(data, "wording", "WD-1")["banned"] = []
    with pytest.raises(TemplateError, match="banned"):
        parse_template(data)


# --- method_ref ------------------------------------------------------------


def test_method_ref_is_kept_as_written():
    data = raw()
    rule(data, "scales", "SC-1")["method_ref"] = "6.3.B"
    rule(data, "levels", "LV-1")["method_ref"] = "M-4.S3"
    t = parse_template(data)
    assert t.clause("SC-1").method_ref == "6.3.B"
    assert t.clause("LV-1").method_ref == "M-4.S3"


def test_method_ref_is_optional():
    data = raw()
    for section in SECTIONS:
        for r in data[section]:
            r.pop("method_ref", None)
    t = parse_template(data)
    assert all(c.method_ref is None for c in t.clauses())


@pytest.mark.parametrize("bad", [6.4, 4, ""])
def test_method_ref_must_be_a_non_empty_string(bad):
    data = raw()
    rule(data, "trend", "TR-1")["method_ref"] = bad
    rejects(data, "method_ref of TR-1 must be a quoted, non-empty string")


# --- Files and shape -------------------------------------------------------


def test_rejects_missing_file(tmp_path):
    with pytest.raises(TemplateError, match="cannot read template"):
        load_template(tmp_path / "nope.yaml")


def test_rejects_invalid_yaml(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("scales: [unclosed", encoding="utf-8")
    with pytest.raises(TemplateError, match="not valid YAML"):
        load_template(p)


def test_rejects_non_mapping_top_level(tmp_path):
    p = tmp_path / "list.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(TemplateError, match="must be a mapping"):
        load_template(p)


def test_rejects_missing_top_level_field():
    data = raw()
    del data["institution"]
    with pytest.raises(TemplateError, match="institution"):
        parse_template(data)


def test_loader_does_not_modify_its_input():
    data = raw()
    before = copy.deepcopy(data)
    parse_template(data)
    assert data == before
