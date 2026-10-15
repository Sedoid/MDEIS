"""release_report: the one gate. Only the signed-in approver can let a checked draft out."""

from ._call import logged_tool
from .context import Refusal, ToolContext


@logged_tool("release_report")
def release_report(ctx: ToolContext, report_id: str):
    """Release a draft that has passed the citation check, in the name of the signed-in approver.

    Refused, and logged, when nobody is signed in, the draft is not pending review, the check has
    not passed, or the rulebook or methodology has changed since the draft was written
    (`stale_pin`). The approver comes from the session. There is no argument that can name one.
    """
    if ctx.approver is None:
        raise Refusal("no approver is signed in, so nothing can be released")
    row = ctx.conn.execute(
        "SELECT status, citations_ok FROM reports WHERE report_id = %s FOR UPDATE", (report_id,)
    ).fetchone()
    if row is None:
        raise Refusal(f"there is no report {report_id}")
    status, citations_ok = row
    if status != "pending_review":
        raise Refusal(f"{report_id} is already {status}")
    if not citations_ok:
        raise Refusal("the citation check has not passed for this draft")
    pin = ctx.conn.execute(
        "SELECT template_stale, methodology_stale FROM report_pin_status WHERE report_id = %s",
        (report_id,),
    ).fetchone()
    if pin[0] or pin[1]:
        raise Refusal("stale_pin")
    released = ctx.conn.execute(
        "UPDATE reports SET status = 'released', released_by_id = %s, released_by_name = %s, "
        "released_at = now() WHERE report_id = %s RETURNING released_at, template_version",
        (ctx.approver.id, ctx.approver.name, report_id),
    ).fetchone()
    result = {
        "status": "released",
        "report_id": report_id,
        "approver": ctx.approver.name,
        "at": released[0].isoformat(),
        "template_version": released[1],
        "logged_event_id": None,
    }
    return result, "ok"
