"""Shared fixtures for the database tests.

The integration tests run against a real Postgres with pgvector, named by DATABASE_URL (the app
role, the same one the migration step uses). Without DATABASE_URL they are skipped, and the log
must say so. They never commit: each test works inside a transaction that is rolled back, so the
append-only diary and the frozen tables are left exactly as they were.
"""

import os

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
