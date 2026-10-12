"""Apply numbered SQL migrations in order. Safe to run twice.

Reads DATABASE_URL from the environment. Each file in migrations/ named like 001_init.sql is
applied once, in order, and recorded in schema_migrations with a checksum. With no migration
files it does nothing and exits 0.

Safe to run twice: a file already recorded is skipped.
Safe to run at the same time as itself: a lock makes the second run wait for the first.
Safe against edits: if an already-applied file has changed since, it stops and says which one.
A migration is never edited after it has been applied anywhere. The next number is added instead.
"""

import hashlib
import os
import sys
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(
    os.environ.get("MDEIS_MIGRATIONS_DIR") or Path(__file__).parent / "migrations"
)
LOCK_KEY = "mdeis.migrate"


def migration_files() -> list[Path]:
    """The migration files, in the order they are applied."""
    return sorted(MIGRATIONS_DIR.glob("[0-9]*.sql"))


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1
    files = migration_files()
    applied_now = 0
    with psycopg.connect(url) as conn:
        conn.execute("SELECT pg_advisory_lock(hashtext(%s))", (LOCK_KEY,))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now(), sha256 text)"
        )
        conn.execute("ALTER TABLE schema_migrations ADD COLUMN IF NOT EXISTS sha256 text")
        conn.commit()
        done = dict(conn.execute("SELECT name, sha256 FROM schema_migrations").fetchall())
        for f in files:
            digest = checksum(f)
            if f.name in done:
                recorded = done[f.name]
                if recorded is not None and recorded != digest:
                    print(
                        f"{f.name} was changed after it was applied. Do not edit an applied "
                        "migration: put the change in a new numbered file.",
                        file=sys.stderr,
                    )
                    return 1
                continue
            conn.execute(f.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (name, sha256) VALUES (%s, %s)", (f.name, digest)
            )
            conn.commit()
            applied_now += 1
            print(f"applied {f.name}")
    print(f"migrations: {applied_now} applied now, {len(files) - applied_now} already applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
