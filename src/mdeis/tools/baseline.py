"""class_baseline: where the school's learners at one stage sit against the stage standard.

LOCKED with the rest of the class view: off unless the environment says `CLASS_VIEW_ENABLED=true`
(mdeis.tools.class_view.enabled). When it is on:

* it needs a signed-in approver and a school in the verified session. The school comes from the
  token, never from an argument, so a call cannot ask about another school. Every call, allowed or
  refused, is in the diary;
* the group is the learners with a record at the stage in the most recent school year that has
  records at that stage, in that one school. The rulebook in use is the active one: there is no
  version argument, so the same group cannot be asked about twice under different rules;
* it answers with counts only, for each learning area, foundation areas first: no names, no learner
  numbers, no record numbers, nothing that compares one learner with another. The rulebook decides
  what is hidden (GM-1, GM-2, GM-3) and the answer says that counts were hidden, never what they
  were;
* it reads and writes nothing else: no issue rows are saved for the learners it looks at.

The numbers come from the same functions as every other result (mdeis.core.trend), so a learner is
placed exactly where `compute_trend` would place them. Each area and the whole answer carry a
finding number (`CF-` and 8 characters of the result's hash) for later citation.
"""

from ..core import class_baseline as core
from ..core.records import year_start
from ..core.rules import load_rules
from ..core.trend import skill_result, when
from ._call import logged_tool
from ._shared import rules_text, situation
from .class_view import enabled
from .context import Refusal, ToolContext


def _years_at(records: list, stage: str) -> set:
    return {r["year"] for r in records if r["stage"] == stage}


@logged_tool("class_baseline")
def class_baseline(ctx: ToolContext, stage: str, term: int):
    """The school's learners at one stage, counted below, within and above the expected range.

    Locked unless the class view has been switched on. Needs a signed-in approver and a school in
    the session. The group is the most recent school year with records at that stage. Each area
    (foundation areas first) gets five counts, with coverage; a group below the rulebook's minimum
    is not shown, a small count is hidden, and so is the count that would give it away. No names,
    no learner or record numbers. It saves nothing about the learners it reads.
    """
    if not enabled():
        raise Refusal("class_view_locked: the class view has not been switched on")
    if ctx.approver is None:
        raise Refusal("no approver is signed in, so the class view cannot be shown")
    if not ctx.school:
        raise Refusal(
            "no_school: no school is attached to this session, so the class view cannot be shown"
        )
    rules = load_rules(ctx.conn)
    core.check_rules(rules)
    if stage not in rules.stage_weights:
        raise Refusal(f"{stage!r} is not a stage in {rules.version}")
    core.require_ranges(rules, stage)
    learner_ids = [
        row[0]
        for row in ctx.conn.execute(
            "SELECT learner_id FROM learners WHERE school_code = %s ORDER BY learner_id",
            (ctx.school,),
        ).fetchall()
    ]
    loaded = []  # (records, blocked) of each learner with a record at the stage
    for learner_id in learner_ids:
        found = situation(ctx, learner_id, rules.version, sync=False)
        if _years_at(found.records, stage):
            loaded.append(found)
    all_years = set().union(*(_years_at(f.records, stage) for f in loaded)) if loaded else set()
    year = max(all_years, key=year_start) if all_years else None
    placements = []
    for found in loaded:
        if year not in _years_at(found.records, stage):
            continue  # last at this stage in an earlier year: not in this class
        cutoff = (year_start(year), term)
        upto = [r for r in found.records if when(r) <= cutoff]
        placements.append(
            {
                area: core.bin_of(skill_result(upto, area, rules, found.blocked))
                for area in core.area_names(rules)
            }
        )
    result = core.build_result(
        school=ctx.school, stage=stage, term=term, year=year, placements=placements, rules=rules
    )
    result["rules"] = rules_text(ctx.conn, rules.version, core.all_clause_ids(result))
    return result, "ok"
