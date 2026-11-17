"""export_released_report: read one stored, released report, render it, and log the attempt.

This is NOT a tool. No model can call it: it is reached only through the human route on the tool
server (`human_routes.install_reports`), which needs the gateway's key and a signed-in person's
token. The person comes from the context (made from that token), never from an argument.

It reads the stored report and the stored findings and writes nothing but one diary line. It never
recomputes anything. Only a report with status `released` is exported, and only for a person of the
school the report's learner belongs to. A report of another school is answered "no such report",
so its existence is not revealed.

Every attempt, allowed or refused, writes one `action_log` row (tool `export_report_pdf`) with the
signed-in person. If that row cannot be written, no file is returned.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime

from mdeis.db import diary

from ..tools.context import ToolContext
from .report_pdf import CLAUSE_ID, RECORD_ID, SECTION_ID, ids_in, render_report_pdf

TOOL = "export_report_pdf"
REPORT_ID = re.compile(r"^R-[0-9]{6,}$")  # the form the database gives a report number
MARKER = re.compile(r"\[([A-Z]{1,2}-[0-9][0-9A-Za-z.]*)\]")  # a citation marker in a body

PLAIN = {
    "not_signed_in": "No one is signed in, so no report can be exported.",
    "no_school": "This sign-in carries no school.",
    "report_not_found": "There is no such report.",
    "report_not_released": "Only a released report can be exported.",
}


def refusal(reason: str) -> dict:
    return {"status": "refused", "reason": reason, "detail": PLAIN[reason]}


def resolve_sources(conn, ids: set, template_version: str, document_id: str | None) -> list:
    """Each ID, resolved the way the citation check resolves it: a record to its document, page and
    row; a clause to its rulebook version and wording; a methodology section to its document, title
    and page. An ID the database does not know is left out."""
    out = []
    for marker in sorted(ids):
        if RECORD_ID.fullmatch(marker):
            row = conn.execute(
                "SELECT subject, academic_year, term, source_document_id, source_page, source_row "
                "FROM assessments WHERE assessment_id = %s",
                (marker,),
            ).fetchone()
            if row:
                subject, year, term, document, page, source_row = row
                out.append({
                    "id": marker, "kind": "record", "subject": subject, "year": year, "term": term,
                    "document_id": document, "page": page, "row": source_row,
                })
        elif SECTION_ID.fullmatch(marker):
            row = conn.execute(
                "SELECT title, page FROM method_chunks WHERE document_id = %s AND section_id = %s",
                (document_id, marker[len("M-"):]),
            ).fetchone()
            if row:
                out.append({
                    "id": marker, "kind": "section", "document_id": document_id,
                    "title": row[0], "page": row[1],
                })
        elif CLAUSE_ID.fullmatch(marker):
            row = conn.execute(
                "SELECT text FROM clauses WHERE version_name = %s AND clause_id = %s",
                (template_version, marker),
            ).fetchone()
            if row:
                out.append({
                    "id": marker, "kind": "clause", "template_version": template_version,
                    "text": row[0],
                })
    return out


def _decide(ctx: ToolContext, report_id, generated_at: datetime, practice_data: bool) -> dict:
    if ctx.approver is None:
        return refusal("not_signed_in")
    if not ctx.school:
        return refusal("no_school")
    if not isinstance(report_id, str) or not REPORT_ID.match(report_id):
        return refusal("report_not_found")
    row = ctx.conn.execute(
        "SELECT r.report_id, r.learner_id, r.status, r.body, r.findings, r.template_version, "
        "r.methodology_document_id, r.released_by_id, r.released_by_name, r.released_at, "
        "l.school_code FROM reports r JOIN learners l ON l.learner_id = r.learner_id "
        "WHERE r.report_id = %s",
        (report_id,),
    ).fetchone()
    if row is None or row[10] != ctx.school:
        return refusal("report_not_found")
    (_, learner_id, status, body, findings, version, document_id, released_by_id,
     released_by_name, released_at, _school) = row
    if status != "released":
        return refusal("report_not_released")

    wanted = set(MARKER.findall(body))
    for finding in findings if isinstance(findings, list) else [findings]:
        for group in ids_in(finding).values():
            wanted.update(group)
    sources = resolve_sources(ctx.conn, wanted, version, document_id)
    pdf = render_report_pdf(
        {"report_id": report_id, "learner_id": learner_id, "body": body, "findings": findings},
        sources,
        {"template_version": version, "methodology_document_id": document_id},
        {"name": released_by_name or released_by_id, "released_at": released_at},
        generated_at,
        practice_data=practice_data,
    )
    return {
        "status": "ok", "report_id": report_id, "file_name": f"{report_id}.pdf", "pdf": pdf,
        "sha256": hashlib.sha256(pdf).hexdigest(), "size_bytes": len(pdf),
    }


def export_released_report(
    ctx: ToolContext, report_id: str, generated_at: datetime, practice_data: bool = False
) -> dict:
    """The PDF of a released report, or a typed refusal. Either way one diary line is written.

    Returns {"status": "ok", "report_id", "file_name", "pdf", "sha256", "size_bytes",
    "logged_event_id"} or {"status": "refused", "reason", "detail", "logged_event_id"}. Raises
    `diary.DiaryUnavailable` when the line cannot be written: then nothing is returned.
    """
    result = _decide(ctx, report_id, generated_at, practice_data)
    ok = result["status"] == "ok"
    shown = report_id if isinstance(report_id, str) and REPORT_ID.match(report_id) else None
    result["logged_event_id"] = diary.write(
        TOOL,
        run_id=ctx.run_id,
        inputs={"report_id": shown},
        outputs={k: v for k, v in result.items() if k != "pdf"},
        outcome="ok" if ok else "refused",
        approver_id=ctx.approver.id if ctx.approver else None,
        approver_name=ctx.approver.name if ctx.approver else None,
        url=ctx.database_url,
    )
    return result
