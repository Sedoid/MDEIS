"""Which tools the model may see (docs/SPEC.md section 3, D-015).

An explicit allow-list. A tool that is not on it is never shown to the model and cannot be called,
even if the model asks for it by name. A tool whose parameters include anything that looks like an
approver or a user is dropped and reported: the approver comes from the signed-in session, never
from an argument the model writes.
"""

from __future__ import annotations

MDEIS_TOOLS = (
    "ingest_assessments",
    "resolve_issue",
    "compute_trend",
    "get_criterion",
    "draft_summary",
    "check_citations",
    "release_report",
)
APPROVER_LIKE = {"approver", "approver_id", "approver_name", "user", "user_id", "by"}
CLASS_VIEW_TOOL = "stage_gap_counts"  # offered only when the class view is switched on
MDEIS = "mdeis"
PG = "pg"


def approver_like(schema: dict) -> set:
    """The parameter names of a tool that look like an approver or a user."""
    return APPROVER_LIKE & set((schema or {}).get("properties", {}))


def bind(listed: dict, pg_allowed: tuple, class_view: bool = False) -> tuple:
    """Choose the tools to offer.

    `listed` maps a server (`mdeis`, `pg`) to its tools, each a dict with name, description and
    inputSchema. Returns (definitions in the model's format, {tool name: server}, dropped names).
    """
    allowed = {MDEIS: set(MDEIS_TOOLS), PG: set(pg_allowed)}
    if class_view:
        allowed[MDEIS].add(CLASS_VIEW_TOOL)
    definitions, owner, dropped = [], {}, []
    for server in (MDEIS, PG):
        for tool in listed.get(server, []):
            name = tool["name"]
            if name not in allowed[server] or name in owner:
                continue
            if approver_like(tool.get("inputSchema")):
                dropped.append(name)
                continue
            owner[name] = server
            definitions.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool.get("description") or "",
                        "parameters": tool.get("inputSchema") or {"type": "object"},
                    },
                }
            )
    return definitions, owner, dropped
