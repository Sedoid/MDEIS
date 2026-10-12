"""Load a rulebook version into the database, give each rule a vector, and search the rules.

    python -m mdeis.rulebook load templates/v2-strict.yaml templates/v1.yaml
    python -m mdeis.rulebook embed          # fill in any missing vectors (load does this too)
    python -m mdeis.rulebook activate v1    # make an already loaded version the one new work uses
    python -m mdeis.rulebook status
    python -m mdeis.rulebook search "expected reading level for Form 1" --version v1

A version is known by its file name without the extension (v1.yaml is version `v1`). Loading:

* checks the file with the template loader (mdeis.core.template), so a bad file never gets in;
* stores the file's checksum, the whole parsed content, and one row per rule with its ID
  (TR-1, ST-2, ...), its sentence and its own values;
* is safe to repeat: the same file again changes nothing (no rows, no vectors, no diary line);
* refuses a changed file under a name that is already loaded. A loaded version never changes.
  Save the edit under a new file name instead;
* makes a version it has just loaded the active one, unless told not to. Only one can be active.
  A repeat of a file that is already loaded leaves the active version alone;
* records who did it when given `--by "Name"` (or RULEBOOK_BY in the environment), in the rulebook's
  row and in the diary. Loading or activating changes which rules new reports are pinned to, so
  say who did it.

Vectors are a second step, so a stopped embedder never blocks loading. `load` tries it straight
after; if the embedder is unreachable the rules stay loaded without vectors, a warning says so,
and `embed` can be run later. The embedder is the llama.cpp server (EMBEDDER_URL, OpenAI-style
/v1/embeddings). nomic-embed-text wants a task prefix on its input: documents and queries differ.

Every school number lives in the template file. This module holds none (a test checks it). The
only numbers here are the named constants below, which are about HTTP, batching and exit codes.

Every change is written to the diary (one line per load, embed, activate or refusal).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx
import psycopg

from mdeis.core.template import SECTION_AREAS, Template, TemplateError, load_template
from mdeis.db import diary

# nomic-embed-text-v1.5 is trained with these prefixes; without them the vectors are weaker.
DOCUMENT_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "

# Tuning, not school rules (a test allows these and no other number).
HTTP_TIMEOUT_S = 30
EMBED_BATCH = 16
SEARCH_K = 3
EXIT_VECTORS_MISSING = 2  # the exit code when vectors were required and could not be made

LOADED_BY = "rulebook cli"


class RulebookError(Exception):
    """The command cannot do what was asked. The message says why, in plain words."""


class EmbedderUnavailable(RulebookError):
    """The embedder did not answer, or answered with something unusable."""


# --------------------------------------------------------------------------- the embedder
class Embedder:
    """A client for the llama.cpp embedding server."""

    def __init__(self, url: str | None = None, model: str | None = None):
        self.url = (url or os.environ.get("EMBEDDER_URL") or "").rstrip("/")
        if not self.url:
            raise EmbedderUnavailable("EMBEDDER_URL is not set")
        self.model = model or os.environ.get("EMBED_MODEL_FILE") or None

    def _call(self, texts: list[str]) -> tuple[list[list[float]], str | None]:
        try:
            r = httpx.post(
                f"{self.url}/v1/embeddings",
                json={"input": texts, "model": self.model or "embedder"},
                timeout=HTTP_TIMEOUT_S,
            )
            r.raise_for_status()
            body = r.json()
            items = sorted(body["data"], key=lambda item: item["index"])
            vectors = [item["embedding"] for item in items]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            raise EmbedderUnavailable(
                f"the embedder at {self.url} did not give vectors: {e}"
            ) from e
        if len(vectors) != len(texts):
            raise EmbedderUnavailable(f"asked for {len(texts)} vectors, got {len(vectors)}")
        return vectors, body.get("model")

    def embed(self, texts: list[str], prefix: str) -> tuple[list[list[float]], str]:
        """Vectors for `texts`, and the model name to record with them."""
        vectors: list[list[float]] = []
        reported = None
        for start in range(0, len(texts), EMBED_BATCH):
            batch = [prefix + t for t in texts[start : start + EMBED_BATCH]]
            got, reported = self._call(batch)
            vectors.extend(got)
        name = self.model or reported
        if not name:
            raise EmbedderUnavailable("no model name: set EMBED_MODEL_FILE")
        return vectors, name


def expected_dimension() -> int:
    raw = os.environ.get("EMBED_DIM")
    if not raw:
        raise RulebookError("EMBED_DIM is not set (it comes from .env)")
    return int(raw)


def column_dimension(conn: psycopg.Connection) -> int:
    """The number of values the clauses.embedding column holds."""
    row = conn.execute(
        "SELECT atttypmod FROM pg_attribute "
        "WHERE attrelid = 'clauses'::regclass AND attname = 'embedding'"
    ).fetchone()
    return row[0]


def check_dimension(conn: psycopg.Connection, vectors: list[list[float]]) -> None:
    want, column = expected_dimension(), column_dimension(conn)
    if want != column:
        raise RulebookError(
            f"EMBED_DIM is {want} but the database column holds {column} values. "
            "They must match: fix .env or the schema."
        )
    for v in vectors:
        if len(v) != want:
            raise RulebookError(f"the embedder returned {len(v)} values, the schema expects {want}")


def as_vector(values: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in values) + "]"


# --------------------------------------------------------------------------- loading
@dataclass
class LoadResult:
    version: str
    status: str  # "loaded" or "unchanged"
    clauses: int
    active: bool


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rule_rows(template: Template) -> list[tuple[str, str, str, str | None, dict]]:
    """(clause id, area code, text, methodology reference, the rule's own values) for every rule."""
    rows = []
    for section, area in SECTION_AREAS.items():
        for rule in getattr(template, section):
            values = rule.model_dump(mode="json")
            params = {k: v for k, v in values.items() if k not in ("id", "text", "method_ref")}
            rows.append((rule.id, area, rule.text, rule.method_ref, params))
    return rows


def load_file(
    conn: psycopg.Connection,
    path: Path,
    *,
    activate: bool = True,
    url: str | None = None,
    by: str | None = None,
    _retry: bool = False,
) -> LoadResult:
    """Load one rulebook file. Commits on success. Raises RulebookError if it must be refused.

    `by` is the person doing it. If another run loads the same version between our check and our
    insert, the database refuses the second insert; we then look again once and report what the
    other run left (unchanged if it was the same file, a refusal if it was a different one).
    """
    path = Path(path)
    if not path.is_file():
        raise RulebookError(f"{path} is not a file")
    try:
        template = load_template(path)
    except TemplateError as e:
        raise RulebookError(f"{path.name} is not a valid rulebook: {e}") from e
    name, digest = path.stem, _digest(path)
    already = conn.execute(
        "SELECT sha256, active FROM templates WHERE version_name = %s", (name,)
    ).fetchone()
    if already:
        if already[0] == digest:
            n = conn.execute(
                "SELECT count(*) FROM clauses WHERE version_name = %s", (name,)
            ).fetchone()[0]
            return LoadResult(name, "unchanged", n, already[1])
        diary.write(
            "load_rulebook",
            inputs={"version": name, "sha256": digest},
            outputs={"reason": "a different file is already loaded under this name"},
            outcome="refused",
            approver_name=by,
            url=url,
        )
        raise RulebookError(
            f"version {name} is already loaded with different content. A loaded version never "
            f"changes: save your edit under a new file name (for example {name}-b.yaml)."
        )

    rows = _rule_rows(template)
    try:
        with diary.logged_call(
            "load_rulebook", inputs={"version": name, "sha256": digest}, work=conn, url=url
        ) as call:
            call.approver_name = by
            _insert_version(conn, name, path, digest, template, rows, activate, by)
            call.outputs = {
                "clauses": len(rows),
                "active": activate,
                "approved_by": template.approved_by,
            }
    except psycopg.errors.UniqueViolation:
        # Another run loaded this version after we looked. Look again once, then give up.
        if _retry:
            raise
        conn.rollback()
        return load_file(conn, path, activate=activate, url=url, by=by, _retry=True)
    return LoadResult(name, "loaded", len(rows), activate)


def _insert_version(conn, name, path, digest, template, rows, activate, by) -> None:
    """The writes of one load. The caller wraps them in one logged transaction."""
    if activate:
        conn.execute("UPDATE templates SET active = false WHERE active")
    conn.execute(
        "INSERT INTO templates (version_name, file_name, sha256, template_id, declared_version,"
        " language, institution_code, institution_name, methodology_version, start_date,"
        " review_date, approved_by, content, active, loaded_by)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            name,
            path.name,
            digest,
            template.template_id,
            str(template.version),
            template.language,
            template.institution.code,
            template.institution.name,
            template.methodology_version,
            template.start_date,
            template.review_date,
            template.approved_by,
            diary.jsonb(template.model_dump(mode="json")),
            activate,
            by or LOADED_BY,
        ),
    )
    for clause_id, area, text, method_ref, params in rows:
        conn.execute(
            "INSERT INTO clauses (version_name, clause_id, area, text, method_ref, params,"
            " text_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                name,
                clause_id,
                area,
                text,
                method_ref,
                diary.jsonb(params),
                hashlib.sha256(text.encode("utf-8")).hexdigest(),
            ),
        )


def activate_version(
    conn: psycopg.Connection, name: str, *, url: str | None = None, by: str | None = None
) -> bool:
    """Make an already loaded version the active one. False if it already was."""
    row = conn.execute("SELECT active FROM templates WHERE version_name = %s", (name,)).fetchone()
    if row is None:
        raise RulebookError(f"no version named {name} is loaded")
    if row[0]:
        return False
    with diary.logged_call(
        "activate_rulebook", inputs={"version": name}, work=conn, url=url
    ) as call:
        call.approver_name = by
        conn.execute("UPDATE templates SET active = false WHERE active")
        conn.execute("UPDATE templates SET active = true WHERE version_name = %s", (name,))
    return True


# --------------------------------------------------------------------------- vectors
def embed_missing(
    conn: psycopg.Connection,
    embedder: Embedder,
    *,
    force: bool = False,
    url: str | None = None,
) -> int:
    """Give a vector to every rule that has none (or all of them with force). Returns the count.

    Only rules without a vector are sent to the embedder, so running this again does nothing.
    Raises EmbedderUnavailable if the embedder cannot be reached; the rules stay as they are.
    """
    where = "" if force else "WHERE embedding IS NULL"
    todo = conn.execute(
        "SELECT version_name, clause_id, text FROM clauses "
        f"{where} ORDER BY version_name, clause_id"
    ).fetchall()
    conn.rollback()  # do not hold a transaction open while waiting for the embedder
    if not todo:
        return 0
    vectors, model = embedder.embed([text for _, _, text in todo], DOCUMENT_PREFIX)
    check_dimension(conn, vectors)
    conn.rollback()
    with diary.logged_call(
        "embed_rulebook", inputs={"model": model, "force": force}, work=conn, url=url
    ) as call:
        for (version, clause_id, _), vec in zip(todo, vectors, strict=True):
            conn.execute(
                "UPDATE clauses SET embedding = %s::vector, embedding_model = %s "
                "WHERE version_name = %s AND clause_id = %s",
                (as_vector(vec), model, version, clause_id),
            )
        call.outputs = {"embedded": len(todo), "versions": sorted({t[0] for t in todo})}
    return len(todo)


# --------------------------------------------------------------------------- reading
def search_clauses(
    conn: psycopg.Connection,
    embedder: Embedder,
    query: str,
    *,
    version: str | None = None,
    k: int = SEARCH_K,
) -> list[dict]:
    """The `k` rules whose meaning is closest to `query`, best first.

    `version` defaults to the active one. Each result has clause_id, text and score (cosine
    similarity, higher is closer).
    """
    if version is None:
        row = conn.execute("SELECT version_name FROM templates WHERE active").fetchone()
        if row is None:
            raise RulebookError("no rulebook version is active: load one first")
        version = row[0]
    have, total = conn.execute(
        "SELECT count(embedding), count(*) FROM clauses WHERE version_name = %s", (version,)
    ).fetchone()
    if total == 0:
        raise RulebookError(f"no version named {version} is loaded")
    if have == 0:
        raise RulebookError(f"version {version} has no vectors yet: run `embed`")
    vectors, _ = embedder.embed([query], QUERY_PREFIX)
    check_dimension(conn, vectors)
    q = as_vector(vectors[0])
    rows = conn.execute(
        "SELECT clause_id, text, 1 - (embedding <=> %s::vector) AS score FROM clauses "
        "WHERE version_name = %s AND embedding IS NOT NULL "
        "ORDER BY embedding <=> %s::vector, clause_id LIMIT %s",
        (q, version, q, k),
    ).fetchall()
    return [
        {"clause_id": clause_id, "text": text, "score": float(score)}
        for clause_id, text, score in rows
    ]


def status(conn: psycopg.Connection) -> dict:
    versions = conn.execute(
        "SELECT t.version_name, t.active, t.approved_by, left(t.sha256, 12), "
        "       count(c.clause_id), count(c.embedding), t.loaded_by "
        "FROM templates t LEFT JOIN clauses c USING (version_name) "
        "GROUP BY t.version_name, t.active, t.approved_by, t.sha256, t.loaded_at, t.loaded_by "
        "ORDER BY t.loaded_at, t.version_name"
    ).fetchall()
    ids = {
        v: {
            r[0]
            for r in conn.execute("SELECT clause_id FROM clauses WHERE version_name = %s", (v,))
        }
        for v in (row[0] for row in versions)
    }
    union = set().union(*ids.values()) if ids else set()
    differences = {v: sorted(union - s) for v, s in ids.items() if union - s}
    return {"versions": versions, "same_ids": not differences, "missing": differences}


# --------------------------------------------------------------------------- the command
def _print_status(conn: psycopg.Connection) -> None:
    s = status(conn)
    if not s["versions"]:
        print("no rulebook version is loaded")
        return
    print(
        f"{'version':<16}{'active':<8}{'rules':<7}{'vectors':<9}{'approved by':<14}"
        f"{'loaded by':<14}file hash"
    )
    for name, active, approved, short, rules, vectors, loaded_by in s["versions"]:
        print(
            f"{name:<16}{'yes' if active else '-':<8}{rules:<7}{vectors:<9}"
            f"{approved or 'nobody yet':<14}{loaded_by or '-':<14}{short}"
        )
    if len(s["versions"]) > 1:
        if s["same_ids"]:
            print("every version has the same rule IDs")
        else:
            for v, missing in s["missing"].items():
                print(f"WARNING: {v} lacks {', '.join(missing)}")


def _embed_step(conn, url, *, required: bool, force: bool = False) -> int:
    try:
        n = embed_missing(conn, Embedder(), force=force, url=url)
    except EmbedderUnavailable as e:
        print(f"WARNING: no vectors were made. {e}", file=sys.stderr)
        print("The rules are loaded. Run the same command again when the embedder is up.")
        return EXIT_VECTORS_MISSING if required else 0
    print(f"vectors added: {n}" if n else "vectors: nothing to add")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m mdeis.rulebook", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    load = sub.add_parser("load", help="load rulebook files (the last one becomes active)")
    load.add_argument("files", nargs="+", type=Path)
    load.add_argument("--no-activate", action="store_true", help="load without making it active")
    load.add_argument("--no-embed", action="store_true", help="do not make vectors now")
    load.add_argument("--by", help='who is loading it, for example --by "Ada Teacher"')
    load.add_argument(
        "--require-embeddings", action="store_true", help="exit 2 if vectors are missing"
    )
    emb = sub.add_parser("embed", help="make vectors for rules that have none")
    emb.add_argument("--force", action="store_true", help="redo every vector")
    emb.add_argument("--require-embeddings", action="store_true")
    act = sub.add_parser("activate", help="make a loaded version the active one")
    act.add_argument("version")
    act.add_argument("--by", help="who is switching it")
    sub.add_parser("status", help="show what is loaded")
    find = sub.add_parser("search", help="find rules by meaning")
    find.add_argument("query")
    find.add_argument("--version")
    find.add_argument("--k", type=int, default=SEARCH_K)
    args = p.parse_args(argv)

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1
    by = (getattr(args, "by", None) or os.environ.get("RULEBOOK_BY") or "").strip() or None
    try:
        with psycopg.connect(url) as conn:
            if args.cmd == "load":
                for path in args.files:
                    r = load_file(conn, path, activate=not args.no_activate, url=url, by=by)
                    word = "loaded" if r.status == "loaded" else "already loaded, unchanged"
                    print(
                        f"{r.version}: {word} ({r.clauses} rules{', active' if r.active else ''})"
                    )
                code = 0
                if not args.no_embed:
                    code = _embed_step(conn, url, required=args.require_embeddings)
                _print_status(conn)
                return code
            if args.cmd == "embed":
                code = _embed_step(conn, url, required=args.require_embeddings, force=args.force)
                _print_status(conn)
                return code
            if args.cmd == "activate":
                changed = activate_version(conn, args.version, url=url, by=by)
                print(f"{args.version} is now active" if changed else f"{args.version} already was")
                return 0
            if args.cmd == "status":
                _print_status(conn)
                return 0
            for hit in search_clauses(conn, Embedder(), args.query, version=args.version, k=args.k):
                print(f"{hit['score']:.3f}  {hit['clause_id']:<6}{hit['text']}")
            return 0
    except (RulebookError, diary.DiaryUnavailable) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    except psycopg.Error as e:
        # The database could not be reached, or refused a statement. Say so in a line, no traceback.
        first_line = (str(e).strip().splitlines() or [""])[0]
        print(f"refused: a database problem ({type(e).__name__}): {first_line}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
