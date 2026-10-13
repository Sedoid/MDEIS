"""The portal's only database access: add a document, and read documents back.

It connects as `portal_app`, which may INSERT and SELECT on `documents` and SELECT on
`document_issues`, and nothing else. Every call opens its own short connection.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

SHOWN = (
    "document_id, kind, original_name, status, failure_kind, failure_detail, failure_page, "
    "failure_row, duplicate_of, uploader_name, created_at, finished_at, acknowledged_by"
)
LIST_LIMIT = 100


def _plain(row: dict) -> dict:
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}


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
        where, args = ("WHERE uploader_id = %s", (uploader_id,)) if uploader_id else ("", ())
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {SHOWN} FROM documents {where} "
                f"ORDER BY created_at DESC LIMIT {LIST_LIMIT}",
                args,
            ).fetchall()
        return [_plain(r) for r in rows]

    def get(self, document_id: str) -> dict | None:
        with self._connect() as conn:
            doc = conn.execute(
                f"SELECT {SHOWN}, uploader_id FROM documents WHERE document_id = %s", (document_id,)
            ).fetchone()
            if doc is None:
                return None
            issues = conn.execute(
                'SELECT kind, clause_id, page, "row", detail FROM document_issues '
                "WHERE document_id = %s ORDER BY document_issue_id",
                (document_id,),
            ).fetchall()
        return {**_plain(doc), "issues": issues}

    def problem(self) -> str | None:
        try:
            self._connect().close()
        except psycopg.Error as error:
            return f"database: {type(error).__name__}"
        return None
