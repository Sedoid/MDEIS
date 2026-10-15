"""Shared set-up for the acceptance tests of the practice data."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SEED = 42


@pytest.fixture(scope="session")
def root():
    return ROOT


@pytest.fixture(scope="session")
def templates_dir():
    return ROOT / "templates"


@pytest.fixture(scope="session")
def built(templates_dir):
    """(files, learners, rules) for seed 42, built once. Nothing is written to disk."""
    from synthetic import generate

    return generate.build_outputs(SEED, templates_dir)


@pytest.fixture(scope="session")
def files(built):
    return built[0]


@pytest.fixture(scope="session")
def learners(built):
    return built[1]


@pytest.fixture(scope="session")
def rules(built):
    return built[2]


@pytest.fixture(scope="session")
def key(files):
    return json.loads(files["truth/answer_key.json"])


@pytest.fixture(scope="session")
def problems(files):
    return json.loads(files["truth/injected_problems.json"])


# --------------------------------------------------------------------------- the tool tests
@pytest.fixture(scope="session")
def tool_schema(files, templates_dir):
    """A throwaway schema with both rulebooks and the practice cohort loaded.

    Committed once for the session; every test then works on its own connection and rolls back.
    """
    import tool_env

    url = tool_env.database_url()
    if not url:
        pytest.skip("DATABASE_URL is not set: the tool contract tests are skipped")
    name, scoped = tool_env.build_schema(url)
    try:
        tool_env.load_rulebooks(scoped, templates_dir)
        tool_env.load_cohort(
            scoped,
            json.loads(files["cohort/learners.json"]),
            json.loads(files["cohort/cards.json"]),
        )
        yield scoped
    finally:
        tool_env.drop_schema(url, name)


@pytest.fixture
def make_ctx(tool_schema):
    """Make a tool context.

    `make_ctx()` has no approver; `make_ctx(("u-1", "Ada Teacher"))` has one. The approver is part
    of the context, set here the way the verified token will set it. It is never a tool argument.
    The context does not commit, so each test is rolled back.
    """
    import uuid

    import psycopg

    opened = []

    def make(approver=None, run_id=None):
        try:
            from mdeis.tools.context import Approver, ToolContext
        except ImportError as e:
            pytest.fail(f"mdeis.tools.context is not built yet ({e})", pytrace=False)
        conn = psycopg.connect(tool_schema)
        opened.append(conn)
        return ToolContext(
            conn=conn,
            run_id=run_id or f"RUN-{uuid.uuid4()}",
            approver=Approver(id=approver[0], name=approver[1]) if approver else None,
            database_url=tool_schema,
            commit=False,
        )

    yield make
    for conn in opened:
        conn.rollback()
        conn.close()
