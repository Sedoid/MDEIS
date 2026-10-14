"""The admin steps a person runs from the command line. The agent never calls these (SPEC.md 5.9).

    python -m mdeis.admin load-criteria templates/v2-strict.yaml templates/v1.yaml --by "Ada"
    python -m mdeis.admin ingest --kind results --file card.pdf --as "Ada Teacher"
    python -m mdeis.admin ingest-dir --kind results --dir data/synthetic/cards --as "Demo loader"
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
    '  python -m mdeis.admin ingest-dir --kind results --dir FOLDER --as "Name" [--recursive]\n'
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


def ingest_file(url: str, inbox: Path, kind: str, source: Path, name: str) -> tuple[str, dict]:
    """Store one file as a document and read it now. Returns (document id, what happened)."""
    stored, digest, size = copy_into_inbox(source, inbox)
    uploader = f"cli:{name}"
    # Inserted already claimed ('processing'), so the background worker (ingestd) leaves it to us.
    with psycopg.connect(url) as conn:
        document_id = conn.execute(
            "INSERT INTO documents (kind, original_name, stored_name, sha256, size_bytes, "
            "uploader_id, uploader_name, status, started_at, attempts) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 'processing', now(), 1) RETURNING document_id",
            (kind, source.name, stored, digest, size, uploader, name),
        ).fetchone()[0]
        conn.commit()
    diary.write(
        "admin_ingest",
        inputs={"document_id": document_id, "kind": kind, "sha256": digest, "by": name},
        outputs={"stored": True},
        outcome="ok",
        url=url,
    )
    claimed = {
        "document_id": document_id,
        "kind": kind,
        "stored_name": stored,
        "sha256": digest,
        "uploader_id": uploader,
        "attempts": 1,
    }
    with psycopg.connect(url) as conn:
        return document_id, worker.process(conn, claimed, inbox, url)


def describe(document_id: str, kind: str, summary: dict) -> list[str]:
    where = ""
    if summary.get("page"):
        row = f", row {summary['row']}" if summary.get("row") else ""
        where = f" (page {summary['page']}{row})"
    first = f"{document_id}: {kind} {summary.get('status', 'not processed')}"
    lines = [f"{first} {summary.get('failure_kind', '')}{where}".rstrip()]
    if summary.get("duplicate_of"):
        lines.append(f"  the same file as {summary['duplicate_of']}: nothing added")
    for key in ("records", "subjects", "sections", "issues"):
        if key in summary:
            lines.append(f"  {key}: {summary[key]}")
    return lines


def worked(summary: dict) -> bool:
    return summary.get("status") in ("done", "needs_review")


def ingest(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m mdeis.admin ingest")
    parser.add_argument("--kind", choices=("results", "methodology"), required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--as", dest="name", required=True, help="who is uploading it")
    args = parser.parse_args(argv)
    source = Path(args.file)
    if not source.is_file():
        print(f"There is no file at {source}.", file=sys.stderr)
        return 1
    url, inbox = os.environ["DATABASE_URL"], worker.inbox_dir()
    document_id, summary = ingest_file(url, inbox, args.kind, source, args.name)
    print("\n".join(describe(document_id, args.kind, summary)))
    return 0 if worked(summary) else 1


def find_cards(folder: Path, recursive: bool = False) -> tuple[list[Path], list[Path]]:
    """The PDF files in a folder, in name order, and the other files that will be left alone."""
    if not folder.is_dir():
        return [], []
    found = sorted(folder.rglob("*") if recursive else folder.glob("*"))
    files = [p for p in found if p.is_file() and not p.name.startswith(".")]
    pdfs = [p for p in files if p.suffix.lower() == ".pdf"]
    return pdfs, [p for p in files if p.suffix.lower() != ".pdf"]


def ingest_dir(argv: list[str]) -> int:
    """Read every PDF in a folder, in name order. One line for each, then a count."""
    parser = argparse.ArgumentParser(prog="python -m mdeis.admin ingest-dir")
    parser.add_argument("--kind", choices=("results", "methodology"), required=True)
    parser.add_argument("--dir", required=True)
    parser.add_argument("--as", dest="name", required=True, help="who is uploading them")
    parser.add_argument("--recursive", action="store_true", help="also read the sub-folders")
    args = parser.parse_args(argv)
    folder = Path(args.dir)
    files, others = find_cards(folder, args.recursive)
    if not files:
        print(f"There are no PDF files in {folder}.", file=sys.stderr)
        return 1
    for other in others:
        print(f"left alone (not a PDF): {other.relative_to(folder)}")
    url, inbox = os.environ["DATABASE_URL"], worker.inbox_dir()
    failed = 0
    for source in files:
        document_id, summary = ingest_file(url, inbox, args.kind, source, args.name)
        ok = worked(summary)
        failed += not ok
        note = "" if ok else f"  <-- {summary.get('failure_kind', 'not processed')}"
        shown = source.relative_to(folder)
        print(f"{'ok  ' if ok else 'FAIL'}  {shown}: {summary.get('status')}{note}")
    print(f"\n{len(files) - failed} of {len(files)} read; {failed} failed")
    return 0 if not failed else 1


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
    if command == "ingest-dir":
        return ingest_dir(rest)
    if command == "ack-methodology":
        return ack_methodology(rest)
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
