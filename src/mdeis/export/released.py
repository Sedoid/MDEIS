"""Which stored report may be exported: one check, shared by the model's tool and the download.

`find_released` reads one stored report (read-only) and says either "refused, and why" or hands back
the row. `check_released` turns that into the small answer the model's tool `export_report` gives
(`ok` with the report number and the file name, or a typed refusal). The download route
(`report_export.export_released_report`) uses the same `find_released`, so the tool says `ok`
exactly when the download would give a file.

Only a report with status `released`, whose learner belongs to the caller's school, passes. A report
of another school is answered "no such report", so its existence is not revealed. The person and the
school come from the context (made from the verified token), never from an argument.

It imports nothing from `mdeis.tools` (the tool imports this module), so no import loop can form.
"""

from __future__ import annotations

import re

REPORT_ID = re.compile(r"^R-[0-9]{6,}$")  # R-000001: the form the database gives a report number

PLAIN = {
    "not_signed_in": "No one is signed in, so no report can be exported.",
    "no_school": "This sign-in carries no school.",
    "report_not_found": "There is no such report.",
    "report_not_released": "Only a released report can be exported.",
}


def refusal(reason: str) -> dict:
    return {"status": "refused", "reason": reason, "detail": PLAIN[reason]}


def file_name(report_id: str) -> str:
    """The name the download gives the file: the report number and `.pdf`."""
    return f"{report_id}.pdf"


def find_released(ctx, report_id) -> tuple:
    """(refusal, None) when this report may not be exported, or (None, row) when it may.

    The row is (report_id, learner_id, status, body, findings, template_version,
    methodology_document_id, released_by_id, released_by_name, released_at, school_code). A number
    that is not a well-formed report number is "not found" without a database read.
    """
    if ctx.approver is None:
        return refusal("not_signed_in"), None
    if not ctx.school:
        return refusal("no_school"), None
    if not isinstance(report_id, str) or not REPORT_ID.fullmatch(report_id):
        return refusal("report_not_found"), None
    row = ctx.conn.execute(
        "SELECT r.report_id, r.learner_id, r.status, r.body, r.findings, r.template_version, "
        "r.methodology_document_id, r.released_by_id, r.released_by_name, r.released_at, "
        "l.school_code FROM reports r JOIN learners l ON l.learner_id = r.learner_id "
        "WHERE r.report_id = %s",
        (report_id,),
    ).fetchone()
    if row is None or row[10] != ctx.school:
        return refusal("report_not_found"), None
    if row[2] != "released":
        return refusal("report_not_released"), None
    return None, row


def check_released(ctx, report_id) -> dict:
    """The answer of the model's tool: `ok` with the number and file name, or a typed refusal.
    It holds no file. The PDF is made later, by the download route, from the stored report."""
    why, row = find_released(ctx, report_id)
    if why is not None:
        return why
    return {"status": "ok", "report_id": row[0], "file_name": file_name(row[0])}
