"""The tool server's PDF route and its place in the system (T-056). No database and no network: the
export function is replaced by a fake that records what it is given, the diary by a list, and the
route is mounted on a plain Starlette app.

What is held here: the route needs the gateway's key AND a good signed token, and refuses with a
typed reason and a diary row otherwise; the person and the school come from the token and nowhere
else; the body is closed; a good answer is the file with a download header, a refusal is typed JSON
with no file; the diary failing means no file; and the file is made only by this route, never by a
model tool.

T-076 changed the last point on purpose (an owner decision of that session): the model may call
`export_report`, a tool that only CHECKS that a report is released and returns its number and file
name. There is still no tool that returns bytes or takes a file, a person or a school, and the
orchestrator still names neither the export code nor this route. The tests below say that.
"""

import ast
import asyncio
import datetime
import importlib
import inspect
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("starlette")

from starlette.applications import Starlette  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402
from test_human_routes import Collector  # noqa: E402

from mdeis import human_routes  # noqa: E402
from mdeis.core import approver_token  # noqa: E402
from mdeis.db import diary  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
SECRET = "approver-secret-not-real"
KEY = "human-key-not-real"
REPORT = "R-000001"
PATH = f"/human/reports/{REPORT}/pdf"
PDF = b"%PDF-1.4\nfake file for a route test\n%%EOF\n"
WHEN = datetime.datetime(2026, 11, 17, 9, 30, tzinfo=datetime.UTC)


def token(user="u-1", name="Ms Tutor", school="TST", secret=SECRET, **more):
    return approver_token.sign(secret, user, name, school=school, **more)


def good(**over):
    headers = {"X-Human-Route-Key": KEY, "X-Approver-Token": token()}
    headers.update(over)
    return {k: v for k, v in headers.items() if v is not None}


class FakeExport:
    """Records each call; answers with what the test set."""

    def __init__(self):
        self.calls = []
        self.answer = {"status": "ok", "report_id": REPORT, "file_name": f"{REPORT}.pdf",
                       "pdf": PDF, "sha256": "f" * 64, "size_bytes": len(PDF)}

    def __call__(self, ctx, report_id, generated_at, practice_data=False):
        self.calls.append(SimpleNamespace(
            approver=ctx.approver, school=ctx.school, report_id=report_id,
            generated_at=generated_at, practice_data=practice_data, run_id=ctx.run_id))
        if isinstance(self.answer, Exception):
            raise self.answer
        return dict(self.answer)


@contextmanager
def no_database():
    yield object()


@pytest.fixture
def world(monkeypatch):
    rows = []

    def write(tool, **kw):
        rows.append({"tool": tool, **kw})
        return len(rows)

    monkeypatch.setattr(diary, "write", write)
    env = {"HUMAN_ROUTE_KEY": KEY, "APPROVER_TOKEN_SECRET": SECRET, "DATABASE_URL": "unused"}
    fake = FakeExport()
    collector = Collector()
    human_routes.install_reports(
        collector, connect=no_database, env=env, commit=False, export=fake, clock=lambda: WHEN
    )
    client = TestClient(Starlette(routes=collector.routes))
    return SimpleNamespace(client=client, export=fake, rows=rows, env=env)


def post(world, path=PATH, headers=None, **kw):
    return world.client.post(path, headers=good() if headers is None else headers, **kw)


# --------------------------------------------------------------------------- the good answer
def test_a_good_key_and_token_give_the_file_as_a_download(world):
    answer = post(world)
    assert answer.status_code == 200 and answer.content == PDF
    assert answer.headers["content-type"] == "application/pdf"
    assert answer.headers["content-disposition"] == f'attachment; filename="{REPORT}.pdf"'
    assert answer.headers["cache-control"] == "no-store"


def test_the_export_is_given_the_person_and_school_from_the_token_the_path_and_the_clock(world):
    post(world, headers=good(**{"X-Approver-Token": token("u-9", "Mr Nine", "OCC")}))
    (call,) = world.export.calls
    assert (call.approver.id, call.approver.name, call.school) == ("u-9", "Mr Nine", "OCC")
    assert call.report_id == REPORT and call.generated_at == WHEN
    assert call.practice_data is False


@pytest.mark.parametrize("value,expected", [("true", True), ("1", True), ("YES", True),
                                            ("on", True), ("false", False), ("", False),
                                            ("no", False), ("maybe", False)])
def test_the_practice_footer_comes_from_the_deployment_setting(world, value, expected):
    world.env["PRACTICE_DATA_ONLY"] = value
    post(world)
    assert world.export.calls[0].practice_data is expected


def test_nothing_in_the_query_or_the_headers_or_the_body_can_name_the_person(world):
    answer = post(
        world, params={"approver": "x", "approver_name": "x", "school": "x"},
        headers=good(**{"X-Approver-Name": "Mallory", "X-OpenWebUI-User-Name": "Mallory"}),
        json={},
    )
    assert answer.status_code == 200
    assert world.export.calls[0].approver.name == "Ms Tutor"
    assert world.export.calls[0].school == "TST"


# --------------------------------------------------------------------------- the gate
@pytest.mark.parametrize("headers,reason,status", [
    ({"X-Human-Route-Key": None}, "no_key", 401),
    ({"X-Human-Route-Key": "wrong"}, "wrong_key", 401),
    ({"X-Approver-Token": None}, "no_token", 401),
    ({"X-Approver-Token": "not-a-token"}, "bad_token", 401),
    ({"X-Approver-Token": "signed-with-another-secret"}, "bad_token", 401),
    ({"X-Approver-Token": "signed-without-a-school"}, "no_school", 403),
])
def test_without_the_key_or_a_good_token_there_is_no_file_a_typed_reason_and_a_diary_row(
    world, headers, reason, status
):
    # A token lasts five minutes, so these two are signed here, not when the tests are collected.
    signed = {
        "signed-with-another-secret": lambda: token(secret="another-secret"),
        "signed-without-a-school": lambda: token(school=None),
    }
    name = headers.get("X-Approver-Token")
    if name in signed:
        headers = {**headers, "X-Approver-Token": signed[name]()}
    answer = post(world, headers=good(**headers))
    assert answer.status_code == status
    assert answer.json()["status"] == "refused" and answer.json()["reason"] == reason
    assert answer.headers["content-type"].startswith("application/json")
    assert world.export.calls == []
    (row,) = world.rows
    assert row["tool"] == human_routes.REFUSED_TOOL and row["outcome"] == "refused"
    assert row["inputs"] == {"route": human_routes.PATHS["report_pdf"], "report_id": REPORT}


def test_an_expired_token_is_refused(world):
    old = approver_token.sign(SECRET, "u-1", "Ms Tutor", school="TST", now=time.time() - 3600)
    answer = post(world, headers=good(**{"X-Approver-Token": old}))
    assert (answer.status_code, answer.json()["reason"]) == (401, "expired_token")
    assert world.export.calls == []


def test_with_no_key_set_on_the_server_the_route_is_off(world):
    world.env["HUMAN_ROUTE_KEY"] = ""
    answer = post(world)
    assert (answer.status_code, answer.json()["reason"]) == (503, "routes_off")
    assert world.export.calls == []


def test_a_refused_attempt_with_a_good_token_names_the_person_in_the_diary(world):
    answer = post(world, content=b'{"approver": "Mallory"}')
    assert answer.status_code == 422
    (row,) = world.rows
    assert (row["approver_id"], row["approver_name"]) == ("u-1", "Ms Tutor")


# --------------------------------------------------------------------------- the body and the id
def test_the_body_is_closed_it_may_be_empty_or_an_empty_object_and_nothing_else(world):
    assert post(world).status_code == 200
    assert post(world, content=b"{}").status_code == 200
    for raw in (b'{"report_id": "R-000002"}', b'{"approver": "x"}', b"[]", b"not json"):
        answer = post(world, content=raw)
        assert answer.status_code in (400, 422), raw
        assert answer.json()["status"] == "refused"
    assert post(world, content=b"x" * 9000).status_code == 400
    assert len(world.export.calls) == 2


@pytest.mark.parametrize("bad", ["R-12", "r-000001", "LP-000001", "R-000001x", "R-"])
def test_a_malformed_report_id_is_not_found_and_the_export_is_not_called(world, bad):
    answer = post(world, path=f"/human/reports/{bad}/pdf")
    assert answer.status_code == 404 and answer.json()["reason"] == "report_not_found"
    assert world.export.calls == []
    assert world.rows[-1]["inputs"]["report_id"] is None
    assert world.rows[-1]["approver_name"] == "Ms Tutor"


def test_only_post_is_accepted(world):
    assert world.client.get(PATH, headers=good()).status_code == 405
    assert world.client.put(PATH, headers=good()).status_code == 405


# --------------------------------------------------------------------------- export refusals
@pytest.mark.parametrize("reason,status", [
    ("not_signed_in", 401), ("no_school", 403), ("report_not_found", 404),
    ("report_not_released", 409),
])
def test_a_refusal_from_the_export_is_typed_json_with_no_file(world, reason, status):
    world.export.answer = {"status": "refused", "reason": reason, "detail": "plain words",
                           "logged_event_id": 5}
    answer = post(world)
    assert answer.status_code == status
    assert answer.json() == {"status": "refused", "reason": reason, "detail": "plain words"}
    assert PDF not in answer.content
    assert world.rows == [], "the export wrote its own diary row; the route adds none"


def test_an_answer_the_route_does_not_know_is_a_502_and_is_logged(world):
    world.export.answer = {"status": "weird"}
    answer = post(world)
    assert answer.status_code == 502 and answer.json()["reason"] == "unexpected_answer"
    assert world.rows[-1]["outcome"] == "error"


def test_when_the_diary_is_down_there_is_no_file(world, monkeypatch):
    world.export.answer = diary.DiaryUnavailable("down")

    def down(*a, **k):
        raise diary.DiaryUnavailable("down")

    monkeypatch.setattr(diary, "write", down)
    answer = post(world)
    assert answer.status_code == 503 and answer.json()["reason"] == "log_unavailable"
    assert PDF not in answer.content


def test_a_failure_inside_the_export_is_a_500_that_shows_nothing_of_it(world):
    world.export.answer = RuntimeError("secret detail: R-000001 body text")
    answer = post(world)
    assert answer.status_code == 500 and answer.json()["reason"] == "error"
    assert "secret detail" not in answer.text
    assert world.rows[-1]["outcome"] == "error"


# --------------------------------------------------------------------------- not a model tool
def server_source():
    return (SRC / "mdeis" / "server.py").read_text("utf-8")


def test_the_server_adds_the_pdf_route_by_one_call_outside_the_class_view_block():
    tree = ast.parse(server_source())
    top = [ast.unparse(n.value) for n in tree.body
           if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)]
    assert top.count("human_routes.install_reports(mcp)") == 1
    everywhere = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert everywhere.count("human_routes.install_reports(mcp)") == 1


def test_the_server_has_exactly_one_export_tool_and_it_names_no_export_function():
    tree = ast.parse(server_source())
    tools = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and any(
        "tool" in ast.unparse(d) for d in n.decorator_list)]
    assert tools, "the test no longer finds the tools"
    assert [t for t in tools if "export" in t or "pdf" in t] == ["export_report"], tools
    for word in ("export_released_report", "report_export", "render_report_pdf"):
        assert word not in server_source(), word


@pytest.mark.parametrize("on", [False, True], ids=["view off", "view on"])
def test_the_tool_list_has_one_export_tool_that_takes_only_a_number_and_the_pdf_route_is_there(
    monkeypatch, on
):
    pytest.importorskip("mcp")
    from mdeis import server

    try:
        if on:
            monkeypatch.setenv("CLASS_VIEW_ENABLED", "true")
        else:
            monkeypatch.delenv("CLASS_VIEW_ENABLED", raising=False)
        server = importlib.reload(server)
        listed = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
        assert [n for n in listed if "export" in n or "pdf" in n] == ["export_report"], listed
        assert set(listed["export_report"].inputSchema["properties"]) == {"report_id"}
        assert len(listed) == (16 if on else 11), "the tool list is 11, or 16 with the class view"
        paths = {getattr(r, "path", None) for r in server.mcp.streamable_http_app().routes}
        assert "/human/reports/{report_id}/pdf" in paths
    finally:
        monkeypatch.delenv("CLASS_VIEW_ENABLED", raising=False)
        importlib.reload(server)


def test_the_orchestrators_allow_list_has_the_one_export_tool_and_no_tool_that_returns_a_file():
    from orchestrator import tools

    offered = set(tools.MDEIS_TOOLS) | {tools.CLASS_VIEW_TOOL}
    assert [n for n in offered if "export" in n or "pdf" in n] == ["export_report"], offered
    assert not [n for n in offered if "download" in n or "file" in n]
    assert "release_report" in offered, "the allow-list still holds the tools it held"


def test_the_orchestrator_names_the_tool_but_not_the_export_code_or_the_download_route():
    for path in (SRC / "orchestrator").rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            text = path.read_text("utf-8", errors="ignore")
            for word in ("report_pdf", "export_released_report", "/pdf", "render_report_pdf",
                         "mdeis.export"):
                assert word not in text, (path, word)


def test_the_export_modules_import_no_model_client_and_no_network_client():
    banned = {"openai", "anthropic", "langchain", "langgraph", "httpx", "requests", "urllib",
              "socket", "orchestrator", "gateway", "portal"}
    for path in (SRC / "mdeis" / "export").glob("*.py"):
        tree = ast.parse(path.read_text("utf-8"))
        found = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
                 for a in n.names}
        found |= {n.module.split(".")[0] for n in ast.walk(tree)
                  if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module}
        assert not found & banned, (path, found & banned)


def test_the_export_takes_no_argument_that_names_a_person_or_a_school():
    from mdeis.export import report_export

    names = set(inspect.signature(report_export.export_released_report).parameters)
    assert names == {"ctx", "report_id", "generated_at", "practice_data"}
    assert not names & {"approver", "approver_id", "approver_name", "user", "school", "by"}
