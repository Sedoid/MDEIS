"""Shared fixtures for the database tests.

The integration tests run against a real Postgres with pgvector, named by DATABASE_URL (the app
role, the same one the migration step uses). Without DATABASE_URL they are skipped, and the log
must say so.

Each test session builds a throwaway schema from the migrations, runs the tests inside it, and drops
it at the end. So the live `public` schema is never written to: no ID numbers are used up, and no
diary line is added to the real diary. Tests that check the live database (the rights each role
holds, which migrations are recorded) read it by writing `public.` in front of the name.

Every test also works inside a transaction that is rolled back, so tests do not affect each other.
"""

import os
import subprocess
import sys

import pytest

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


@pytest.fixture(scope="session")
def test_db_url():
    """A database URL whose search_path is a throwaway schema with every migration applied.

    Anything a test commits through this URL (the diary helper does) stays in that schema and is
    dropped with it at the end of the session. If a run is killed, an `mdeis_test_...` schema can be
    left behind. It is harmless, and can be listed and dropped by hand.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is not set: integration tests skipped")
    import psycopg
    from psycopg.conninfo import make_conninfo

    name = "mdeis_test_" + os.urandom(4).hex()
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{name}"')
    # `public` stays on the path only so that the `vector` type is found.
    scoped = make_conninfo(url, options=f"-c search_path={name},public")
    try:
        built = subprocess.run(
            [sys.executable, "-m", "mdeis.db.migrate"],
            capture_output=True,
            text=True,
            env={**os.environ, "DATABASE_URL": scoped},
        )
        if built.returncode != 0:
            pytest.fail(f"could not build the test schema from the migrations:\n{built.stderr}")
        yield scoped
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{name}" CASCADE')


@pytest.fixture
def conn(test_db_url):
    import psycopg

    with psycopg.connect(test_db_url) as c:
        yield c
        c.rollback()


@pytest.fixture
def roles():
    return ROLES
