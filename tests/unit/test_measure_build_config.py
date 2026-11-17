"""The build rules file (templates/measure-build-v1.yaml) and the code that reads it (T-071).
No database. The expected homes are typed by hand from src/mdeis/core/template.py: the field of a
hand-written rule that holds each plain quantity."""

import copy
import re
from pathlib import Path

import pytest
import yaml

from mdeis.core.template import MEASURE_UNITS
from mdeis.measure_build import config
from mdeis.measure_build.config import BuildRules, BuildRulesError

ROOT = Path(__file__).resolve().parents[2]
FILE = ROOT / "templates" / "measure-build-v1.yaml"
PACKAGE = ROOT / "src" / "mdeis" / "measure_build"
MARK = "# PROPOSED: owner to confirm"


def data():
    return yaml.safe_load(FILE.read_text(encoding="utf-8"))


def test_the_file_loads_and_holds_every_part():
    rules = config.load_build_rules(FILE)
    assert isinstance(rules, BuildRules)
    assert rules.version == "measure-build-v1"
    assert rules.repair_rounds > 0 and rules.min_document_examples > 0
    assert rules.output_kinds == ("number",)
    assert rules.record_fields == ("score",)


def test_the_homes_are_the_fields_the_template_really_has():
    rules = config.load_build_rules(FILE)
    held = {h.setting: (h.section, h.field) for h in rules.homes}
    assert held == {
        "score_scale_maximum": ("scales", "max"),
        "pass_mark": ("scales", "pass_mark"),
        "window": ("trend", "window_terms"),
        "minimum_evidence": ("trend", "min_scores"),
        "minimum_group_size": ("group_minimum", "min_group_size"),
    }
    assert {h.unit for h in rules.homes} <= set(MEASURE_UNITS)


def test_a_moved_threshold_and_a_steady_band_are_not_listed_as_homes():
    # Their boundary is in the code of the fixed tool, not in the template (T-069 section 4).
    rules = config.load_build_rules(FILE)
    assert rules.home_for("moved_threshold", "move_threshold", "steady_band") is None
    assert rules.home_for("moved_at_or_above") is None


def test_a_home_is_found_by_its_setting_or_an_alias_in_any_spelling():
    rules = config.load_build_rules(FILE)
    assert rules.home_for("Window").field == "window_terms"
    assert rules.home_for("window terms").field == "window_terms"
    assert rules.home_for("minimum-evidence").field == "min_scores"
    assert rules.home_for("Minimum scores").field == "min_scores"
    assert rules.home_for(None, "", "pass_mark").field == "pass_mark"
    assert rules.home_for("something else") is None


def test_every_value_in_the_file_is_marked_proposed():
    """A value line (a key with something after it) carries the mark, except the build version,
    the fields that follow `- setting:` inside a home (the entry's first line is marked), and the
    entries under `fixed_tools` (their header is marked)."""
    lines = FILE.read_text(encoding="utf-8").splitlines()
    in_tools, unmarked = False, []
    for number, line in enumerate(lines, start=1):
        code = line.split(" #")[0].rstrip()
        if not code.strip() or code.lstrip().startswith("#"):
            continue
        indent = len(code) - len(code.lstrip())
        if indent == 0:
            in_tools = code.startswith("fixed_tools:")
        valued = re.match(r"^\s*(?:- )?[A-Za-z_]+:\s*\S", code)
        if not valued or code.startswith("build_version:"):
            continue
        if in_tools and indent > 0:
            continue
        if re.match(r"^\s+(section|field|unit|names):", code):
            continue
        if MARK not in line:
            unmarked.append((number, line))
    assert not unmarked, unmarked
    assert next(ln for ln in lines if ln.startswith("fixed_tools:")).count(MARK) == 1


@pytest.mark.parametrize(
    "key",
    ["build_version", "document", "reference_words", "homes", "fixed_tools", "repair_rounds",
     "max_rules_per_section", "quote_min_chars", "min_document_examples", "output",
     "record_fields"],
)  # fmt: skip
def test_a_missing_part_is_refused_by_name(key):
    broken = data()
    del broken[key]
    with pytest.raises(BuildRulesError, match=key):
        BuildRules.from_dict(broken)


@pytest.mark.parametrize("key", ["repair_rounds", "max_rules_per_section", "quote_min_chars",
                                 "min_document_examples"])  # fmt: skip
def test_a_count_that_is_not_a_whole_number_above_zero_is_refused(key):
    for bad in (0, -1, "2", None, True, 1.5):
        broken = data()
        broken[key] = bad
        with pytest.raises(BuildRulesError, match=key):
            BuildRules.from_dict(broken)


def test_a_pattern_that_does_not_compile_is_refused():
    broken = data()
    broken["document"]["version_pattern"] = "(unclosed"
    with pytest.raises(BuildRulesError, match="version_pattern"):
        BuildRules.from_dict(broken)


def test_two_homes_for_one_setting_are_refused():
    broken = data()
    broken["homes"].append(copy.deepcopy(broken["homes"][0]))
    with pytest.raises(BuildRulesError, match="same setting"):
        BuildRules.from_dict(broken)


def test_the_code_holds_no_list_or_number_of_the_rules():
    # No school value, threshold or word list written in the build code. Numbers: only 0 and 1.
    import ast

    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, int):
                assert node.value in (0, 1, True, False), (path.name, node.lineno, node.value)
