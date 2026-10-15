"""The rules the tools read, taken from the two rulebook files. Expected values are typed by hand
from templates/v1.yaml and templates/v2-strict.yaml (read them side by side)."""

from fractions import Fraction
from pathlib import Path

import pytest

from mdeis.core.rules import RulebookProblem, rules_from_content
from mdeis.core.template import load_template

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"


def rules(name):
    template = load_template(TEMPLATES / f"{name}.yaml")
    return rules_from_content(template.model_dump(mode="json"), name, "0" * 64)


def test_the_normal_rulebook():
    r = rules("v1")
    assert (r.min_scores, r.window_terms) == (3, 3)
    assert (r.move_threshold, r.gap_threshold) == (Fraction(1), Fraction(1, 2))
    assert r.scale_max == Fraction(20)
    assert r.clause["evidence"] == "TR-1" and r.clause["move"] == "TR-2"


def test_the_strict_rulebook_asks_for_more_and_moves_sooner():
    r = rules("v2-strict")
    assert (r.min_scores, r.window_terms) == (4, 4)
    assert (r.move_threshold, r.gap_threshold) == (Fraction(1, 2), Fraction(1, 4))


def test_subjects_areas_ranges_and_stage_weights():
    r = rules("v1")
    assert r.subjects["Mathematics"] == Fraction(4)
    assert r.subjects["Logic"] == Fraction(1)
    assert r.areas["literacy"] == ["English", "French", "Literature in English"]
    assert r.areas["numeracy"] == ["Mathematics"]
    assert r.ranges[("Form 3", "literacy")] == (Fraction(12), Fraction(16))
    assert r.range_clause[("Form 3", "literacy")] == "ST-3"
    assert r.stage_weights["Lower Sixth"] == Fraction(5, 2)
    assert r.stage_weights["Form 1"] == Fraction(1)
    assert r.grade_bands[0] == ("A+", Fraction(18))


def test_the_banned_words_come_from_the_rulebook():
    assert "weak" in rules("v1").banned_words


def test_a_rulebook_missing_a_rule_the_tools_need_is_named():
    template = load_template(TEMPLATES / "v1.yaml")
    content = template.model_dump(mode="json")
    content["trend"] = [t for t in content["trend"] if t.get("move_threshold") is None]
    with pytest.raises(RulebookProblem, match="move_threshold"):
        rules_from_content(content, "broken", "0" * 64)
