"""The trend of one subject, and the level and gap of one learning area. Pure functions, no
database.

A record is a dict: record_id, year ("2025/2026"), term, stage, subject, score (a Fraction on the
card's own scale, or None for a blank term), denominator. Every number the rules need comes in
through `rules` (mdeis.core.rules). Nothing here holds a school value.

The steps follow the methodology: a subject's recent scores are its window (TR-1); the level is the
stage-weighted average of the window (CA-4); the change is the last value minus the first, and
counts as a move only when it is more than the threshold (TR-2); a skill's value in a term is the
coefficient-weighted average of its subjects (CA-3); the gap is the distance from the level to the
nearest edge of the stage's expected range (CA-5), and its direction compares the first and last
term's gaps against their own stage's ranges (TR-4).
"""

from typing import NamedTuple

from .numbers import distance_to_range, level_name, two_places, weighted_average
from .records import year_start


class SkillValue(NamedTuple):
    """A skill's value in one term, and the records it was worked out from."""

    year: str
    term: int
    stage: str
    value: object  # a Fraction, or None when a subject of the area has no score that term
    record_ids: list


class RulebookGap(ValueError):
    """The rulebook has no weight or range for something in the data. The message says what."""


def on_scale(record, rules):
    """The score converted to the rulebook's scale, or None for a blank term."""
    if record["score"] is None:
        return None
    return record["score"] * rules.scale_max / record["denominator"]


def when(record):
    """Chronological order: school year, then term."""
    return (year_start(record["year"]), record["term"])


def move_label(change, rules) -> str:
    if change > rules.move_threshold:
        return "improving"
    if change < -rules.move_threshold:
        return "declining"
    return "steady"


def _weight(rules, stage):
    weight = rules.stage_weights.get(stage)
    if weight is None:
        raise RulebookGap(f"the rulebook has no stage weight for {stage}")
    return weight


def subject_result(records, subject, rules, blocked) -> dict:
    """The trend for one subject. `blocked` is the set of subjects that may not be worked out."""
    ordered = sorted((r for r in records if r["subject"] == subject), key=when)
    evidence, move = rules.clause["evidence"], rules.clause["move"]
    base = {"skill": subject, "kind": "subject", "clause_ids": [evidence, move]}
    if subject in blocked:
        return {**base, "status": "blocked"}
    values = [(r, on_scale(r, rules)) for r in ordered if r["score"] is not None]
    if len(values) < rules.min_scores:
        return {
            **base,
            "status": "ok",
            "trend": "not_enough_evidence",
            "scores_available": len(values),
        }
    window = values[-rules.window_terms :]
    first, last = window[0][1], window[-1][1]
    level = weighted_average([(_weight(rules, r["stage"]), v) for r, v in window])
    change = last - first
    clauses = list(base["clause_ids"])
    if len({r["stage"] for r, _ in window}) > 1:
        clauses += [rules.clause["window_average"], rules.clause["stage_weights"]]
    return {
        **base,
        "status": "ok",
        "trend": move_label(change, rules),
        "change": two_places(change),
        "level": two_places(level),
        "first": two_places(first),
        "last": two_places(last),
        "window": [
            {
                "record_id": r["record_id"],
                "year": r["year"],
                "term": r["term"],
                "stage": r["stage"],
                "score": two_places(v),
            }
            for r, v in window
        ],
        "record_ids": [r["record_id"] for r, _ in window],
        "records_available": len(values),
        "clause_ids": clauses,
    }


def skill_values(records, area, rules):
    """A skill's value per term: [(year, term, stage, value or None, record ids)], in time order.

    The value is None when any subject of the area has no score in that term.
    """
    keys = sorted(
        {(r["year"], r["term"], r["stage"]) for r in records},
        key=lambda k: (year_start(k[0]), k[1]),
    )
    out = []
    for year, term, stage in keys:
        pairs, ids, complete = [], [], True
        for subject in rules.areas[area]:
            found = [
                r
                for r in records
                if r["subject"] == subject and r["year"] == year and r["term"] == term
            ]
            value = on_scale(found[0], rules) if found else None
            if value is None:
                complete = False
                break
            pairs.append((rules.subjects[subject], value))
            ids.append(found[0]["record_id"])
        value = weighted_average(pairs) if complete else None
        out.append(SkillValue(year, term, stage, value, ids))
    return out


def skill_result(records, area, rules, blocked) -> dict:
    """Level, gap and direction of the gap for one learning area."""
    base = {
        "skill": area,
        "kind": "area",
        "clause_ids": [rules.area_clause[area], rules.clause["evidence"]],
    }
    if any(s in blocked for s in rules.areas[area]):
        return {**base, "status": "blocked"}
    values = [v for v in skill_values(records, area, rules) if v.value is not None]
    if len(values) < rules.min_scores:
        return {
            **base,
            "status": "ok",
            "trend": "not_enough_evidence",
            "scores_available": len(values),
            "stage_gap": None,
        }
    window = values[-rules.window_terms :]
    level = weighted_average([(_weight(rules, v.stage), v.value) for v in window])
    first_stage, last_stage = window[0].stage, window[-1].stage
    low, high = _range(rules, last_stage, area)
    first_low, first_high = _range(rules, first_stage, area)
    gap_first = distance_to_range(window[0].value, first_low, first_high)
    gap_last = distance_to_range(window[-1].value, low, high)
    shift = gap_last - gap_first
    if shift > rules.gap_threshold:
        direction = "widening"
    elif shift < -rules.gap_threshold:
        direction = "closing"
    else:
        direction = "steady"
    change = window[-1].value - window[0].value
    clauses = base["clause_ids"] + [
        rules.range_clause[(last_stage, area)],
        rules.clause["gap"],
        rules.clause["gap_direction"],
    ]
    if first_stage != last_stage:
        clauses.append(rules.range_clause[(first_stage, area)])
    return {
        **base,
        "status": "ok",
        "trend": move_label(change, rules),
        "change": two_places(change),
        "level": two_places(level),
        "stage_gap": {
            "stage": last_stage,
            "range": [two_places(low), two_places(high)],
            "level_name": level_name(level, low, high),
            "gap": two_places(distance_to_range(level, low, high)),
            "direction": direction,
            "first_term_gap": two_places(gap_first),
            "last_term_gap": two_places(gap_last),
        },
        "window": [
            {"year": v.year, "term": v.term, "stage": v.stage, "skill_value": two_places(v.value)}
            for v in window
        ],
        "record_ids": [rid for v in window for rid in v.record_ids],
        "clause_ids": clauses,
    }


def _range(rules, stage, area):
    found = rules.ranges.get((stage, area))
    if found is None:
        raise RulebookGap(f"the rulebook has no expected range for {stage} {area}")
    return found
