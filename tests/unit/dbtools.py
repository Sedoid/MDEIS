"""Small helpers for the database tests."""

import os
import subprocess
import sys

import pytest


def refused(conn, error, sql, params=()):
    """Run one statement and expect Postgres to refuse it with `error`.

    It runs inside a savepoint, so the surrounding test transaction stays usable afterwards.
    Returns the exception so the test can look at the message.
    """
    with pytest.raises(error) as info:
        with conn.transaction():
            conn.execute(sql, params)
    return info.value


def one(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def vec(i, size=768):
    """A unit vector with a 1 at position i, in pgvector's text form."""
    values = ["0"] * size
    values[i] = "1"
    return "[" + ",".join(values) + "]"


def make_learner(conn, school="TEST", ref=None):
    return one(
        conn,
        "INSERT INTO learners (school_code, school_ref) VALUES (%s, %s) RETURNING learner_id",
        (school, ref),
    )


def make_template(conn, name="t-test", active=False):
    if active:
        conn.execute("UPDATE templates SET active = false WHERE active")
    conn.execute(
        "INSERT INTO templates (version_name, file_name, sha256, template_id, content, active) "
        "VALUES (%s, %s, repeat('a', 64), 'tid', '{}'::jsonb, %s)",
        (name, name + ".yaml", active),
    )
    return name


def make_document(conn, kind="results", active=False, status="pending", **extra):
    if active:
        conn.execute("UPDATE documents SET active = false WHERE kind = 'methodology' AND active")
    cols = {
        "kind": kind,
        "original_name": "x.pdf",
        "stored_name": "d.bin",
        "sha256": "b" * 64,
        "size_bytes": 10,
        "uploader_id": "u1",
        "status": status,
        "active": active,
        **extra,
    }
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    return one(
        conn,
        f"INSERT INTO documents ({names}) VALUES ({marks}) RETURNING document_id",
        list(cols.values()),
    )


def run_migrate(url, directory=None):
    """Run the migration runner as its own process against `url`."""
    env = {**os.environ, "DATABASE_URL": url}
    if directory:
        env["MDEIS_MIGRATIONS_DIR"] = str(directory)
    return subprocess.run(
        [sys.executable, "-m", "mdeis.db.migrate"], capture_output=True, text=True, env=env
    )
