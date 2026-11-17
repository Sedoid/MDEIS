"""Who can reach the teacher demo and what it may touch (T-073). Mostly static: the source is read.

* A confirmation and a rejection are human routes, not tools. The chat assistant's allow-list does
  not change, the MCP tool list never offers any of them, and the model path has no name for them.
* The routes are added to the server by one call, outside the class-view block (the demo does not
  depend on the class view), and the server file names neither the registry nor the sandbox.
* The demo changes the registry only through the two registry functions that are meant for a person
  at the demo (`record_tests` for a teacher case, `record_teacher_decision`). It cannot move a
  version along, review it, clear a hold, retire it or create one.
* The one switch that lets a value no teacher has confirmed be used in a run (`unconfirmed_ok`) is
  set in the demo and the build and nowhere a user's run is made.
* The demo imports no model client and no network client.
"""

import ast
import asyncio
import importlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
DEMO = SRC / "mdeis" / "measure_demo"
ROUTES_FILE = SRC / "mdeis" / "measure_routes.py"
NAMES = (
    "list_awaiting", "open_demo", "try_measure", "save_case", "confirm", "reject",
    "measure_demo_list", "measure_demo_open", "measure_demo_try", "measure_demo_case",
    "measure_demo_confirm", "measure_demo_reject", "confirm_measure", "reject_measure",
    "approve_measure", "record_teacher_decision",
)
SIX_ROUTES = {
    "/human/measures", "/human/measures/{measure_id}/{version}",
    "/human/measures/{measure_id}/{version}/try", "/human/measures/{measure_id}/{version}/case",
    "/human/measures/{measure_id}/{version}/confirm",
    "/human/measures/{measure_id}/{version}/reject",
}


def python_files(*parts):
    root = SRC.joinpath(*parts)
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def imported(path):
    found = set()
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def demo_files():
    return [*sorted(DEMO.glob("*.py")), ROUTES_FILE]


# --------------------------------------------------------------------------- not a model tool
def test_the_demo_package_and_the_routes_file_exist():
    assert (DEMO / "__init__.py").exists() and (DEMO / "compose.py").exists()
    assert (DEMO / "api.py").exists() and ROUTES_FILE.exists()


def test_the_chat_allow_list_is_exactly_what_it_was_before_the_demo():
    from orchestrator import tools

    assert set(tools.MDEIS_TOOLS) == {
        "ingest_assessments", "resolve_issue", "compute_trend", "get_criterion", "draft_summary",
        "check_citations", "make_chart", "release_report", "list_measures", "compute_measure",
    }
    assert len(tools.MDEIS_TOOLS) == 10
    for name in tools.MDEIS_TOOLS:
        assert "demo" not in name and "confirm" not in name and "reject" not in name


def test_the_model_path_has_no_name_for_a_demo_function_or_a_demo_route():
    for place in ("orchestrator", "gateway"):
        for path in python_files(place):
            text = path.read_text("utf-8")
            for name in NAMES:
                if name in ("confirm", "reject"):
                    continue  # plain words that other code uses; the longer names are the test
                assert name not in text, (path, name)
            assert "measure_demo" not in text and "measure_routes" not in text, path
            assert "/human/measures" not in text or place == "gateway", path


def test_the_orchestrator_names_no_route_to_the_tool_servers_human_door():
    for path in python_files("orchestrator"):
        text = path.read_text("utf-8")
        assert "/human/" not in text and "HUMAN_ROUTE_KEY" not in text, path


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
def test_the_mcp_tool_list_offers_no_demo_confirm_or_reject_tool(server_module, monkeypatch, on):
    server = reloaded(server_module, monkeypatch, on)
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert not names & set(NAMES), names & set(NAMES)
    assert not [n for n in names if "demo" in n or "confirm" in n or "reject" in n], names


@pytest.mark.parametrize("on", [False, True], ids=["view off", "view on"])
def test_the_six_routes_are_on_the_server_with_the_class_view_on_or_off(
    server_module, monkeypatch, on
):
    server = reloaded(server_module, monkeypatch, on)
    found = {getattr(r, "path", None) for r in server.mcp.streamable_http_app().routes}
    assert SIX_ROUTES <= found


def test_the_server_adds_the_routes_by_one_call_outside_the_class_view_block():
    tree = ast.parse((SRC / "mdeis" / "server.py").read_text("utf-8"))
    everywhere = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert everywhere.count("measure_routes.install(mcp)") == 1
    for node in tree.body:
        if isinstance(node, ast.If) and "class_view.enabled" in ast.unparse(node.test):
            inside = [ast.unparse(n) for n in ast.walk(node) if isinstance(n, ast.Call)]
            assert "measure_routes.install(mcp)" not in inside


def test_the_server_file_names_neither_the_registry_nor_the_sandbox_nor_the_demo_store():
    text = (SRC / "mdeis" / "server.py").read_text("utf-8")
    for word in ("registry", "sandbox.client", "MEASURE_RUNNER", "measure_demo", "unconfirmed_ok"):
        assert word not in text, word


# ---------------------------------------------------------------------- what the demo may touch
def test_the_demo_changes_the_registry_only_through_a_teacher_case_and_the_teachers_decision():
    reads = set()
    for path in demo_files():
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "registry" in node.module:
                reads |= {a.name for a in node.names}
    assert reads <= {"record_tests", "record_teacher_decision", "transitions"}, reads


def test_the_demo_writes_no_sql_of_its_own_to_the_registry_or_the_patches():
    for path in demo_files():
        for text in (s.value for s in ast.walk(ast.parse(path.read_text("utf-8")))
                     if isinstance(s, ast.Constant) and isinstance(s.value, str)):
            assert not re.match(r"\s*(INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER)\b", text), (
                path.name, text[:80])


def test_the_demo_never_advances_reviews_clears_retires_or_creates():
    for path in demo_files():
        text = path.read_text("utf-8")
        for word in ("advance_status", "record_review", "clear_hold", "create_version", "retire(",
                     "template_patches SET", "apply_patch", "build_api", "build_record_review"):
            assert word not in text, (path.name, word)


@pytest.mark.parametrize("place", [("tools",), ("orchestrator",), ("gateway",), ("portal",)])
def test_the_switch_for_an_unconfirmed_value_is_not_set_where_a_users_run_is_made(place):
    for path in python_files(*place):
        assert "unconfirmed_ok" not in path.read_text("utf-8"), path
    assert "unconfirmed_ok" not in (SRC / "mdeis" / "server.py").read_text("utf-8")


def test_the_run_a_user_asks_for_never_sets_the_switch_and_the_default_is_off():
    import inspect

    from mdeis.sandbox import with_template

    parameter = inspect.signature(with_template.run).parameters["unconfirmed_ok"]
    assert parameter.default is False and parameter.kind is inspect.Parameter.KEYWORD_ONLY
    users = (SRC / "mdeis" / "tools" / "measures.py").read_text("utf-8")
    assert "with_template.run(" in users and "unconfirmed_ok" not in users


# --------------------------------------------------------------------------- imports
@pytest.mark.parametrize("path", demo_files(), ids=lambda p: p.name)
def test_the_demo_imports_no_model_client_and_no_network_client(path):
    found = imported(path)
    for banned in ("openai", "anthropic", "langchain", "langgraph", "httpx", "requests", "urllib",
                   "socket", "orchestrator", "gateway", "portal"):
        assert banned not in found, banned


@pytest.mark.parametrize("path", demo_files(), ids=lambda p: p.name)
def test_the_demo_names_no_model_call_and_no_runner_internals(path):
    text = path.read_text("utf-8")
    for word in ("OPENROUTER", "tool_calls", "complete(", "call_tool", "sandbox.client",
                 "sandbox import client", "sandbox.supervisor", "sandbox.child",
                 "MEASURE_RUNNER"):
        assert word not in text, word


def test_the_demo_reaches_the_sandbox_only_through_the_call_path_that_builds_the_values():
    text = (DEMO / "api.py").read_text("utf-8")
    assert "with_template" in text and "with_template.run(" in text


def test_the_only_modules_outside_the_demo_that_import_it_are_the_routes_and_tests():
    holders = set()
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts or DEMO in path.parents:
            continue
        text = path.read_text("utf-8")
        if re.search(r"(?m)^\s*(from|import)\s[^\n]*measure_demo", text):
            holders.add(str(path.relative_to(SRC)))
    assert holders == {"mdeis/measure_routes.py"}


def test_the_routes_reuse_the_lesson_routes_gate_and_do_not_copy_the_key_check():
    text = ROUTES_FILE.read_text("utf-8")
    assert "human_routes" in text and "_gate" in text
    assert "compare_digest" not in text, "the key is compared in one place only"
    assert "approver_token.verify" not in text, "the token is verified in one place only"
