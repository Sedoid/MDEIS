"""stage_gap_counts: how many learners of one stage sit below, within and above the expected range.

LOCKED. This tool is off unless the environment says `CLASS_VIEW_ENABLED=true`, and the server does
not even list it then. Switching it on is a decision for a named person after the safety tests pass
(docs/SPEC.md section 5.8). When it is on:

* it needs a signed-in approver, and every call, allowed or refused, is in the diary;
* it covers one school only, and answers with counts only: no names, no learner numbers, no record
  numbers, nothing that compares one learner with another;
* a group smaller than the rulebook's group minimum (GM-1) is not shown at all: the answer says the
  group is too small and gives neither the counts nor the size.

The numbers come from the same functions as every other result (mdeis.core.trend), so a learner is
counted exactly where `compute_trend` would place them.
"""

import os

from ..core.rules import load_rules
from ..core.trend import skill_result, when
from ._call import logged_tool
from ._shared import rules_text, situation, with_hash
from .context import Refusal, ToolContext

FLAG = "CLASS_VIEW_ENABLED"
ON = ("1", "true", "yes", "on")
CATEGORIES = (
    "below_expected_range",
    "within_expected_range",
    "above_expected_range",
    "not_enough_evidence",
    "blocked",
)


def enabled() -> bool:
    """True only when the environment switches the class view on. Read each time it is asked."""
    return os.environ.get(FLAG, "false").strip().lower() in ON


def category(result: dict) -> str:
    if result["status"] == "blocked":
        return "blocked"
    if result["trend"] == "not_enough_evidence":
        return "not_enough_evidence"
    return result["stage_gap"]["level_name"]


def records_to(records: list, stage: str, term: int) -> list | None:
    """The records up to the end of `term` in the latest school year the learner was at `stage`.
    None when the learner has no record at that stage."""
    years = [when(r)[0] for r in records if r["stage"] == stage]
    if not years:
        return None
    cutoff = (max(years), term)
    return [r for r in records if when(r) <= cutoff]


@logged_tool("stage_gap_counts")
def stage_gap_counts(
    ctx: ToolContext,
    school_id: str,
    stage: str,
    skill: str,
    term: int,
    template_version: str | None = None,
):
    """Counts of the school's learners below, within and above the stage's expected range.

    Locked unless the class view has been switched on. Needs a signed-in approver. A group smaller
    than the rulebook's group minimum is suppressed. No names and no learner or record numbers.
    """
    if not enabled():
        raise Refusal("class_view_locked: the class view has not been switched on")
    if ctx.approver is None:
        raise Refusal("no approver is signed in, so the class view cannot be shown")
    rules = load_rules(ctx.conn, template_version)
    if rules.min_group is None:
        raise Refusal(f"the rulebook {rules.version} sets no group minimum, so no group is shown")
    if skill not in rules.areas:
        raise Refusal(f"{skill!r} is not a learning area in {rules.version}")
    if stage not in rules.stage_weights:
        raise Refusal(f"{stage!r} is not a stage in {rules.version}")
    learners = [
        row[0]
        for row in ctx.conn.execute(
            "SELECT learner_id FROM learners WHERE school_code = %s ORDER BY learner_id",
            (school_id,),
        ).fetchall()
    ]
    counts = dict.fromkeys(CATEGORIES, 0)
    group = 0
    for learner_id in learners:
        found = situation(ctx, learner_id, rules.version)
        upto = records_to(found.records, stage, term)
        if upto is None:
            continue
        group += 1
        counts[category(skill_result(upto, skill, rules, found.blocked))] += 1
    base = {
        "school_id": school_id,
        "stage": stage,
        "skill": skill,
        "term": term,
        "template_version": rules.version,
        "minimum_group_size": rules.min_group,
    }
    clauses = [rules.clause["group_minimum"]]
    if group < rules.min_group:
        result = {
            **base,
            "status": "suppressed",
            "reason": "the group is smaller than the minimum that may be shown",
            "clause_ids": clauses,
        }
        with_hash(result)
        result["rules"] = rules_text(ctx.conn, rules.version, clauses)
        return result, "ok"
    clauses += [rules.range_clause[(stage, skill)], rules.clause["gap"]]
    result = {**base, "status": "ok", "group_size": group, "counts": counts, "clause_ids": clauses}
    with_hash(result)
    result["rules"] = rules_text(ctx.conn, rules.version, clauses)
    return result, "ok"
