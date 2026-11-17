"""Who can reach the human-only routes, and who cannot (T-062). Mostly static: the source is read.

* The key is known to the gateway and the tool server, and to nothing else: not the orchestrator,
  not the model path, not the portal, not the chat assistant's tool list.
* The tool server's MCP tool list never offers an approve or a reject tool, with the routes added.
* The routes are added only through `human_routes.install`, only with the class view on.
* The human routes module imports no model client and no network client.
"""

import ast
import asyncio
import importlib
from pathlib import Path

import pytest
from orchestrator_fakes import spec

from orchestrator import tools
from orchestrator.config import load_settings
from orchestrator.lessons import GRAPH_TOOLS

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
KEY_NAME = "HUMAN_ROUTE_KEY"
TEXT_FILES = {".py", ".md", ".js", ".html"}
HEADER = "X-Human-Route-Key"
NAMES_FOR_APPROVAL = (
    "approve_lesson_plan", "reject_lesson_plan", "lesson_approve_plan", "lesson_reject_plan",
    "approve_lesson", "reject_lesson", "approve_plan", "reject_plan",
)
FIVE_ROUTES = {
    "/human/lessons/options", "/human/lessons", "/human/lessons/{plan_id}",
    "/human/lessons/{plan_id}/approve", "/human/lessons/{plan_id}/reject",
}


def python_files(*parts):
    return sorted(p for p in SRC.joinpath(*parts).rglob("*.py") if "__pycache__" not in p.parts)


def everything_under(*parts):
    root = SRC.joinpath(*parts)
    return sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


# --------------------------------------------------------------------------- who knows the key
def test_the_key_is_named_only_by_the_gateway_and_the_human_routes():
    holders = {
        str(p.relative_to(SRC))
        for p in SRC.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix in TEXT_FILES
        and KEY_NAME in p.read_text("utf-8")
    }
    assert holders == {"gateway/app.py", "mdeis/human_routes.py"}


@pytest.mark.parametrize("place", ["orchestrator", "portal"])
def test_neither_the_orchestrator_nor_the_portal_names_the_key_the_header_or_the_routes(place):
    for path in everything_under(place):
        text = path.read_text("utf-8")
        for word in (KEY_NAME, HEADER, HEADER.lower(), "/human/", "human_routes"):
            assert word not in text, (path, word)


def test_the_orchestrators_settings_read_no_variable_that_could_hold_the_key():
    config = (SRC / "orchestrator" / "config.py").read_text("utf-8")
    assert "HUMAN" not in config and "PORTAL" not in config


def test_the_model_path_has_no_way_to_name_an_approval_tool():
    for path in everything_under("orchestrator"):
        text = path.read_text("utf-8")
        for name in NAMES_FOR_APPROVAL:
            assert name not in text, (path, name)


@pytest.mark.parametrize("view", [False, True], ids=["view off", "view on"])
def test_the_chat_assistant_is_offered_no_approval_tool_even_if_the_server_listed_one(view):
    listed_names = [
        "ingest_assessments", "resolve_issue", "compute_trend", "get_criterion", "draft_summary",
        "check_citations", "make_chart", "release_report", "class_baseline", "stage_gap_counts",
        *NAMES_FOR_APPROVAL,
    ]
    pg_names = load_settings({}).pg_tools
    listed = {tools.MDEIS: [spec(n) for n in listed_names], tools.PG: [spec(n) for n in pg_names]}
    definitions, owner, _ = tools.bind(listed, pg_names, view)
    offered = {d["function"]["name"] for d in definitions}
    assert not offered & set(NAMES_FOR_APPROVAL)
    assert not set(owner) & set(NAMES_FOR_APPROVAL)


def test_the_lesson_graph_can_call_only_its_own_tools():
    assert not GRAPH_TOOLS & set(NAMES_FOR_APPROVAL)
    assert not any("approve" in n or "reject" in n for n in GRAPH_TOOLS)


# --------------------------------------------------------------------------- the tool server
@pytest.fixture
def server_module(monkeypatch):
    pytest.importorskip("mcp")
    from mdeis import server

    yield server
    monkeypatch.delenv("CLASS_VIEW_ENABLED", raising=False)
    importlib.reload(server)


def reloaded(server, monkeypatch, on):
    if on:
        monkeypatch.setenv("CLASS_VIEW_ENABLED", "true")
    else:
        monkeypatch.delenv("CLASS_VIEW_ENABLED", raising=False)
    return importlib.reload(server)


@pytest.mark.parametrize("on", [False, True], ids=["view off", "view on"])
def test_the_mcp_tool_list_never_offers_a_tool_that_approves_or_rejects(
    server_module, monkeypatch, on
):
    server = reloaded(server_module, monkeypatch, on)
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert not [n for n in names if "approve" in n or "reject" in n], names
    assert not names & set(NAMES_FOR_APPROVAL)
    assert len(names) == (15 if on else 10), "the human routes are not tools"


def test_the_human_routes_are_added_to_the_server_only_with_the_class_view_on(
    server_module, monkeypatch
):
    def paths(on):
        server = reloaded(server_module, monkeypatch, on)
        return {getattr(r, "path", None) for r in server.mcp.streamable_http_app().routes}

    assert FIVE_ROUTES <= paths(True)
    assert not FIVE_ROUTES & paths(False)


def test_the_server_adds_the_routes_by_one_call_inside_the_class_view_block():
    tree = ast.parse((SRC / "mdeis" / "server.py").read_text("utf-8"))
    guarded = [
        node for node in tree.body
        if isinstance(node, ast.If) and "class_view.enabled" in ast.unparse(node.test)
    ]
    assert len(guarded) == 1
    calls = [ast.unparse(n) for n in ast.walk(guarded[0]) if isinstance(n, ast.Call)]
    assert "human_routes.install(mcp)" in calls
    everywhere = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert everywhere.count("human_routes.install(mcp)") == 1


def test_the_server_file_still_does_not_name_an_approval_function():
    server = (SRC / "mdeis" / "server.py").read_text("utf-8")
    assert "approve_lesson" not in server and "reject_lesson" not in server


# --------------------------------------------------------------------------- the module itself
def imported_modules(path):
    found = set()
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def test_the_human_routes_import_no_model_client_and_no_network_client():
    found = imported_modules(SRC / "mdeis" / "human_routes.py")
    for banned in ("openai", "anthropic", "langchain", "langgraph", "httpx", "requests", "urllib",
                   "socket", "orchestrator", "gateway", "portal"):
        assert banned not in found, banned


def test_the_human_routes_name_neither_a_model_nor_a_tool_call():
    text = (SRC / "mdeis" / "human_routes.py").read_text("utf-8")
    for word in ("OPENROUTER", "tool_calls", "complete(", "call_tool"):
        assert word not in text, word


def test_the_human_routes_never_read_a_person_or_a_school_from_the_request_body():
    """The only things read from the request are the two headers, the plan number in the path and
    the closed body. This pins the names of the headers; the behaviour is in the route tests."""
    from mdeis import human_routes

    assert human_routes.KEY_HEADER == HEADER.lower()
    assert human_routes.TOKEN_HEADER == "x-approver-token"
    assert human_routes.APPROVE_FIELDS == ("accepted_edit_ids",)
    assert human_routes.REJECT_FIELDS == ()
