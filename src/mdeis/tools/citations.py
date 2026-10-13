"""check_citations: every sentence of a draft must be backed by what the tools returned in the run.

The rules, in order of what they catch (docs/step-08.md):
  uncited_sentence  a sentence with no [source] marker
  unknown_record    a [A-000123] marker no tool returned in this run
  unknown_clause    a [TR-2] marker no tool returned in this run
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

MARKER = re.compile(r"\[([A-Z]{1,2}-[0-9][0-9.]*)\]")
RECORD_ID = re.compile(r"A-[0-9]+")
KNOWN_ID = re.compile(r"A-[0-9]{6,}|[A-Z]{2}-[0-9]+|M-[0-9][0-9.]*")
NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)?")
ID_TOKEN = re.compile(r"\b[A-Z]{1,2}-[0-9][0-9.]*")
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


def problems_in(body: str, ids: set, numbers: set, banned: list) -> list:
    problems = []
    for number, sentence in enumerate(sentences(body), start=1):
        markers = MARKER.findall(sentence)
        if not markers:
            problems.append(
                {"reason": "uncited_sentence", "sentence": number, "detail": sentence}
            )
        for marker in markers:
            if marker in ids:
                continue
            reason = "unknown_record" if RECORD_ID.fullmatch(marker) else "unknown_clause"
            problems.append({"reason": reason, "sentence": number, "detail": marker})
        for n in sorted(numbers_in(sentence)):
            if n not in numbers:
                problems.append({"reason": "unknown_number", "sentence": number, "detail": str(n)})
    for word in banned:
        if re.search(rf"\b{re.escape(word)}\b", body, re.IGNORECASE):
            problems.append({"reason": "banned_word", "sentence": None, "detail": word})
    return problems


def sources_for(ctx: ToolContext, body: str, version: str) -> list:
    """Each distinct marker in the draft, with what it points to."""
    out = []
    for marker in dict.fromkeys(MARKER.findall(body)):
        if RECORD_ID.fullmatch(marker):
            row = ctx.conn.execute(
                "SELECT subject, academic_year, term FROM assessments WHERE assessment_id = %s",
                (marker,),
            ).fetchone()
            if row:
                subject, year, term = row
                out.append(
                    {"id": marker, "kind": "record", "subject": subject, "year": year, "term": term}
                )
        else:
            row = ctx.conn.execute(
                "SELECT text FROM clauses WHERE version_name = %s AND clause_id = %s",
                (version, marker),
            ).fetchone()
            if row:
                out.append({"id": marker, "kind": "clause", "text": row[0]})
    return out


@logged_tool("check_citations")
def check_citations(ctx: ToolContext, report_id: str):
    """Check a draft against what the tools returned in the run it was written in.

    A pass sets the draft's `citations_ok`; a failure clears it and lists every problem. Only a
    draft that has passed can be released.
    """
    row = ctx.conn.execute(
        "SELECT body, run_id, template_version, status, findings FROM reports "
        "WHERE report_id = %s",
        (report_id,),
    ).fetchone()
    if row is None:
        raise Refusal(f"there is no report {report_id}")
    body, run_id, version, status, findings = row
    if status != "pending_review":
        raise Refusal(f"{report_id} is {status}; only a draft can be checked")
    ids, numbers = returned_in_run(ctx, run_id, findings)
    problems = problems_in(body, ids, numbers, load_rules(ctx.conn, version).banned_words)
    ok = not problems
    ctx.conn.execute("UPDATE reports SET citations_ok = %s WHERE report_id = %s", (ok, report_id))
    result = {
        "status": "ok" if ok else "fail",
        "report_id": report_id,
        "template_version": version,
        "problems": problems,
        "sources": sources_for(ctx, body, version),
    }
    return result, "ok" if ok else "blocked"
