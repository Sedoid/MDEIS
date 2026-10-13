"""The school's rules, read from the rulebook version stored in the database.

Every threshold, range, weight and band the tools use comes from here, and here it comes from the
`templates.content` of the version asked for (SQL on the parsed rulebook, never from a prompt). This
module holds no school value. It finds each rule by the field it carries (the rule that has
`move_threshold` is the move rule), so a renumbered rulebook still works, and it records the clause
ID of each rule so results can cite it.
"""

from dataclasses import dataclass
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
    #               window_average, skill_value, gap


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
        },
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
