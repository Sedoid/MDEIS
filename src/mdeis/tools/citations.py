"""check_citations: every sentence of a draft must be backed by what the tools returned in the run.

The rules, in order of what they catch:
  uncited_sentence  a sentence with no [source] marker
  unknown_record    a [A-000123] marker no tool returned in this run
  unknown_clause    a [TR-2] marker no tool returned in this run, or not in the pinned rulebook
  unknown_section   a [M-6.4] marker no tool returned, or not in the pinned methodology
  unknown_number    a number in the text that is not in the findings the draft was built from
  banned_word       a word the pinned rulebook bans
The check is plain code. The writer cannot argue with it, and nothing but a clean pass sets
`citations_ok`, the thing `release_report` looks for.
"""

import re
from decimal import Decimal

from ..core.rules import load_rules
from ._call import logged_tool
from .context import Refusal, ToolContext

MARKER = re.compile(r"\[([A-Z]{1,2}-[0-9][0-9A-Za-z.]*)\]")
RECORD_ID = re.compile(r"A-[0-9]+")
SECTION_ID = re.compile(r"M-[0-9][0-9A-Za-z.]*")
KNOWN_ID = re.compile(r"A-[0-9]{6,}|[A-Z]{2}-[0-9]+|M-[0-9][0-9A-Za-z.]*")
NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)?")
ID_TOKEN = re.compile(r"\b[A-Z]{1,2}-[0-9][0-9A-Za-z.]*")
NOT_COUNTED = {"result_hash"}
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
RULES_TOOL = "get_criterion"
RETURNED_BY = ("compute_trend", "ingest_assessments", RULES_TOOL)


def sentences(body: str) -> list:
    return [s.strip() for s in SENTENCE_END.split(body.strip()) if s.strip()]


def numbers_in(text: str) -> set:
    """The numbers written in a text, leaving out IDs such as A-000123 or TR-2."""
    return {Decimal(n) for n in NUMBER.findall(ID_TOKEN.sub("", text))}


def _walk(value):
    """Every string and number inside a nested result."""
    if isinstance(value, dict):
        for key, v in value.items():
            if key not in NOT_COUNTED:
                yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)
    elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
        yield value


def returned_in_run(ctx: ToolContext, run_id: str, findings: list) -> tuple:
    """(ids, numbers) the draft may use.

    IDs: every record and rule ID any tool returned in the run. Numbers: only the numbers in the
    findings the draft was built from, and in the rules get_criterion returned. A number from some
    other learner or subject in the same run is not a number the draft may state.
    """
    rows = ctx.conn.execute(
        "SELECT tool, outputs FROM action_log WHERE run_id = %s AND outcome = 'ok' "
        "AND tool = ANY(%s)",
        (run_id, list(RETURNED_BY)),
    ).fetchall()
    ids, numbers = set(), set()
    for tool, outputs in rows:
        for leaf in _walk(outputs):
            if KNOWN_ID.fullmatch(str(leaf)):
                ids.add(str(leaf))
            if tool == RULES_TOOL:
                numbers |= numbers_in(str(leaf))
    for leaf in _walk(findings):
        numbers |= numbers_in(str(leaf))
    return ids, numbers


def problems_in(
    body: str, ids: set, numbers: set, banned: list, clauses: set | None = None,
    sections: set | None = None,
) -> list:
    """Every problem in a draft. `clauses` and `sections` are the rules of the pinned rulebook
    version and the sections of the pinned methodology: a marker outside them is refused even if a
    tool returned it. Left out, only what the tools returned in the run is checked."""
    problems = []
    for number, sentence in enumerate(sentences(body), start=1):
        markers = MARKER.findall(sentence)
        if not markers:
            problems.append(
                {"reason": "uncited_sentence", "sentence": number, "detail": sentence}
            )
        for marker in markers:
            reason = _marker_problem(marker, ids, clauses, sections)
            if reason:
                problems.append({"reason": reason, "sentence": number, "detail": marker})
        for n in sorted(numbers_in(sentence)):
            if n not in numbers:
                problems.append({"reason": "unknown_number", "sentence": number, "detail": str(n)})
    for word in banned:
        if re.search(rf"\b{re.escape(word)}\b", body, re.IGNORECASE):
            problems.append({"reason": "banned_word", "sentence": None, "detail": word})
    return problems


def _marker_problem(marker, ids, clauses, sections) -> str | None:
    if RECORD_ID.fullmatch(marker):
        return None if marker in ids else "unknown_record"
    if SECTION_ID.fullmatch(marker):
        known = marker in ids and (sections is None or marker in sections)
        return None if known else "unknown_section"
    known = marker in ids and (clauses is None or marker in clauses)
    return None if known else "unknown_clause"


def sources_for(ctx: ToolContext, body: str, version: str, document_id: str | None) -> list:
    """Each distinct marker in the draft, with what it points to: a record to its document, page
    and row; a rule to the rulebook version; a methodology section to its document and page."""
    out = []
    for marker in dict.fromkeys(MARKER.findall(body)):
        if RECORD_ID.fullmatch(marker):
            row = ctx.conn.execute(
                "SELECT subject, academic_year, term, source_document_id, source_page, source_row "
                "FROM assessments WHERE assessment_id = %s",
                (marker,),
            ).fetchone()
            if row:
                subject, year, term, document, page, source_row = row
                out.append(
                    {
                        "id": marker,
                        "kind": "record",
                        "subject": subject,
                        "year": year,
                        "term": term,
                        "document_id": document,
                        "page": page,
                        "row": source_row,
                    }
                )
        elif SECTION_ID.fullmatch(marker):
            row = ctx.conn.execute(
                "SELECT title, page FROM method_chunks WHERE document_id = %s AND section_id = %s",
                (document_id, marker[len("M-") :]),
            ).fetchone()
            if row:
                title, page = row
                out.append(
                    {
                        "id": marker,
                        "kind": "section",
                        "document_id": document_id,
                        "title": title,
                        "page": page,
                    }
                )
        else:
            row = ctx.conn.execute(
                "SELECT text FROM clauses WHERE version_name = %s AND clause_id = %s",
                (version, marker),
            ).fetchone()
            if row:
                out.append(
                    {"id": marker, "kind": "clause", "template_version": version, "text": row[0]}
                )
    return out


@logged_tool("check_citations")
def check_citations(ctx: ToolContext, report_id: str):
    """Check a draft against what the tools returned in the run it was written in.

    A pass sets the draft's `citations_ok`; a failure clears it and lists every problem. Only a
    draft that has passed can be released.
    """
    row = ctx.conn.execute(
        "SELECT body, run_id, template_version, status, findings, methodology_document_id "
        "FROM reports WHERE report_id = %s",
        (report_id,),
    ).fetchone()
    if row is None:
        raise Refusal(f"there is no report {report_id}")
    body, run_id, version, status, findings, document_id = row
    if status != "pending_review":
        raise Refusal(f"{report_id} is {status}; only a draft can be checked")
    ids, numbers = returned_in_run(ctx, run_id, findings)
    rules = load_rules(ctx.conn, version)
    in_version = {
        r[0]
        for r in ctx.conn.execute(
            "SELECT clause_id FROM clauses WHERE version_name = %s", (version,)
        ).fetchall()
    }
    in_methodology = {
        "M-" + r[0]
        for r in ctx.conn.execute(
            "SELECT section_id FROM method_chunks WHERE document_id = %s "
            "AND section_id IS NOT NULL",
            (document_id,),
        ).fetchall()
    }
    problems = problems_in(body, ids, numbers, rules.banned_words, in_version, in_methodology)
    ok = not problems
    ctx.conn.execute("UPDATE reports SET citations_ok = %s WHERE report_id = %s", (ok, report_id))
    result = {
        "status": "ok" if ok else "fail",
        "report_id": report_id,
        "template_version": version,
        "problems": problems,
        "methodology_document_id": document_id,
        "sources": sources_for(ctx, body, version, document_id),
    }
    return result, "ok" if ok else "blocked"
