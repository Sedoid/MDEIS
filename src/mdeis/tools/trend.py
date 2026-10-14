"""compute_trend: the trend of one subject, or the level and gap of one learning area."""

from ..core.trend import skill_result, subject_result
from ._call import logged_tool
from ._shared import (
    Situation,
    active_methodology,
    rules_text,
    situation,
    sources_of,
    with_hash,
)
from .context import Refusal, ToolContext


def blocking_reasons(found: Situation, subjects: list) -> list:
    """The unanswered blocking problems that stop these subjects from being worked out."""
    reasons = []
    for issue in found.issues:
        if issue["severity"] != "blocking" or issue["issue_id"] in found.answers:
            continue
        if issue["kind"] == "duplicate_card" or issue["subject"] in subjects:
            reasons.append(
                {
                    "issue_id": issue["issue_id"],
                    "kind": issue["kind"],
                    "subject": issue["subject"],
                    "question": issue["question"],
                }
            )
    return reasons


@logged_tool("compute_trend")
def compute_trend(
    ctx: ToolContext, learner_id: str, skill: str, template_version: str | None = None
):
    """Work out one subject's trend, or one area's level and gap to the stage's expected range.

    `skill` is a subject name or a learning area from the rulebook. Every threshold, weight and
    range comes from the rulebook version; the result names the records and the rules it used. A
    subject with an unanswered blocking problem gets no numbers at all, only the reasons.
    """
    found = situation(ctx, learner_id, template_version)
    rules = found.rules
    if skill in rules.subjects:
        result = subject_result(found.records, skill, rules, found.blocked)
        subjects = [skill]
    elif skill in rules.areas:
        result = skill_result(found.records, skill, rules, found.blocked)
        subjects = rules.areas[skill]
    else:
        known = ", ".join(sorted(rules.subjects) + sorted(rules.areas))
        raise Refusal(f"{skill!r} is not a subject or area in {rules.version}. Known: {known}")
    result = {"learner_id": learner_id, "template_version": rules.version, **result}
    with_hash(result)  # the hash covers the inputs and the rulebook version, not the methodology
    result["methodology_document_id"] = active_methodology(ctx.conn)
    result["sources"] = sources_of(ctx.conn, found, result.get("record_ids", []))
    result["rules"] = rules_text(ctx.conn, rules.version, result.get("clause_ids", []))
    if result["status"] == "blocked":
        result["reasons"] = blocking_reasons(found, subjects)
        return result, "blocked"
    return result, "ok"
