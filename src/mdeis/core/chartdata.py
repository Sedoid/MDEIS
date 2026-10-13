"""What a progression chart shows, worked out from one learner's records and the rulebook.

Pure functions, no database and no model. The numbers are the stored scores on the rulebook's scale,
exactly as `compute_trend` reads them; nothing is estimated, smoothed or filled in. A term with no
score is a gap, never a zero. The expected range and the clause that sets it come from the rulebook.
The picture is drawn from this dict by `charts.trend_svg`.

A line is a subject or a learning area. A subject with an unanswered blocking problem, or with fewer
scores than the rulebook asks for, is left off and listed in `excluded` with the reason. The shaded
range is only drawn for one learning area, because the rulebook sets ranges per area and stage and a
subject has none.
"""

from __future__ import annotations

from typing import NamedTuple

from .numbers import distance_to_range, two_places
from .records import year_start
from .trend import on_scale, skill_values, when

LINE_CAP = 6  # a drawing limit: more lines than this cannot be told apart. Not a rule.
SHORT = 2  # digits kept of the second year in "2025/26"
SOURCE = "Report-card scores only. Not a prediction."


class Point(NamedTuple):
    """One term of one line: the score on the rulebook's scale, its stage and its records."""

    value: object  # a Fraction
    stage: str
    ids: list


class Line(NamedTuple):
    name: str
    is_area: bool
    points: dict  # (year, term) -> Point


class UnknownSkill(ValueError):
    """The name is neither a subject nor a learning area of the rulebook."""


class TooManyLines(ValueError):
    pass


class NothingAsked(ValueError):
    pass


def short_year(year: str) -> str:
    """'2025/2026' as '2025/26'."""
    start, _, end = year.partition("/")
    return f"{start}/{end[-SHORT:]}" if end else year


def _subject_points(records, subject, rules):
    """{(year, term): (value, stage, [record ids])} for the scored terms of one subject."""
    points = {}
    for r in sorted((r for r in records if r["subject"] == subject), key=when):
        if r["score"] is not None:
            points[(r["year"], r["term"])] = Point(on_scale(r, rules), r["stage"], [r["record_id"]])
    return points


def _area_points(records, area, rules):
    return {
        (v.year, v.term): Point(v.value, v.stage, list(v.record_ids))
        for v in skill_values(records, area, rules)
        if v.value is not None
    }


def chart_data(records: list, rules, skills: list, blocked: set) -> dict:
    """The dict a chart is drawn from, for the named subjects or learning areas."""
    names = list(dict.fromkeys(skills))
    if not names:
        raise NothingAsked("name at least one subject or learning area")
    if len(names) > LINE_CAP:
        raise TooManyLines(f"a chart takes at most {LINE_CAP} lines; {len(names)} were asked for")
    known = sorted(rules.subjects) + sorted(rules.areas)
    lines, excluded = [], []
    for name in names:
        if name in rules.subjects:
            subjects, points = [name], _subject_points(records, name, rules)
        elif name in rules.areas:
            subjects, points = rules.areas[name], _area_points(records, name, rules)
        else:
            raise UnknownSkill(f"{name!r} is not a subject or area in {rules.version}. "
                               f"Known: {', '.join(known)}")
        if any(s in blocked for s in subjects):
            excluded.append({"skill": name, "reason": "blocked"})
        elif len(points) < rules.min_scores:
            excluded.append({"skill": name, "reason": "not_enough_evidence",
                             "scores_available": len(points), "scores_needed": rules.min_scores})
        else:
            lines.append(Line(name, name in rules.areas, points))

    keys = sorted(
        {k for line in lines for k in line.points}, key=lambda k: (year_start(k[0]), k[1])
    )
    stage_of = {k: line.points[k].stage for line in lines for k in line.points}
    only_area = lines[0].name if len(lines) == 1 and lines[0].is_area else None
    terms = []
    for key in keys:
        year, term = key
        band = None
        if only_area and (stage_of[key], only_area) in rules.ranges:
            low, high = rules.ranges[(stage_of[key], only_area)]
            band = [two_places(low), two_places(high)]
        terms.append({"label": f"T{term}", "group": f"{short_year(year)} · {stage_of[key]}",
                      "band": band})
    series = [
        {
            "name": line.name,
            "kind": "area" if line.is_area else "subject",
            "values": [
                two_places(line.points[k].value) if k in line.points else None for k in keys
            ],
            "record_ids": [line.points[k].ids if k in line.points else [] for k in keys],
        }
        for line in lines
    ]
    clauses = [rules.clause["evidence"]] if lines else []
    for line in lines:
        if line.is_area:
            clauses.append(rules.area_clause[line.name])
    band_clauses = []
    if only_area:
        band_clauses = list(dict.fromkeys(
            rules.range_clause[(stage_of[k], only_area)] for k in keys
            if (stage_of[k], only_area) in rules.ranges
        ))
        clauses += band_clauses
    return {
        "scale_max": two_places(rules.scale_max),
        "terms": terms,
        "series": series,
        "band_clauses": band_clauses,
        "gap": _last_gap(lines, keys, stage_of, only_area, rules),
        "excluded": excluded,
        "record_ids": sorted({rid for s in series for ids in s["record_ids"] for rid in ids}),
        "clause_ids": list(dict.fromkeys(clauses)),
        "source": SOURCE,
    }


def _last_gap(lines, keys, stage_of, only_area, rules):
    """For one learning area: the last score's distance to the nearest edge of its range."""
    if not only_area:
        return None
    last = keys[-1]
    if (stage_of[last], only_area) not in rules.ranges:
        return None
    value = lines[0].points[last].value
    low, high = rules.ranges[(stage_of[last], only_area)]
    gap = distance_to_range(value, low, high)
    if gap == 0:
        return None
    side, edge = ("below", low) if value < low else ("above", high)
    return {"index": len(keys) - 1, "from_value": two_places(value), "to_value": two_places(edge),
            "text": f"{two_places(gap)} {side}"}
