"""Shared fixtures for the database tests.

The integration tests run against a real Postgres with pgvector, named by DATABASE_URL (the app
role, the same one the migration step uses). Without DATABASE_URL they are skipped, and the log
must say so. They never commit: each test works inside a transaction that is rolled back, so the
append-only diary and the frozen tables are left exactly as they were.
"""

import os

import pytest
from dbtools import run_migrate

# Role names come from .env (same defaults as .env.example). They are names, not secrets.
ROLES = {
    "app": os.environ.get("MDEIS_APP_USER", "mdeis_app"),
    "ro": os.environ.get("MDEIS_RO_USER", "mdeis_ro"),
    "portal": os.environ.get("PORTAL_DB_USER", "portal_app"),
    "auto": os.environ.get("N8N_AUTO_DB_USER", "n8n_auto"),
    "orchestrator": os.environ.get("ORCHESTRATOR_DB_USER", "orchestrator_app"),
    "n8n": os.environ.get("N8N_DB_USER", "n8n_app"),
}
DB_NAME = os.environ.get("MDEIS_DB_NAME", "mdeis")
ORCH_DB_NAME = os.environ.get("ORCHESTRATOR_DB_NAME", "orchestrator")


@pytest.fixture
def conn():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is not set: integration tests skipped")
    import psycopg

    with psycopg.connect(url) as c:
        yield c
        c.rollback()


@pytest.fixture
def roles():
    return ROLES


@pytest.fixture
def scratch_schema(conn):
    """An empty schema to run the whole migration set into, then throw away.

    Anything that must COMMIT (the migration runner, the diary helper) works here instead of in
    `public`, so the live tables, their sequences and the live diary are left alone.
    Yields (schema name, connection URL whose search_path points at it; `public` comes second,
    only so the `vector` type is found).
    """
    from psycopg.conninfo import make_conninfo

    name = "migtest_" + os.urandom(4).hex()
    conn.commit()
    conn.execute(f'CREATE SCHEMA "{name}"')
    conn.commit()
    url = make_conninfo(os.environ["DATABASE_URL"], options=f"-c search_path={name},public")
    yield name, url
    conn.rollback()
    conn.execute(f'DROP SCHEMA "{name}" CASCADE')
    conn.commit()


@pytest.fixture
def built_schema(scratch_schema):
    """The scratch schema with every migration applied. Yields (name, url)."""
    name, url = scratch_schema
    done = run_migrate(url)
    assert done.returncode == 0, done.stderr
    return name, url
