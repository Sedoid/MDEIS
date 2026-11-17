"""The database reads and writes of the measure build. Everything that decides something is in
`engine.py` and `patch.py`; this file only loads what they are given and stores what they return.

No model, no sandbox, no template edit. The approver is never read here: a build is by the system.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from psycopg.rows import dict_row

from ..db.diary import jsonb
from ..ingest import methodology
from ..tools.context import Refusal
from . import engine, patch
from .config import BuildRules

DOCUMENT_ID_SHAPE = "D-"  # the registry's document ids start with this (see migration 002)


def load_inputs(conn, document_id, rules: BuildRules) -> engine.BuildInputs:
    """Everything a build needs to know about one methodology and the active template. Raises
    Refusal, in plain words, if the document is not one a build may start from."""
    if not isinstance(document_id, str) or not document_id.startswith(DOCUMENT_ID_SHAPE):
        raise Refusal("a methodology document ID is needed")
    cursor = conn.cursor(row_factory=dict_row)
    document = cursor.execute(
        "SELECT kind, status, active, created_at FROM documents WHERE document_id = %s",
        (document_id,),
    ).fetchone()
    if document is None:
        raise Refusal(f"there is no document {document_id}")
    if document["kind"] != "methodology":
        raise Refusal(f"{document_id} is not a methodology document")
    if document["status"] != "done" or not document["active"]:
        raise Refusal(
            f"{document_id} is not the methodology in force: a build starts from a methodology "
            "that passed the clarity gate and was acknowledged"
        )
    template = cursor.execute(
        "SELECT version_name, content FROM templates WHERE active"
    ).fetchone()
    if template is None:
        raise Refusal("no template version is active, so there are no rules to build against")
    kinds = (methodology.ADDED, methodology.CHANGED, methodology.REMOVED)
    issues = cursor.execute(
        "SELECT kind, ref_id FROM document_issues WHERE document_id = %s AND kind = ANY(%s) "
        "ORDER BY document_issue_id",
        (document_id, list(kinds)),
    ).fetchall()
    return engine.make_inputs(
        document_id=document_id,
        sections=methodology.sections_of(conn, document_id),
        issues=issues,
        first_methodology=previous_document(conn, document_id) is None,
        template_version=template["version_name"],
        content=template["content"],
        rules=rules,
    )


def previous_document(conn, document_id: str) -> str | None:
    """The methodology that was in force before this one (the latest earlier one that was done), or
    None when this is the first."""
    row = conn.execute(
        "SELECT p.document_id FROM documents p, documents d "
        "WHERE d.document_id = %s AND p.kind = 'methodology' AND p.status = 'done' "
        "AND p.document_id <> d.document_id AND p.created_at < d.created_at "
        "ORDER BY p.created_at DESC, p.document_id DESC LIMIT 1",
        (document_id,),
    ).fetchone()
    return row[0] if row else None


def allocator(conn, content: dict) -> Callable[[], int]:
    """The next `measure_params` number, from the database sequence, skipping a number the active
    template already uses. A number is never reused: a build that fails leaves a gap."""
    used = set()
    for rule in content.get("measure_params") or []:
        tail = str(rule.get("id", "")).split("-")[-1]
        if tail.isdigit():
            used.add(int(tail))

    def next_number() -> int:
        while True:
            (number,) = conn.execute("SELECT nextval('measure_param_seq')").fetchone()
            if number not in used:
                used.add(number)
                return number

    return next_number


def store_patch(
    conn, *, measure_id: str, version: int, inputs: engine.BuildInputs, body: dict,
    section_hashes: dict,
) -> str:  # fmt: skip
    """Write a proposed patch, with the hash of its text. Returns its ID."""
    text = patch.canonical_text(body)
    row = conn.execute(
        "INSERT INTO template_patches (measure_id, version, source_document_id, "
        "base_template_version, section_hashes, patch_text, patch_sha256, clauses) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING patch_id",
        (
            measure_id, version, inputs.document_id, body["base_template_version"],
            jsonb(section_hashes), text, patch.patch_sha256(body), jsonb(body["measure_params"]),
        ),
    ).fetchone()
    return row[0]


def section_hashes(inputs: engine.BuildInputs, section_id: str, rules_quoted: list) -> dict:
    """{section id: sha256} for the section built and every section a kept rule was quoted from."""
    wanted = {section_id, *(q.get("quote_section") for q in rules_quoted)}
    return {s.id: s.sha256 for s in inputs.sections if s.id in wanted}


def latest_plan(conn, document_id: str) -> dict:
    """What the most recent plan for a document recorded, from the action log: the sections it
    skipped and the sections a person asked to have built all the same. Both are empty when no plan
    is on record. The demo shows them beside each other."""
    row = conn.execute(
        "SELECT outputs FROM action_log WHERE tool = 'build_plan' AND outcome = 'ok' "
        "AND inputs ->> 'document_id' = %s ORDER BY event_id DESC LIMIT 1",
        (document_id,),
    ).fetchone()
    if not row:
        return {"skipped": [], "build_anyway": []}
    outputs = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    return {
        "skipped": outputs.get("skipped", []),
        "build_anyway": outputs.get("build_anyway", []),
    }


def latest_skipped(conn, document_id: str) -> list:
    """The sections the most recent plan for a document skipped, from the action log: what the
    demo shows beside its 'build it anyway' choice."""
    return latest_plan(conn, document_id)["skipped"]
