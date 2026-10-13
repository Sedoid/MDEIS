"""The admin steps a person runs from the command line. The agent never calls these (SPEC.md 5.9).

    python -m mdeis.admin load-criteria templates/v2-strict.yaml templates/v1.yaml --by "Ada"
    python -m mdeis.admin ingest --kind results --file card.pdf --as "Ada Teacher"
    python -m mdeis.admin ack-methodology D-000012 --by "Ada Teacher" --note "checked 6.4"

`load-criteria` does what `python -m mdeis.rulebook load` does, and takes the same options. The
rulebook module also has embed, activate, status and search.

`ingest` copies a file into the inbox, records it as a document, and reads it in this process with
the same code the worker uses. `ack-methodology` puts a methodology that needed review in force; it
needs the name of the person who checked it. Both write the diary.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import uuid
from pathlib import Path

import psycopg

from mdeis import rulebook
from mdeis.db import diary
from mdeis.ingest import methodology, worker

# the admin name -> the rulebook command it runs
COMMANDS = {"load-criteria": "load"}
CHUNK = 1 << 20  # read a file this much at a time
USAGE = (
    "usage:\n"
    "  python -m mdeis.admin load-criteria FILE [FILE ...] [--by NAME] [--no-activate]\n"
    '  python -m mdeis.admin ingest --kind results|methodology --file PATH --as "Name"\n'
    '  python -m mdeis.admin ack-methodology D-000012 --by "Name" [--note "..."]'
)


def copy_into_inbox(source: Path, inbox: Path) -> tuple[str, str, int]:
    """Copy a file into the inbox under a name of our own. Returns (stored name, sha256, bytes)."""
    stored = f"{uuid.uuid4().hex}{source.suffix.lower()}"
    digest, size = hashlib.sha256(), 0
    with open(source, "rb") as src, open(inbox / stored, "wb") as dst:
        while block := src.read(CHUNK):
            digest.update(block)
            dst.write(block)
            size += len(block)
    return stored, digest.hexdigest(), size


def ingest(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m mdeis.admin ingest")
    parser.add_argument("--kind", choices=("results", "methodology"), required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--as", dest="name", required=True, help="who is uploading it")
    args = parser.parse_args(argv)
    source, url = Path(args.file), os.environ["DATABASE_URL"]
    if not source.is_file():
        print(f"There is no file at {source}.", file=sys.stderr)
        return 1
    inbox = worker.inbox_dir()
    stored, digest, size = copy_into_inbox(source, inbox)
    with psycopg.connect(url) as conn:
        document_id = conn.execute(
            "INSERT INTO documents (kind, original_name, stored_name, sha256, size_bytes, "
            "uploader_id, uploader_name) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING document_id",
            (args.kind, source.name, stored, digest, size, f"cli:{args.name}", args.name),
        ).fetchone()[0]
        conn.commit()
    diary.write(
        "admin_ingest",
        inputs={"document_id": document_id, "kind": args.kind, "sha256": digest, "by": args.name},
        outputs={"stored": True},
        outcome="ok",
        url=url,
    )
    summary = worker.run_once(url, inbox, only=document_id) or {}
    where = ""
    if summary.get("page"):
        row = f", row {summary['row']}" if summary.get("row") else ""
        where = f" (page {summary['page']}{row})"
    print(f"{document_id}: {args.kind} {summary.get('status', 'not processed')}", end="")
    print(f" {summary.get('failure_kind', '')}{where}".rstrip())
    for key in ("records", "subjects", "sections", "issues"):
        if key in summary:
            print(f"  {key}: {summary[key]}")
    return 0 if summary.get("status") in ("done", "needs_review") else 1


def ack_methodology(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m mdeis.admin ack-methodology")
    parser.add_argument("document_id")
    parser.add_argument("--by", required=True, help="the name of the person who checked it")
    parser.add_argument("--note", default="")
    args = parser.parse_args(argv)
    url = os.environ["DATABASE_URL"]
    with psycopg.connect(url) as conn:
        try:
            done = methodology.acknowledge(conn, args.document_id, args.by, args.note, url)
            conn.commit()
        except methodology.AckRefused as refusal:
            conn.rollback()
            print(f"Refused: {refusal}", file=sys.stderr)
            return 1
    print(f"{done['document_id']} is now the methodology in force (acknowledged by {args.by}).")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(USAGE, file=sys.stderr)
        return 2
    command, rest = argv[0], argv[1:]
    if command in COMMANDS:
        return rulebook.main([COMMANDS[command], *rest])
    if command == "ingest":
        return ingest(rest)
    if command == "ack-methodology":
        return ack_methodology(rest)
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
