"""render_report_pdf: a released report as a PDF.

A pure function: no database, no network, no model.

It is given the stored report, the sources already resolved, the pin, and the person who released
it. It writes down what it is given and adds no words of its own beyond the headings and the fixed
lines below. Nothing is worked out here: no number, no label, no ranking, no advice. The same input
and the same `generated_at` give the same bytes.

Order of the page (T-056): the learner number; the report body exactly as stored; the findings with
their record and clause IDs; the sources resolved to document, page and row; the pin; the methods
named; the limitations the stored findings carry; who released it and when; and a footer line when
the deployment says the data is practice data.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from .pdf_writer import write_pdf

# Every word this module writes itself is in this block. A test reads the page back and holds it to
# these and to the stored values.
FOOTER = "Practice data only"
H_REPORT = "Report"
H_FINDINGS = "Findings"
H_SOURCES = "Sources"
H_PIN = "Pin"
H_METHODS = "Methods"
H_LIMITS = "Limitations"
H_RELEASE = "Release"
TITLE = "Report {report_id}"
LEARNER = "Learner number: {learner_id}"
FINDING = "Finding {n}"
FINDING_SKILL = "Subject or area: {skill}"
FINDING_HASH = "Result hash: {result_hash}"
FINDING_RECORDS = "Records: {ids}"
FINDING_CLAUSES = "Clauses: {ids}"
FINDING_SECTIONS = "Sections: {ids}"
NO_FINDINGS = "No findings are stored with this report."
NO_SOURCES = "No sources are stored with this report."
SOURCE_RECORD = "{id} | {subject} | {year} term {term} | {document_id} | page {page} | row {row}"
SOURCE_CLAUSE = "{id} | template {template_version} | {text}"
SOURCE_SECTION = "{id} | {document_id} | page {page}"
SOURCE_OTHER = "{id}"
NOT_RECORDED = "not recorded"
PIN_TEMPLATE = "Template version: {template_version}"
PIN_METHODOLOGY = "Methodology document: {document_id}"
PIN_NO_METHODOLOGY = "none"
PIN_MEASURES = "Measure versions: {versions}"
METHOD = "{id}: {title}"
NO_METHODS = "No methodology section is cited."
LIMIT_NOT_ENOUGH = "Finding {n}: not enough evidence ({scores_available} scores available)"
LIMIT_NOT_ENOUGH_BARE = "Finding {n}: not enough evidence"
LIMIT_BLOCKED = "Finding {n}: blocked, no numbers were worked out"
LIMIT_ASSUMED = "Finding {n}: assumed {what} for {stage}, used {used}"
NO_LIMITS = "None recorded in the stored findings."
RELEASED_BY = "Released by {name} on {time}"

# The shape of the IDs in docs/SPEC.md (records, rule clauses, methodology sections). Not rules.
RECORD_ID = re.compile(r"A-[0-9]{6,}")
CLAUSE_ID = re.compile(r"[A-Z]{2}-[0-9]+")
SECTION_ID = re.compile(r"M-[0-9][0-9A-Za-z.]*")
KIND_ORDER = {"record": 0, "clause": 1, "section": 2}


def _natural(text: str) -> list:
    """A sort key that puts TR-2 before TR-10. Pieces alternate text and number, so they compare."""
    return [int(p) if p.isdigit() else p for p in re.split(r"([0-9]+)", str(text))]


def _walk(value):
    """Every string inside a stored finding."""
    if isinstance(value, dict):
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)
    elif isinstance(value, str):
        yield value


def ids_in(finding) -> dict:
    """The record, clause and section IDs a stored finding carries, each sorted and listed once."""
    found = {"records": set(), "clauses": set(), "sections": set()}
    for leaf in _walk(finding):
        if RECORD_ID.fullmatch(leaf):
            found["records"].add(leaf)
        elif SECTION_ID.fullmatch(leaf):
            found["sections"].add(leaf)
        elif CLAUSE_ID.fullmatch(leaf):
            found["clauses"].add(leaf)
    return {k: sorted(v, key=_natural) for k, v in found.items()}


def when(moment) -> str:
    """A time as the page shows it: UTC, to the minute. A text is shown as it is."""
    if isinstance(moment, datetime):
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return str(moment)


def _shown(value) -> str:
    return NOT_RECORDED if value is None else str(value)


def _source_line(source: dict) -> str:
    kind = source.get("kind")
    if kind == "record":
        return SOURCE_RECORD.format(
            id=source["id"], subject=_shown(source.get("subject")),
            year=_shown(source.get("year")), term=_shown(source.get("term")),
            document_id=_shown(source.get("document_id")), page=_shown(source.get("page")),
            row=_shown(source.get("row")),
        )
    if kind == "clause":
        return SOURCE_CLAUSE.format(
            id=source["id"], template_version=_shown(source.get("template_version")),
            text=_shown(source.get("text")),
        )
    if kind == "section":
        return SOURCE_SECTION.format(
            id=source["id"], document_id=_shown(source.get("document_id")),
            page=_shown(source.get("page")),
        )
    return SOURCE_OTHER.format(id=source["id"])


def _limits(findings: list) -> list:
    """What the stored findings say about their own limits, one line each, in their order."""
    lines = []
    for n, finding in enumerate(findings, start=1):
        if not isinstance(finding, dict):
            continue
        if finding.get("trend") == "not_enough_evidence":
            available = finding.get("scores_available")
            if available is None:
                lines.append(LIMIT_NOT_ENOUGH_BARE.format(n=n))
            else:
                lines.append(LIMIT_NOT_ENOUGH.format(n=n, scores_available=available))
        if finding.get("status") == "blocked":
            lines.append(LIMIT_BLOCKED.format(n=n))
        for item in finding.get("assumed") or []:
            if isinstance(item, dict):
                lines.append(LIMIT_ASSUMED.format(
                    n=n, what=_shown(item.get("what")), stage=_shown(item.get("stage")),
                    used=_shown(item.get("used")),
                ))
    return lines


def as_list(findings) -> list:
    """The stored findings as a list. An empty object (the column's default) is no findings."""
    if isinstance(findings, list):
        return findings
    if isinstance(findings, dict) and findings:
        return [findings]
    return []


def build_blocks(report: dict, sources: list, pin: dict, approver: dict) -> list:
    """The page as (style, text) blocks, in the order of the brief."""
    findings = as_list(report.get("findings"))
    blocks = [
        ("title", TITLE.format(report_id=report["report_id"])),
        ("text", LEARNER.format(learner_id=report["learner_id"])),
        ("heading", H_REPORT),
        ("text", report["body"]),
        ("heading", H_FINDINGS),
    ]
    for n, finding in enumerate(findings, start=1):
        blocks.append(("text", FINDING.format(n=n)))
        if isinstance(finding, dict):
            if finding.get("skill") is not None:
                blocks.append(("text", FINDING_SKILL.format(skill=finding["skill"])))
            if finding.get("result_hash") is not None:
                blocks.append(("text", FINDING_HASH.format(result_hash=finding["result_hash"])))
        found = ids_in(finding)
        for key, line in (
            ("records", FINDING_RECORDS), ("clauses", FINDING_CLAUSES),
            ("sections", FINDING_SECTIONS),
        ):
            if found[key]:
                blocks.append(("text", line.format(ids=", ".join(found[key]))))
    if not findings:
        blocks.append(("text", NO_FINDINGS))

    ordered = sorted(
        sources, key=lambda s: (KIND_ORDER.get(s.get("kind"), 9), _natural(s["id"]))
    )
    blocks.append(("heading", H_SOURCES))
    blocks += [("text", _source_line(s)) for s in ordered] or [("text", NO_SOURCES)]

    document = pin.get("methodology_document_id")
    blocks.append(("heading", H_PIN))
    version = _shown(pin.get("template_version"))
    blocks.append(("text", PIN_TEMPLATE.format(template_version=version)))
    blocks.append(("text", PIN_METHODOLOGY.format(
        document_id=PIN_NO_METHODOLOGY if document is None else document)))
    measures = pin.get("measure_versions")
    if measures:
        versions = ", ".join(f"{name} {measures[name]}" for name in sorted(measures))
        blocks.append(("text", PIN_MEASURES.format(versions=versions)))

    sections = [s for s in ordered if s.get("kind") == "section"]
    blocks.append(("heading", H_METHODS))
    blocks += [
        ("text", METHOD.format(id=s["id"], title=_shown(s.get("title")))) for s in sections
    ] or [("text", NO_METHODS)]

    blocks.append(("heading", H_LIMITS))
    blocks += [("text", line) for line in _limits(findings)] or [("text", NO_LIMITS)]

    blocks.append(("heading", H_RELEASE))
    blocks.append(("text", RELEASED_BY.format(
        name=approver["name"], time=when(approver["released_at"]))))
    return blocks


def render_report_pdf(
    report: dict,
    sources: list,
    pin: dict,
    approver: dict,
    generated_at: datetime,
    practice_data: bool = False,
) -> bytes:
    """The PDF bytes of a released report.

    report    {"report_id", "learner_id", "body", "findings"}, exactly as stored
    sources   [{"id", "kind": "record" | "clause" | "section", ...}], as the citation check
              resolves them (document, page, row)
    pin       {"template_version", "methodology_document_id", "measure_versions" (optional)}
    approver  {"name", "released_at"}: the person who released the report, from the stored row
    generated_at   the time written into the file's metadata; a test pins it
    practice_data  true when the deployment says the data is synthetic: adds the footer line
    """
    blocks = build_blocks(report, sources, pin, approver)
    return write_pdf(
        blocks,
        title=TITLE.format(report_id=report["report_id"]),
        created=generated_at,
        footer=FOOTER if practice_data else None,
    )
