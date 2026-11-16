"""The school of a session: read from the verified token by the server, never from a tool argument.

No database. `server._run` is called with a fake call and a fake connection, and the context it
builds is looked at. The school is checked in the same way the approver is: a missing, forged or
expired token gives none.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")

from mdeis import server  # noqa: E402
from mdeis.core import approver_token  # noqa: E402
from mdeis.tools.context import ToolContext  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SECRET = "the-toolbox-secret-not-real"


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def call(monkeypatch):
    monkeypatch.setenv("APPROVER_TOKEN_SECRET", SECRET)
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@nowhere/none")
    monkeypatch.setattr(server.psycopg, "connect", lambda url: FakeConnection())

    def run(headers):
        request = SimpleNamespace(request_context=SimpleNamespace(
            request=SimpleNamespace(headers=headers)
        ))
        return server._run(request, lambda ctx, **kw: ctx)

    return run


def signed(school=None, secret=SECRET, **kw):
    return approver_token.sign(secret, "u-ada", "Ada Teacher", school=school, **kw)


def test_the_toolbox_gives_the_tool_the_school_from_the_verified_token(call):
    ctx = call({server.APPROVER_HEADER: signed("OCC")})
    assert isinstance(ctx, ToolContext)
    assert ctx.school == "OCC"
    assert (ctx.approver.id, ctx.approver.name) == ("u-ada", "Ada Teacher")


def test_an_old_token_with_no_school_gives_an_approver_and_no_school(call):
    ctx = call({server.APPROVER_HEADER: signed()})
    assert ctx.approver is not None and ctx.school is None


def test_no_token_gives_no_approver_and_no_school(call):
    ctx = call({})
    assert ctx.approver is None and ctx.school is None


def test_a_forged_token_gives_no_school_even_if_it_names_one(call):
    ctx = call({server.APPROVER_HEADER: signed("OCC", secret="someone-elses-secret")})
    assert ctx.approver is None and ctx.school is None


def test_an_expired_token_gives_no_school(call):
    ctx = call({server.APPROVER_HEADER: signed("OCC", now=1_000, lifetime=60)})
    assert ctx.approver is None and ctx.school is None


def test_a_school_in_another_header_is_not_read(call):
    headers = {"x-school": "ZZZ", "x-school-id": "ZZZ", "school": "ZZZ", "x-run-id": "RUN-1"}
    assert call(headers).school is None
    assert call({**headers, server.APPROVER_HEADER: signed("OCC")}).school == "OCC"


def test_the_context_has_a_school_field_that_defaults_to_none():
    ctx = ToolContext(conn=None, run_id="R")
    assert ctx.school is None
    assert ctx.approver is None


def test_run_reads_the_school_only_from_the_verified_identity():
    """Static: `_run` sets `school=` from a helper that reads the token, not from an argument."""
    source = (ROOT / "src" / "mdeis" / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    run = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_run")
    assert [a.arg for a in run.args.args] == ["request", "tool"]
    assert run.args.kwarg is not None and run.args.kwarg.arg == "arguments"
    keywords = {
        k.arg: ast.unparse(k.value)
        for n in ast.walk(run)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "ToolContext"
        for k in n.keywords
    }
    assert "school" in keywords
    assert "headers" in keywords["school"] or "identity" in keywords["school"]
    assert "arguments" not in keywords["school"]
