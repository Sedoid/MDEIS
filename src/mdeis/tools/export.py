"""export_report: whether a report can be exported as a PDF. It makes no file and returns none."""

from ..export.released import check_released
from ._call import logged_tool
from .context import ToolContext


@logged_tool("export_report")
def export_report(ctx: ToolContext, report_id: str):
    """Check that a report can be exported as a PDF, so the person can download it.

    Only a released report can be exported. A draft, a report waiting for review, a report that does
    not exist and a report of another school are refused with a typed reason (`report_not_released`,
    `report_not_found`). This returns no file: the answer is `ok` with the report number and the
    file name, or the refusal. You cannot make, attach or send a PDF yourself. When the answer is
    `ok`, the system adds the download link under your answer. Never write a link, a path or a file
    name. If it is refused as not released, say plainly that the report is not released yet and that
    the approver releases it. Do not promise a file for a draft. Give only a report number: one the
    person gave you, or one `draft_summary` or `release_report` returned in this conversation, and
    never one you made up. Who is asking comes from the signed-in session, never from an argument.
    """
    result = check_released(ctx, report_id)
    result["logged_event_id"] = None  # the wrapper fills it in, as for every tool
    return result, "ok" if result["status"] == "ok" else "refused"
