"""The school's rules, read from the rulebook version stored in the database.

Every threshold, range, weight and band the tools use comes from here, and here it comes from the
`templates.content` of the version asked for (SQL on the parsed rulebook, never from a prompt). This
module holds no school value. It finds each rule by the field it carries (the rule that has
`move_threshold` is the move rule), so a renumbered rulebook still works, and it records the clause
ID of each rule so results can cite it.
"""

from dataclasses import dataclass, field
from fractions import Fraction

from .numbers import exact


class RulebookProblem(ValueError):
    """The rulebook version is missing, or lacks a rule the tools need. The message says which."""


@dataclass(frozen=True)
class Rules:
    version: str
    sha256: str
    scale_max: Fraction
    grade_bands: list  # [(grade, lowest score)], highest first
    subjects: dict  # subject -> coefficient
    areas: dict  # area -> [subjects]
    area_clause: dict  # area -> SK clause id
    ranges: dict  # (stage, area) -> (low, high)
    range_clause: dict  # (stage, area) -> ST clause id
    stage_weights: dict  # stage -> weight
    min_scores: int
    window_terms: int
    move_threshold: Fraction
    gap_threshold: Fraction
    banned_words: list
    clause: dict  # purpose -> clause id: evidence, move, missing, gap_direction, stage_weights,
    #               window_average, skill_value, gap, and group_minimum when the rulebook has one
    min_group: int | None = None  # the smallest group a class view may show (GM-1), if set
    # The class-view rules (GM-2, GM-3). All None in a rulebook that has none; the class view then
    # refuses. `clause` gains "cell_minimum" and "complementary" when they are present.
    min_cell: int | None = None  # a count above 0 and below this is hidden (GM-2)
    bin_order: list | None = None  # the five bins in the order they are shown (GM-3)
    complementary: str | None = None  # how a hidden count's partner is chosen (GM-3)
    coverage_when_hidden: str | None = None  # what happens to coverage when a count is hidden
    foundation_areas: list = field(default_factory=list)  # in the template's order
    level_clause: dict = field(default_factory=dict)  # level name -> LV clause id
    level_statement: dict = field(default_factory=dict)  # level name -> the template's statement


def _find(rules, field):
    """The first rule in a list that carries `field`."""
    for rule in rules:
        if rule.get(field) is not None:
            return rule
    raise RulebookProblem(f"the rulebook has no rule with {field!r}")


def rules_from_content(content: dict, version: str, sha256: str) -> Rules:
    scales = content["scales"]
    trend = content["trend"]
    calculations = content["calculations"]
    area_rules = content["areas"]
    stage_rules = content["stage_expectations"]
    try:
        scale = _find(scales, "max")
        bands = _find(scales, "grade_bands")
        evidence = _find(trend, "min_scores")
        move = _find(trend, "move_threshold")
        missing = _find(trend, "missing_term")
        gap_direction = _find(trend, "gap_direction_threshold")
        window_average = _find(calculations, "window_average")
        skill_value = _find(calculations, "skill_term_value")
        gap = _find(calculations, "gap")
        weights = content["stage_weights"][0]
        coefficients = content["coefficients"][0]
        banned = _find(content["wording"], "banned")
    except (KeyError, IndexError) as e:
        raise RulebookProblem(f"the rulebook {version} lacks a section: {e}") from e
    group_rules = content.get("group_minimum") or []
    group = next((r for r in group_rules if r.get("min_group_size")), None)
    cell = next((r for r in group_rules if r.get("min_cell_size")), None)
    complementary = next((r for r in group_rules if r.get("complementary_suppression")), None)
    levels = content.get("levels") or []
    return Rules(
        version=version,
        sha256=sha256,
        scale_max=exact(scale["max"]),
        grade_bands=[(b["grade"], exact(b["min"])) for b in bands["grade_bands"]],
        subjects={name: exact(c) for name, c in coefficients["subjects"].items()},
        areas={r["area"]: list(r["subjects"]) for r in area_rules},
        area_clause={r["area"]: r["id"] for r in area_rules},
        ranges={
            (r["stage"], r["area"]): (exact(r["range"][0]), exact(r["range"][1]))
            for r in stage_rules
        },
        range_clause={(r["stage"], r["area"]): r["id"] for r in stage_rules},
        stage_weights={stage: exact(w) for stage, w in weights["stages"].items()},
        min_scores=int(evidence["min_scores"]),
        window_terms=int(evidence["window_terms"]),
        move_threshold=exact(move["move_threshold"]),
        gap_threshold=exact(gap_direction["gap_direction_threshold"]),
        banned_words=list(banned["banned"]),
        clause={
            "evidence": evidence["id"],
            "move": move["id"],
            "missing": missing["id"],
            "gap_direction": gap_direction["id"],
            "stage_weights": weights["id"],
            "window_average": window_average["id"],
            "skill_value": skill_value["id"],
            "gap": gap["id"],
            **({"group_minimum": group["id"]} if group else {}),
            **({"cell_minimum": cell["id"]} if cell else {}),
            **({"complementary": complementary["id"]} if complementary else {}),
        },
        min_group=int(group["min_group_size"]) if group else None,
        min_cell=int(cell["min_cell_size"]) if cell else None,
        bin_order=list(complementary["bin_order"]) if complementary else None,
        complementary=complementary["complementary_suppression"] if complementary else None,
        coverage_when_hidden=complementary.get("coverage_when_hidden") if complementary else None,
        foundation_areas=list(content.get("foundation_areas") or []),
        level_clause={lv["name"]: lv["id"] for lv in levels},
        level_statement={lv["name"]: lv["statement"] for lv in levels},
    )


def load_rules(conn, version=None) -> Rules:
    """The rules of `version`, or of the active version when none is named."""
    if version is None:
        row = conn.execute(
            "SELECT version_name, sha256, content FROM templates WHERE active"
        ).fetchone()
        if row is None:
            raise RulebookProblem("no rulebook version is active: load one first")
    else:
        row = conn.execute(
            "SELECT version_name, sha256, content FROM templates WHERE version_name = %s",
            (version,),
        ).fetchone()
        if row is None:
            raise RulebookProblem(f"no rulebook version named {version} is loaded")
    name, digest, content = row
    return rules_from_content(content, name, digest)
