"""draft_summary: save the writer's text as a draft that waits for a named person to release it."""

from mdeis.db import diary

from ..core import store
from ._call import logged_tool
from .context import Refusal, ToolContext

FINDING_TOOLS = ("compute_trend", "ingest_assessments")


def findings_for(ctx: ToolContext, hashes: list) -> list:
    """The tool results of this run with these hashes, exactly as logged. Refuses unknown hashes."""
    found = {}
    rows = ctx.conn.execute(
        "SELECT outputs FROM action_log WHERE run_id = %s AND outcome = 'ok' AND tool = ANY(%s) "
        "ORDER BY event_id",
        (ctx.run_id, list(FINDING_TOOLS)),
    ).fetchall()
    for (outputs,) in rows:
        if isinstance(outputs, dict) and outputs.get("result_hash") in hashes:
            found[outputs["result_hash"]] = outputs
    missing = [h for h in hashes if h not in found]
    if missing:
        raise Refusal("not returned by a tool in this run: " + ", ".join(missing))
    return [found[h] for h in hashes]


@logged_tool("draft_summary")
def draft_summary(ctx: ToolContext, learner_id: str, text: str, finding_hashes: list[str]):
    """Save a draft report. It is not released: a signed-in person must release it, after the check.

    `finding_hashes` are the `result_hash` values of the tool results the text is built from. The
    draft is pinned to the rulebook version those results used.
    """
    if not store.learner_exists(ctx.conn, learner_id):
        raise Refusal(f"there is no learner {learner_id}")
    if not text or not text.strip():
        raise Refusal("the draft is empty")
    if not finding_hashes:
        raise Refusal("a draft must name the findings it is built from")
    findings = findings_for(ctx, list(finding_hashes))
    versions = {f["template_version"] for f in findings if "template_version" in f}
    if len(versions) != 1:
        raise Refusal("the findings must all come from one rulebook version")
    if {f.get("learner_id") for f in findings} != {learner_id}:
        raise Refusal("every finding must be about this learner")
    version = versions.pop()
    methodology = ctx.conn.execute(
        "SELECT document_id FROM documents WHERE kind = 'methodology' AND active"
    ).fetchone()
    row = ctx.conn.execute(
        "INSERT INTO reports (learner_id, run_id, body, findings, template_version, "
        "methodology_document_id) VALUES (%s, %s, %s, %s, %s, %s) RETURNING report_id",
        (
            learner_id,
            ctx.run_id,
            text,
            diary.jsonb(findings),
            version,
            methodology[0] if methodology else None,
        ),
    ).fetchone()
    result = {
        "status": "pending_review",
        "report_id": row[0],
        "template_version": version,
        "methodology_document_id": methodology[0] if methodology else None,
        "logged_event_id": None,
    }
    return result, "ok"
