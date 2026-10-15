"""ingest_assessments: read one learner's stored records, check them, and name every problem."""

from ._call import logged_tool
from ._shared import public_issue, public_records, situation, sources_of, with_hash
from .context import ToolContext


@logged_tool("ingest_assessments")
def ingest_assessments(ctx: ToolContext, learner_id: str, template_version: str | None = None):
    """Read the learner's records under a rulebook version and list the problems found.

    Nothing is changed or fixed. A problem that blocks a subject (a missing term, a score outside
    the scale, a duplicated year) is listed with a plain question for the approver; until it is
    answered, nothing is worked out for that subject.
    """
    found = situation(ctx, learner_id, template_version)
    result = {
        "learner_id": learner_id,
        "template_version": found.rules.version,
        "records": public_records(found),
        "issues": [public_issue(i, found.answers) for i in found.issues],
        "blocked_subjects": sorted(found.blocked),
    }
    with_hash(result)
    result["sources"] = sources_of(ctx.conn, found)
    return result, "ok"
