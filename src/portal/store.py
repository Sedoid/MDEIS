"""The portal's only database access: add a document, and read documents back.

It connects as `portal_app`, which may INSERT and SELECT on `documents`, SELECT on `document_issues`
and SELECT on three columns of `report_cards` (document, school year, stage), and nothing else. The
queries are plain functions of a connection, so a test can run them inside a transaction.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

SHOWN = (
    "d.document_id, d.kind, d.original_name, d.status, d.failure_kind, d.failure_detail, "
    "d.failure_page, d.failure_row, d.duplicate_of, d.uploader_name, d.created_at, d.finished_at, "
    "d.acknowledged_by, d.acknowledged_at, d.active, rc.stage, rc.academic_year"
)
FROM = "documents d LEFT JOIN report_cards rc ON rc.document_id = d.document_id"
# A card that could not be read, and was later read from the same file, is not a problem any more.
NOT_SUPERSEDED = (
    "NOT (d.status = 'failed' AND EXISTS (SELECT 1 FROM documents o WHERE o.kind = d.kind "
    "AND o.sha256 = d.sha256 AND o.status = 'done'))"
)
LIST_LIMIT = 500
EARLIER_LIMIT = 10


def _plain(row: dict) -> dict:
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}


def list_documents(conn, uploader_id: str | None = None) -> list[dict]:
    """The documents a person may see, newest first. Report cards carry their stage and year."""
    where, args = [NOT_SUPERSEDED], []
    if uploader_id:
        where.append("d.uploader_id = %s")
        args.append(uploader_id)
    rows = conn.execute(
        f"SELECT {SHOWN} FROM {FROM} WHERE {' AND '.join(where)} "
        f"ORDER BY d.created_at DESC, d.document_id DESC LIMIT {LIST_LIMIT}",
        args,
    ).fetchall()
    return [_plain(r) for r in rows]


def document_detail(conn, document_id: str) -> dict | None:
    doc = conn.execute(
        f"SELECT {SHOWN}, d.uploader_id FROM {FROM} WHERE d.document_id = %s", (document_id,)
    ).fetchone()
    if doc is None:
        return None
    issues = conn.execute(
        'SELECT kind, clause_id, page, "row", detail FROM document_issues '
        "WHERE document_id = %s ORDER BY document_issue_id",
        (document_id,),
    ).fetchall()
    return {**_plain(doc), "issues": issues}


def document_file(conn, document_id: str) -> dict | None:
    """Where a document's file is kept, and whose it is. The name on disk stays in the portal."""
    return conn.execute(
        "SELECT stored_name, original_name, kind, uploader_id FROM documents "
        "WHERE document_id = %s",
        (document_id,),
    ).fetchone()


def methodology_state(conn) -> dict:
    """The methodology in force, those waiting for a person to check them, and the earlier ones."""
    rows = [
        _plain(r)
        for r in conn.execute(
            f"SELECT {SHOWN} FROM {FROM} WHERE d.kind = 'methodology' AND {NOT_SUPERSEDED} "
            "ORDER BY d.created_at DESC, d.document_id DESC LIMIT 50"
        ).fetchall()
    ]
    in_force = next((r for r in rows if r["active"]), None)
    waiting = [r for r in rows if r["status"] == "needs_review" and not r["active"]]
    rest = [r for r in rows if r is not in_force and r not in waiting]
    return {"in_force": in_force, "waiting": waiting, "earlier": rest[:EARLIER_LIMIT]}


class PgStore:
    def __init__(self, url: str):
        self.url = url

    def _connect(self):
        return psycopg.connect(self.url, row_factory=dict_row, connect_timeout=5)

    def add(self, *, kind, original_name, stored_name, sha256, size_bytes, uploader_id,
            uploader_name) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "INSERT INTO documents (kind, original_name, stored_name, sha256, size_bytes, "
                "uploader_id, uploader_name) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "RETURNING document_id",
                (kind, original_name, stored_name, sha256, size_bytes, uploader_id, uploader_name),
            ).fetchone()
            conn.commit()
        return row["document_id"]

    def list(self, uploader_id: str | None = None) -> list[dict]:
        with self._connect() as conn:
            return list_documents(conn, uploader_id)

    def get(self, document_id: str) -> dict | None:
        with self._connect() as conn:
            return document_detail(conn, document_id)

    def file_of(self, document_id: str) -> dict | None:
        with self._connect() as conn:
            return document_file(conn, document_id)

    def methodology(self) -> dict:
        with self._connect() as conn:
            return methodology_state(conn)

    def problem(self) -> str | None:
        try:
            self._connect().close()
        except psycopg.Error as error:
            return f"database: {type(error).__name__}"
        return None
