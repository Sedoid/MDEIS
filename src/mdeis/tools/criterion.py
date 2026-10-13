"""get_criterion: find a rule in the rulebook, by its ID or by what it is about."""

import re

from mdeis import rulebook

from ._call import logged_tool
from ._shared import active_methodology, with_hash
from .context import Refusal, ToolContext

CLAUSE_ID = re.compile(r"[A-Za-z]{2}-[0-9]+")
DEFAULT_RESULTS = 3
SCORE_PLACES = 4
SECTION_ID = re.compile(r"[Mm]-[0-9][0-9A-Za-z.]*")
MARK = "M-"


def _version(ctx: ToolContext, template_version: str | None) -> str:
    if template_version is not None:
        found = ctx.conn.execute(
            "SELECT version_name FROM templates WHERE version_name = %s", (template_version,)
        ).fetchone()
    else:
        found = ctx.conn.execute("SELECT version_name FROM templates WHERE active").fetchone()
    if found is None:
        raise Refusal("no such rulebook version" if template_version else "no rulebook is active")
    return found[0]


def _by_id(ctx, version, clause_id) -> list:
    rows = ctx.conn.execute(
        "SELECT clause_id, text, params FROM clauses WHERE version_name = %s AND clause_id = %s",
        (version, clause_id),
    ).fetchall()
    return [{"clause_id": cid, "text": text, "params": params} for cid, text, params in rows]


def _by_meaning(ctx, version, query, k) -> tuple:
    """The closest rules by meaning; plain text matching if the embedder cannot be reached."""
    try:
        found = rulebook.search_clauses(
            ctx.conn, rulebook.Embedder(), query, version=version, k=k
        )
        return "meaning", [
            {
                "clause_id": c["clause_id"],
                "text": c["text"],
                "score": round(c["score"], SCORE_PLACES),
            }
            for c in found
        ]
    except rulebook.RulebookError:
        like = "%" + query.replace("%", "").replace("_", "") + "%"
        rows = ctx.conn.execute(
            "SELECT clause_id, text FROM clauses WHERE version_name = %s AND text ILIKE %s "
            "ORDER BY clause_id LIMIT %s",
            (version, like, k),
        ).fetchall()
        return "text", [{"clause_id": r[0], "text": r[1]} for r in rows]


def _sections(ctx, document_id, query, k) -> list:
    """Sections of the methodology in force: by ID (`M-6.4`), else by meaning, else by words."""
    if document_id is None:
        return []
    if SECTION_ID.fullmatch(query):
        rows = ctx.conn.execute(
            "SELECT section_id, title, page, text FROM method_chunks "
            "WHERE document_id = %s AND section_id = %s",
            (document_id, query[len(MARK) :]),
        ).fetchall()
    else:
        rows = _sections_by_meaning(ctx, document_id, query, k)
    return [
        {"section_id": MARK + section_id, "title": title, "page": page, "text": text}
        for section_id, title, page, text in rows
    ]


def _sections_by_meaning(ctx, document_id, query, k) -> list:
    try:
        vectors, _ = rulebook.Embedder().embed([query], rulebook.QUERY_PREFIX)
        vector = rulebook.as_vector(vectors[0])
        return ctx.conn.execute(
            "SELECT section_id, title, page, text FROM method_chunks "
            "WHERE document_id = %s AND section_id IS NOT NULL AND embedding IS NOT NULL "
            "ORDER BY embedding <=> %s::vector, ordinal LIMIT %s",
            (document_id, vector, k),
        ).fetchall()
    except rulebook.RulebookError:
        like = "%" + query.replace("%", "").replace("_", "") + "%"
        return ctx.conn.execute(
            "SELECT section_id, title, page, text FROM method_chunks "
            "WHERE document_id = %s AND section_id IS NOT NULL AND text ILIKE %s "
            "ORDER BY ordinal LIMIT %s",
            (document_id, like, k),
        ).fetchall()


@logged_tool("get_criterion")
def get_criterion(
    ctx: ToolContext, query: str, template_version: str | None = None, k: int = DEFAULT_RESULTS
):
    """Return rules from the rulebook and sections of the methodology in force.

    A rule ID such as TR-2 returns that rule with its own words, and a section ID such as M-6.4 that
    section. Anything else is searched by meaning (the embedder); if it cannot be reached, by
    matching the words. Nothing found returns an empty list, not an error.
    """
    version = _version(ctx, template_version)
    text = query.strip()
    document = active_methodology(ctx.conn)
    if CLAUSE_ID.fullmatch(text):
        how, clauses, sections = "id", _by_id(ctx, version, text.upper()), []
    elif SECTION_ID.fullmatch(text):
        how, clauses, sections = "id", [], _sections(ctx, document, text, k)
    else:
        how, clauses = _by_meaning(ctx, version, text, k)
        sections = _sections(ctx, document, text, k)
    result = {
        "template_version": version,
        "methodology_document_id": document,
        "query": text,
        "match": how,
        "clauses": clauses,
        "sections": sections,
    }
    return with_hash(result), "ok"
