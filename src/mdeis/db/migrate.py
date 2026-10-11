"""Apply numbered SQL migrations in order. Safe to run twice.

Reads DATABASE_URL from the environment. Each file in migrations/ named like 001_init.sql is
applied once and recorded in schema_migrations. With no migration files it does nothing and
exits 0, which is the state before T-003 adds 001_init.sql.
"""
import os
import sys
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1
    files = sorted(MIGRATIONS_DIR.glob("[0-9]*.sql"))
    applied_now = 0
    with psycopg.connect(url) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        conn.commit()
        done = {row[0] for row in conn.execute("SELECT name FROM schema_migrations")}
        for f in files:
            if f.name in done:
                continue
            conn.execute(f.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (f.name,))
            conn.commit()
            applied_now += 1
            print(f"applied {f.name}")
    print(f"migrations: {applied_now} applied now, {len(files) - applied_now} already applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
