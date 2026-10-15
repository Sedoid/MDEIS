"""The school's rules, read straight from a template YAML file.

This is a second, independent reader of the template. It does not import anything from src/mdeis,
so a bug in the system's own template loader cannot hide a wrong expected answer. Every number the
generator and the answer key use comes from the YAML through this module.
"""

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import yaml


def exact(value):
    """A YAML number as an exact fraction (2.5 is 5/2, not a float)."""
    return Fraction(str(value))


@dataclass(frozen=True)
class Rules:
    version: str  # the file name without .yaml, for example "v1"
    institution_name: str
    institution_code: str
    scale_max: Fraction
    subjects: dict  # subject name -> coefficient (Fraction), in the template's order
    areas: dict  # area name -> list of subject names
    ranges: dict  # (stage, area) -> (low, high)
    stage_weights: dict  # stage -> weight
    grade_bands: list  # [(grade, minimum)] from the highest down
    pass_mark: Fraction
    terms_per_year: int
    min_scores: int
    window_terms: int
    move_threshold: Fraction
    gap_threshold: Fraction
    min_group: int
    clause: dict  # name of a rule value -> clause id that states it
    range_clause: dict  # (stage, area) -> the ST-n clause that states the range
    area_clause: dict  # area -> the SK-n clause that lists its subjects


def load_rules(path):
    path = Path(path)
    d = yaml.safe_load(path.read_text(encoding="utf-8"))
    one = lambda section: d[section][0]  # noqa: E731  (each of these sections has one rule)
    trend = {r["id"]: r for r in d["trend"]}
    calc = {r["id"]: r for r in d["calculations"]}
    scale = {r["id"]: r for r in d["scales"]}
    subjects = {k: exact(v) for k, v in one("coefficients")["subjects"].items()}
    return Rules(
        version=path.stem,
        institution_name=d["institution"]["name"],
        institution_code=d["institution"]["code"],
        scale_max=exact(scale["SC-1"]["max"]),
        subjects=subjects,
        areas={r["area"]: list(r["subjects"]) for r in d["areas"]},
        ranges={
            (r["stage"], r["area"]): (exact(r["range"][0]), exact(r["range"][1]))
            for r in d["stage_expectations"]
        },
        stage_weights={k: exact(v) for k, v in one("stage_weights")["stages"].items()},
        grade_bands=[(b["grade"], exact(b["min"])) for b in scale["SC-2"]["grade_bands"]],
        pass_mark=exact(scale["SC-3"]["pass_mark"]),
        terms_per_year=int(calc["CA-1"]["terms_per_year"]),
        min_scores=int(trend["TR-1"]["min_scores"]),
        window_terms=int(trend["TR-1"]["window_terms"]),
        move_threshold=exact(trend["TR-2"]["move_threshold"]),
        gap_threshold=exact(trend["TR-4"]["gap_direction_threshold"]),
        min_group=int(one("group_minimum")["min_group_size"]),
        range_clause={(r["stage"], r["area"]): r["id"] for r in d["stage_expectations"]},
        area_clause={r["area"]: r["id"] for r in d["areas"]},
        clause={
            "evidence": "TR-1",
            "move": "TR-2",
            "missing": "TR-3",
            "gap_direction": "TR-4",
            "stage_weights": "SW-1",
            "window_average": "CA-4",
            "gap": "CA-5",
            "skill_value": "CA-3",
        },
    )


def stage_range_clause(path, stage, area):
    """The clause id (ST-n) that states the range for a stage and area."""
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    for r in d["stage_expectations"]:
        if r["stage"] == stage and r["area"] == area:
            return r["id"]
    raise KeyError((stage, area))
