"""ingestd: the document worker.

It claims `pending` documents one at a time, reads them with the readers in this package (ordinary
code, no model), and writes the rows and the document's final status in one transaction. A document
that fails stores no rows. Every document leaves one line in the diary.

    python -m mdeis.ingest.worker                  run until stopped
    python -m mdeis.ingest.worker --once           process what is waiting, then stop
    python -m mdeis.ingest.worker --healthcheck    exit 0 if the database answers

Safety:
* two workers never take the same document (`FOR UPDATE SKIP LOCKED`);
* the file is read from the inbox only by the name the portal gave it, and its SHA-256 must match;
* the reader runs in a child process with a time limit, so a file that hangs it cannot stop the
  worker;
* a document stuck in `processing` is put back once, then failed with `parse_timeout`;
* the same file twice (same SHA-256 and kind) is marked a duplicate of the first and adds nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psycopg

from mdeis.db import diary

from . import methodology, report_card
from .store import store_report_card

POLL_SECONDS = 2
PARSE_TIMEOUT_SECONDS = 30
STUCK_AFTER_MINUTES = 5
MAX_ATTEMPTS = 2  # a stuck document is tried this many times in all
HEARTBEAT = Path("/tmp/ingestd.heartbeat")
DEFAULT_INBOX = "/inbox"
TOOL_NAME = "ingestd"

INTERNAL_ERROR = "internal_error"
PARSE_TIMEOUT = "parse_timeout"

CLAIM_SQL = (
    "UPDATE documents SET status = 'processing', started_at = now(), attempts = attempts + 1 "
    "WHERE document_id = ("
    "  SELECT document_id FROM documents WHERE status = 'pending' "
    "  AND (%(only)s::text IS NULL OR document_id = %(only)s) "
    "  ORDER BY created_at, document_id FOR UPDATE SKIP LOCKED LIMIT 1) "
    "RETURNING document_id, kind, stored_name, sha256, uploader_id, attempts"
)
FIELDS = ("document_id", "kind", "stored_name", "sha256", "uploader_id", "attempts")


def inbox_dir() -> Path:
    return Path(os.environ.get("INBOX_DIR", DEFAULT_INBOX))


def claim(conn, only: str | None = None) -> dict | None:
    """Take one pending document (or the named one) and mark it `processing`. Commits at once."""
    row = conn.execute(CLAIM_SQL, {"only": only}).fetchone()
    conn.commit()
    return dict(zip(FIELDS, row, strict=True)) if row else None


def recover_stuck(conn) -> int:
    """Put documents stuck in `processing` back once; fail them with `parse_timeout` after that."""
    rows = conn.execute(
        "UPDATE documents SET "
        " status = CASE WHEN attempts < %(max)s THEN 'pending' ELSE 'failed' END, "
        " failure_kind = CASE WHEN attempts < %(max)s THEN failure_kind ELSE %(kind)s END, "
        " failure_detail = CASE WHEN attempts < %(max)s THEN failure_detail "
        "   ELSE 'the reader did not finish, twice' END, "
        " finished_at = CASE WHEN attempts < %(max)s THEN NULL ELSE now() END "
        "WHERE status = 'processing' AND started_at < now() - make_interval(mins => %(mins)s) "
        "RETURNING document_id",
        {"max": MAX_ATTEMPTS, "kind": PARSE_TIMEOUT, "mins": STUCK_AFTER_MINUTES},
    ).fetchall()
    conn.commit()
    return len(rows)


def _run_reader(module: str, path: Path) -> tuple[dict | None, str | None]:
    """Run a reader in a child process. Returns (its JSON, None) or (None, the failure kind)."""
    try:
        done = subprocess.run(
            [sys.executable, "-m", module, str(path)],
            capture_output=True,
            text=True,
            timeout=PARSE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return None, PARSE_TIMEOUT
    if done.returncode != 0:
        return None, INTERNAL_ERROR
    try:
        return json.loads(done.stdout), None
    except ValueError:
        return None, INTERNAL_ERROR


def _fail(conn, doc, kind, detail, page=None, row=None) -> dict:
    """Record a failed document. Nothing it produced is kept: the caller rolled back first."""
    conn.execute(
        "UPDATE documents SET status = 'failed', failure_kind = %s, failure_detail = %s, "
        "failure_page = %s, failure_row = %s, finished_at = now() WHERE document_id = %s",
        (kind, detail, page, row, doc["document_id"]),
    )
    return {"status": "failed", "failure_kind": kind, "page": page, "row": row}


def _log(doc, outputs, outcome, url) -> int:
    inputs = {k: doc[k] for k in ("document_id", "kind", "sha256", "uploader_id")}
    return diary.write(TOOL_NAME, inputs=inputs, outputs=outputs, outcome=outcome, url=url)


def process(conn, doc: dict, inbox: Path, url: str | None = None) -> dict:
    """Process one claimed document to its end. Returns what happened (the diary has it too)."""
    try:
        outcome, summary = _process(conn, doc, inbox)
    except diary.DiaryUnavailable:
        conn.rollback()
        raise  # the document stays `processing`; recovery will put it back
    except Exception as error:  # a bug: leave no rows and say so, safely
        conn.rollback()
        detail = f"the worker stopped on {type(error).__name__}"
        summary = _fail(conn, doc, INTERNAL_ERROR, detail)
        outcome = "error"
    summary["event"] = _log(doc, summary, outcome, url)
    conn.commit()
    if summary.get("status") == "done" and summary.get("active"):
        embed_sections(conn, doc["document_id"])
    return summary


def _process(conn, doc, inbox) -> tuple[str, dict]:
    earlier = conn.execute(
        "SELECT document_id FROM documents WHERE kind = %s AND sha256 = %s AND status = 'done' "
        "AND document_id <> %s AND duplicate_of IS NULL ORDER BY created_at, document_id LIMIT 1",
        (doc["kind"], doc["sha256"], doc["document_id"]),
    ).fetchone()
    if earlier:
        conn.execute(
            "UPDATE documents SET status = 'done', duplicate_of = %s, finished_at = now() "
            "WHERE document_id = %s",
            (earlier[0], doc["document_id"]),
        )
        return "ok", {"status": "done", "duplicate_of": earlier[0], "records": 0}
    name = Path(doc["stored_name"])
    path = inbox / name.name
    if name.name != doc["stored_name"] or not path.is_file():
        return "refused", _fail(conn, doc, INTERNAL_ERROR, "the stored file is not in the inbox")
    if hashlib.sha256(path.read_bytes()).hexdigest() != doc["sha256"]:
        detail = "the stored file does not match its hash"
        return "refused", _fail(conn, doc, INTERNAL_ERROR, detail)
    if doc["kind"] == "results":
        return _results(conn, doc, path)
    if doc["kind"] == "methodology":
        return _methodology(conn, doc, path)
    return "refused", _fail(conn, doc, "unsupported_type", f"unknown document kind {doc['kind']}")


def _results(conn, doc, path) -> tuple[str, dict]:
    data, problem = _run_reader("mdeis.ingest.report_card", path)
    if problem:
        return "refused", _fail(conn, doc, problem, "the report card reader did not finish")
    result = report_card.ParseResult.from_dict(data)
    if result.failure:
        f = result.failure
        return "refused", _fail(conn, doc, f.kind, f.detail, f.page, f.row)
    stored = store_report_card(conn, doc["document_id"], result.card)
    conn.execute(
        "UPDATE documents SET status = 'done', finished_at = now() WHERE document_id = %s",
        (doc["document_id"],),
    )
    return "ok", {"status": "done", "records": stored["records"], "subjects": stored["subjects"]}


def _methodology(conn, doc, path) -> tuple[str, dict]:
    data, problem = _run_reader("mdeis.ingest.methodology", path)
    if problem:
        return "refused", _fail(conn, doc, problem, "the methodology reader did not finish")
    if "failure" in data:
        f = data["failure"]
        return "refused", _fail(conn, doc, f["kind"], f["detail"])
    stored = methodology.store_methodology(
        conn, doc["document_id"], methodology.Parsed.from_dict(data["parsed"])
    )
    return "ok", stored


def embed_sections(conn, document_id: str) -> None:
    """Give the sections of a methodology in force a vector, if the embedder is there.

    This is the one network call in the worker and it goes to the embedder only. If it fails the
    document stays as it is and `embedding_status` says `failed`.
    """
    if not os.environ.get("EMBEDDER_URL"):
        return
    from mdeis import rulebook

    try:
        rows = conn.execute(
            "SELECT chunk_id, title, text FROM method_chunks WHERE document_id = %s "
            "AND embedding IS NULL ORDER BY ordinal",
            (document_id,),
        ).fetchall()
        conn.rollback()
        if rows:
            texts = [f"{title or ''}\n{text}".strip() for _, title, text in rows]
            vectors, _ = rulebook.Embedder().embed(texts, rulebook.DOCUMENT_PREFIX)
            rulebook.check_dimension(conn, vectors)
            for (chunk_id, _, _), vector in zip(rows, vectors, strict=True):
                conn.execute(
                    "UPDATE method_chunks SET embedding = %s::vector WHERE chunk_id = %s",
                    (rulebook.as_vector(vector), chunk_id),
                )
        status = "done"
    except Exception:
        conn.rollback()
        status = "failed"
    conn.execute(
        "UPDATE documents SET embedding_status = %s WHERE document_id = %s", (status, document_id)
    )
    conn.commit()


def run_once(url: str, inbox: Path | None = None, only: str | None = None) -> dict | None:
    """Recover stuck documents, claim one, process it. None when nothing was waiting."""
    with psycopg.connect(url) as conn:
        recover_stuck(conn)
        doc = claim(conn, only)
        if doc is None:
            return None
        return process(conn, doc, inbox or inbox_dir(), url)


def healthy(url: str) -> bool:
    try:
        psycopg.connect(url, connect_timeout=3).close()
        return True
    except psycopg.Error:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mdeis.ingest.worker")
    parser.add_argument("--once", action="store_true", help="process what is waiting, then stop")
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args(argv)
    url = os.environ["DATABASE_URL"]
    if args.healthcheck:
        return 0 if healthy(url) else 1
    print("ingestd: reading documents from the inbox", flush=True)
    while True:
        if healthy(url):
            HEARTBEAT.touch()
        try:
            summary = run_once(url)
        except Exception as error:  # (the class only: never a connection string or a file name)
            message = f"ingestd: could not process ({type(error).__name__})"
            print(message, file=sys.stderr, flush=True)
            summary = None
        if summary is None:
            if args.once:
                return 0
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
