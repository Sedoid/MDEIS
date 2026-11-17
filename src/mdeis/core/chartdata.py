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

from fractions import Fraction
from typing import NamedTuple

from .numbers import distance_to_range, two_places
from .records import year_start
from .trend import on_scale, skill_values, when

LINE_CAP = 6  # a drawing limit: more lines than this cannot be told apart. Not a rule.
SHORT = 2  # digits kept of the second year in "2025/26"
SOURCE = "Report-card scores only. Not a prediction."
# What a chart drawn from approved measure results says about itself. Wording, not school values.
MEASURE_SOURCE = "Computed by an approved measure from report-card scores. Not a prediction."
MEASURE_LEGEND = "Value of the measure, computed from the report-card scores"
MEASURE_GROUP = "Computed measure"
MEASURE_LABEL = "Result"
DRAWN_KINDS = ("number", "series")  # the declared kinds this chart can draw; a renderer for each


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


class MixedKinds(ValueError):
    """A number and a series cannot share one chart: one has no terms and the other has them."""


class FieldNeeded(ValueError):
    """A measure result has several numbers and the caller did not name one of them, or named one
    the result does not have. The message lists the names the result has."""


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


# --- a chart of approved measure results ------------------------------------------------------
def _on_axis(exact: str, top: Fraction) -> bool:
    return Fraction(0) <= Fraction(exact) <= top


def _chosen(result: dict, field: str | None) -> str:
    """The number of a `number` result that is drawn: the one named, or the only one it has."""
    names = sorted(result["result"]["fields"])
    if field in names:
        return field
    if field is None and len(names) == 1:
        return names[0]
    raise FieldNeeded(f"{result['measure_id']} has the numbers {', '.join(names)}: name one")


def _undrawn(result: dict, top: Fraction, field: str | None):
    """None when the result can be drawn, else the reason it is left off. Reads, never changes."""
    if result.get("status") != "ok":
        return "no_value"
    kind = result.get("output_kind")
    if kind not in DRAWN_KINDS:
        return "kind_not_drawn"
    try:
        if kind == "number":
            fits = [_on_axis(result["result"]["fields"][_chosen(result, field)]["exact"], top)]
        else:
            points = result["result"]["points"]
            if not points:
                return "no_value"
            fits = [_on_axis(p["exact"], top) for p in points]
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as why:
        if isinstance(why, FieldNeeded):
            raise
        return "no_value"
    return None if all(fits) else "outside_the_score_scale"


def measure_chart_data(results: list, rules, field: str | None = None) -> dict:
    """The dict a chart is drawn from, for approved measure results a tool returned in the run.

    A `number` result is one point, the number named by `field` (or the only one it has); a
    `series` is a line over its own terms. The value on the line is the text the tool wrote, passed
    through unchanged; only its exact form is read, to see whether it fits the rulebook's score
    axis. A value that does not fit is left off and listed, never clipped. The same result twice is
    one line. At most LINE_CAP lines; a number and a series cannot be drawn together."""
    unique = list({(r["measure_id"], r["result_hash"]): r for r in results}.values())
    if not unique:
        raise NothingAsked("name at least one measure result")
    if len(unique) > LINE_CAP:
        raise TooManyLines(f"a chart takes at most {LINE_CAP} lines; {len(unique)} were asked for")
    top = Fraction(rules.scale_max)
    kinds = {r.get("output_kind") for r in unique if r.get("status") == "ok"} & set(DRAWN_KINDS)
    if len(kinds) > 1:
        raise MixedKinds("a number and a series cannot be drawn on one chart")
    drawn, excluded = [], []
    for result in unique:
        reason = _undrawn(result, top, field)
        if reason:
            excluded.append({"skill": result["name"], "measure_id": result["measure_id"],
                             "reason": reason})
        else:
            drawn.append(result)
    terms, series = _measure_lines(drawn, kinds, field)
    clauses = list(dict.fromkeys(c for r in drawn for c in r["clause_ids"]))
    ids = sorted({rid for line in series for found in line["record_ids"] for rid in found})
    return {
        "scale_max": two_places(top),
        "terms": terms,
        "series": series,
        "band_clauses": [],
        "gap": None,
        "excluded": excluded,
        "record_ids": ids,
        "clause_ids": clauses,
        "source": MEASURE_SOURCE,
        "measures": [
            {
                "measure_id": r["measure_id"],
                "version": r["measure_version"],
                "code_sha256": r["code_sha256"],
                "result_hash": r["result_hash"],
            }
            for r in drawn
        ],
    }


def _line(name: str, values: list, ids: list) -> dict:
    return {
        "name": name,
        "kind": "measure",
        "values": values,
        "record_ids": ids,
        "legend": MEASURE_LEGEND,
    }


def _number_line(result: dict, field: str | None) -> dict:
    chosen = _chosen(result, field)
    several = len(result["result"]["fields"]) > 1
    name = f"{result['name']}: {chosen}" if several else result["name"]
    value = result["result"]["fields"][chosen]["value"]
    return _line(name, [value], [list(result["record_ids"])])


def _measure_lines(drawn: list, kinds: set, field: str | None) -> tuple:
    """(terms, series) for the results that can be drawn."""
    if not drawn:
        return [], []
    if kinds == {"number"}:
        terms = [{"label": MEASURE_LABEL, "group": MEASURE_GROUP, "band": None}]
        return terms, [_number_line(r, field) for r in drawn]
    by_line = [{(p["year"], p["term"]): p for p in r["result"]["points"]} for r in drawn]
    keys = sorted(
        {k for line in by_line for k in line}, key=lambda k: (year_start(k[0]), k[1])
    )
    stage_of = {}
    for line in by_line:
        for key, point in line.items():
            stage_of.setdefault(key, point["stage"])
    terms = [
        {"label": f"T{term}", "group": f"{short_year(year)} · {stage_of[(year, term)]}",
         "band": None}
        for year, term in keys
    ]
    series = [
        _line(
            result["name"],
            [line[k]["value"] if k in line else None for k in keys],
            [list(line[k]["record_ids"]) if k in line else [] for k in keys],
        )
        for result, line in zip(drawn, by_line, strict=True)
    ]
    return terms, series
