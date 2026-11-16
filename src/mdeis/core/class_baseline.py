"""The class baseline: where a school's stage cohort sits against the stage standard, by area.

Pure functions, no database and no names. They take one placement per learner (which of five bins
the learner's result for each area falls in) and return counts, with the hiding rules of the
rulebook applied: a whole group that is too small is not shown (GM-1), a small count is hidden
(GM-2), and when exactly one count is hidden its best partner is hidden too (GM-3). Every number,
every minimum and the order of the five counts come from `rules` (mdeis.core.rules). This module
holds no school value. The only numbers in it are 0 and 1 and one named schema detail, the length
of a finding number.

What is hidden is never in the result, and never in a hash: the hash is taken over what is shown,
so a hidden count cannot be found by trying values until a hash matches.
"""

import hashlib
import json

from .rules import RulebookProblem, Rules
from .template import BIN_NAMES, COMPLEMENTARY_METHOD
from .trend import RulebookGap

FINDING_HEX = 8  # the finding number is "CF-" and this many characters of the result's hash
FINDING_PREFIX = "CF-"
NOT_ENOUGH = "not_enough_evidence"
BLOCKED = "blocked"
PLACED = tuple(n for n in BIN_NAMES if n not in (NOT_ENOUGH, BLOCKED))  # below, within, above
INSUFFICIENT = "insufficient_evidence"  # the name of the template's level for too few scores
WITHHELD = "withheld"

REASON_GROUP = "the group is smaller than the minimum that may be shown"
REASON_HIDDEN = (
    "Some counts for this area are hidden. A count that small could point to one learner, and a "
    "count that could be worked out from the shown counts is hidden with it. The number of "
    "learners with enough evidence is not shown for this area. No hidden count is given."
)


class ClassBaselineError(RulebookProblem):
    """The rulebook cannot be used for the class view. The message starts with a typed reason."""


def finding_id(result_hash: str) -> str:
    return FINDING_PREFIX + result_hash[:FINDING_HEX]


def digest(content: dict) -> str:
    """Same recipe as `tools._shared.with_hash`: the same content gives the same hash."""
    text = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sealed(content: dict) -> dict:
    """The content with its `result_hash` and `finding_id` (taken over everything else)."""
    out = dict(content)
    out["result_hash"] = digest(content)
    out["finding_id"] = finding_id(out["result_hash"])
    return out


def check_rules(rules: Rules) -> None:
    """Refuse a rulebook that has no class-view rules, or rules this code does not know."""
    missing = [
        name
        for name, value in (
            ("min_group_size (GM-1)", rules.min_group),
            ("min_cell_size (GM-2)", rules.min_cell),
            ("bin_order (GM-3)", rules.bin_order),
            ("complementary_suppression (GM-3)", rules.complementary),
            ("coverage_when_hidden (GM-3)", rules.coverage_when_hidden),
        )
        if value is None
    ]
    if missing:
        raise ClassBaselineError(
            f"class_rules_missing: the rulebook {rules.version} does not set {', '.join(missing)}, "
            "so no class view is shown. Load a rulebook version that has the class-view rules."
        )
    if rules.complementary != COMPLEMENTARY_METHOD or rules.coverage_when_hidden != WITHHELD:
        raise ClassBaselineError(
            f"class_rules_unknown: the rulebook {rules.version} names a hiding rule this code does "
            "not know, so no class view is shown."
        )
    if sorted(rules.bin_order) != sorted(BIN_NAMES):
        raise ClassBaselineError(
            f"class_rules_unknown: bin_order in {rules.version} is not the five known counts."
        )
    if rules.min_cell > rules.min_group:
        raise ClassBaselineError(
            f"class_rules_unknown: min_cell_size in {rules.version} is larger than min_group_size, "
            "so a hidden count could not be protected."
        )


def require_ranges(rules: Rules, stage: str) -> None:
    """Every area needs an expected range for the stage, or nothing is shown (as compute_trend)."""
    for area in rules.areas:
        if (stage, area) not in rules.ranges:
            raise RulebookGap(f"the rulebook has no expected range for {stage} {area}")


def area_names(rules: Rules) -> list:
    """Foundation areas first, in the template's order, then the other areas in its order."""
    first = [a for a in rules.foundation_areas if a in rules.areas]
    return first + [a for a in rules.areas if a not in first]


def bin_of(result: dict) -> str:
    """The bin a learner's `skill_result` for an area falls in."""
    if result["status"] == "blocked":
        return BLOCKED
    if result["trend"] == "not_enough_evidence":
        return NOT_ENOUGH
    name = result["stage_gap"]["level_name"]
    if name not in PLACED:
        raise ClassBaselineError(f"class_rules_unknown: a result has the level {name!r}")
    return name


def hidden_bins(counts: dict, rules: Rules) -> list:
    """The bins whose count is hidden, in the template's order.

    A count of more than 0 and fewer than `min_cell` is hidden (GM-2). When exactly one is hidden,
    it could be found by taking the shown counts from the group size, so one more is hidden (GM-3):
    the smallest of the other counts that is more than 0, and on a tie the one that comes first in
    `bin_order`. A count of 0 is never chosen. After that pass there are two or none, so the loop
    ends; it is written as a loop with its stop condition.
    """
    order = list(rules.bin_order)
    hidden = {b for b in order if 0 < counts[b] < rules.min_cell}
    while len(hidden) == 1:
        others = [b for b in order if b not in hidden and counts[b] > 0]
        if not others:  # cannot happen when min_cell is not larger than min_group (check_rules)
            raise ClassBaselineError("class_rules_unknown: a hidden count cannot be protected")
        hidden.add(min(others, key=lambda b: (counts[b], order.index(b))))
    return [b for b in order if b in hidden]


def _unique(ids: list) -> list:
    return list(dict.fromkeys(i for i in ids if i))


def area_view(area: str, counts: dict, group: int, stage: str, rules: Rules) -> dict:
    """One area's block: five counts (some possibly hidden), coverage, and its finding number."""
    clause = rules.clause
    base = {"area": area, "foundation": area in rules.foundation_areas}
    placed = sum(counts[n] for n in PLACED)
    if not placed:
        statement = rules.level_statement.get(INSUFFICIENT)
        content = {
            **base,
            "status": "no_usable_evidence",
            "statement": statement,
            "coverage": {"with_enough_evidence": placed, "group_size": group},
            "suppressed": False,
            "suppression": None,
            "clause_ids": _unique(
                [
                    rules.area_clause[area],
                    clause["evidence"],
                    rules.level_clause.get(INSUFFICIENT),
                    clause["group_minimum"],
                ]
            ),
        }
        return _sealed(content)
    hidden = set(hidden_bins(counts, rules))
    bins = [
        {"bin": b, "shown": b not in hidden, "count": None if b in hidden else counts[b]}
        for b in rules.bin_order
    ]
    content = {
        **base,
        "status": "ok",
        "bins": bins,
        "coverage": None if hidden else {"with_enough_evidence": placed, "group_size": group},
        "suppressed": bool(hidden),
        "suppression": (
            {
                "reason": REASON_HIDDEN,
                "clause_ids": [clause["cell_minimum"], clause["complementary"]],
            }
            if hidden
            else None
        ),
        "clause_ids": _unique(
            [
                rules.area_clause[area],
                clause["evidence"],
                clause["missing"],
                rules.range_clause[(stage, area)],
                clause["gap"],
                *(rules.level_clause.get(n) for n in PLACED),
                clause["group_minimum"],
                clause["cell_minimum"],
                clause["complementary"],
            ]
        ),
    }
    return _sealed(content)


def build_result(
    *,
    school: str,
    stage: str,
    term: int,
    year: str | None,
    placements: list,
    rules: Rules,
) -> dict:
    """The class view for one stage and term. `placements` has one dict per learner of the group,
    `{area: bin}`. Nothing about a learner is kept: only counts go in."""
    check_rules(rules)
    group = len(placements)
    head = {
        "school_id": school,
        "stage": stage,
        "term": term,
        "academic_year": year,
        "template_version": rules.version,
        "minimum_group_size": rules.min_group,
    }
    if group < rules.min_group:
        return _sealed(
            {
                **head,
                "status": "suppressed",
                "reason": REASON_GROUP,
                "clause_ids": [rules.clause["group_minimum"]],
            }
        )
    require_ranges(rules, stage)
    areas = []
    for area in area_names(rules):
        counts = dict.fromkeys(rules.bin_order, 0)
        for placed in placements:
            if placed[area] not in counts:
                raise ClassBaselineError(f"class_rules_unknown: unknown bin {placed[area]!r}")
            counts[placed[area]] += 1
        areas.append(area_view(area, counts, group, stage, rules))
    return _sealed(
        {
            **head,
            "minimum_cell_size": rules.min_cell,
            "status": "ok",
            "group_size": group,
            "areas": areas,
            "clause_ids": [
                rules.clause["group_minimum"],
                rules.clause["cell_minimum"],
                rules.clause["complementary"],
            ],
        }
    )


def all_clause_ids(result: dict) -> list:
    """Every clause id the result cites, top level and areas, once each, in order."""
    ids = list(result.get("clause_ids", []))
    for area in result.get("areas", []):
        ids += area["clause_ids"]
        ids += (area.get("suppression") or {}).get("clause_ids", [])
    return _unique(ids)
