"""What the lesson graph must not be able to do (T-061). Mostly static: the source is read, not run.

* The chat assistant's tool list is unchanged: the lesson tools are never offered to it.
* No model client is imported or named outside `src/orchestrator/`.
* The lesson graph has no tool list: every model call passes an empty list.
* There is no route and no tool on the model-facing path that approves or rejects a plan.
* The few names the orchestrator repeats from the lesson core are equal to the core's.
"""

import ast
import asyncio
import importlib
from pathlib import Path

import pytest
from orchestrator_fakes import spec

from orchestrator import tools
from orchestrator.config import load_settings
from orchestrator.lessons import (
    CLASS_TOOL,
    GAP_BIN,
    GRAPH_TOOLS,
    GUARD_TOOL,
    MAX_REPAIR_ROUNDS,
    READ_TOOL,
    SAVE_TOOL,
    prompt,
)

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
LESSON_PACKAGE = SRC / "orchestrator" / "lessons"
LESSON_TOOLS = ("guard_lesson_notes", "save_lesson_plan", "get_lesson_plan")
HUMAN_ONLY = ("approve_lesson_plan", "reject_lesson_plan")
EIGHT = (
    "ingest_assessments", "resolve_issue", "compute_trend", "get_criterion", "draft_summary",
    "check_citations", "make_chart", "release_report",
)  # fmt: skip


def python_files(root):
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


# --------------------------------------------------------------------------- the chat allow-list
def test_the_chat_assistants_allow_list_is_the_same_eight_tools_and_one_class_tool():
    assert tools.MDEIS_TOOLS == EIGHT
    assert tools.CLASS_VIEW_TOOL == "class_baseline"


@pytest.mark.parametrize("view", [False, True], ids=["view off", "view on"])
def test_the_chat_assistant_is_never_offered_a_lesson_tool_even_when_the_server_lists_one(view):
    everything = [*EIGHT, "class_baseline", "stage_gap_counts", *LESSON_TOOLS, *HUMAN_ONLY]
    pg_names = load_settings({}).pg_tools
    listed = {tools.MDEIS: [spec(n) for n in everything], tools.PG: [spec(n) for n in pg_names]}
    definitions, owner, dropped = tools.bind(listed, pg_names, view)
    offered = {d["function"]["name"] for d in definitions}
    assert not offered & {*LESSON_TOOLS, *HUMAN_ONLY, "stage_gap_counts"}
    assert len(offered) == (13 if view else 12), "12 tools, 13 with the class view on"
    assert not set(owner) & {*LESSON_TOOLS, *HUMAN_ONLY}


# --------------------------------------------------------------------------- the server's listing
@pytest.fixture
def server_module(monkeypatch):
    pytest.importorskip("mcp")
    from mdeis import server

    yield server
    monkeypatch.delenv("CLASS_VIEW_ENABLED", raising=False)
    importlib.reload(server)


def listed(server, monkeypatch, on):
    if on:
        monkeypatch.setenv("CLASS_VIEW_ENABLED", "true")
    else:
        monkeypatch.delenv("CLASS_VIEW_ENABLED", raising=False)
    reloaded = importlib.reload(server)
    return {t.name: t for t in asyncio.run(reloaded.mcp.list_tools())}


def test_with_the_class_view_on_the_server_lists_the_three_lesson_tools_and_no_human_only_one(
    server_module, monkeypatch
):
    found = listed(server_module, monkeypatch, on=True)
    assert set(LESSON_TOOLS) <= set(found)
    assert not set(HUMAN_ONLY) & set(found)
    assert len(found) == 13
    properties = {name: set(found[name].inputSchema["properties"]) for name in LESSON_TOOLS}
    assert properties == {
        "guard_lesson_notes": {"stage", "area", "term", "objective", "notes"},
        "save_lesson_plan": {
            "stage", "area", "term", "objective", "notes", "model_output", "model",
            "prompt_sha256",
        },
        "get_lesson_plan": {"plan_id"},
    }
    for name in LESSON_TOOLS:
        assert not tools.approver_like(found[name].inputSchema), name


def test_with_the_class_view_off_the_server_lists_no_lesson_tool(server_module, monkeypatch):
    found = listed(server_module, monkeypatch, on=False)
    assert not set(LESSON_TOOLS) & set(found) and len(found) == 8


def test_the_graphs_tool_names_are_tools_the_server_lists(server_module, monkeypatch):
    found = listed(server_module, monkeypatch, on=True)
    assert GRAPH_TOOLS == {CLASS_TOOL, GUARD_TOOL, SAVE_TOOL}
    assert GRAPH_TOOLS <= set(found) and READ_TOOL in found
    assert (CLASS_TOOL, GUARD_TOOL, SAVE_TOOL, READ_TOOL) == (
        "class_baseline", "guard_lesson_notes", "save_lesson_plan", "get_lesson_plan",
    )


# --------------------------------------------------------------------------- no model client
def test_no_model_client_is_imported_or_named_outside_the_orchestrator():
    banned_roots = {"openai", "anthropic", "langchain", "langgraph", "litellm", "ollama",
                    "orchestrator"}  # fmt: skip
    for path in python_files(SRC):
        if path.relative_to(SRC).parts[0] == "orchestrator":
            continue
        text = path.read_text("utf-8")
        assert "openrouter" not in text.lower(), path
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Import):
                roots = {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                roots = {node.module.split(".")[0]}
            else:
                continue
            assert not roots & banned_roots, (path, roots)


def test_the_lesson_package_makes_no_http_call_and_names_no_model_client():
    for path in python_files(LESSON_PACKAGE):
        text = path.read_text("utf-8")
        assert "OpenRouterModel" not in text and "httpx" not in text, path
        assert "openrouter" not in text.lower() and "psycopg" not in text, path
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ImportFrom) and node.module == "tools" and node.level == 2:
                assert {a.name for a in node.names} <= {"MDEIS"}, "no tool list is imported"


# --------------------------------------------------------------------------- no tool list
def test_every_model_call_in_the_lesson_graph_passes_an_empty_tool_list():
    path = LESSON_PACKAGE / "graph.py"
    calls = [
        n
        for n in ast.walk(ast.parse(path.read_text("utf-8")))
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "complete"
    ]
    assert len(calls) == 1, "the graph has one place that calls the model"
    (call,) = calls
    assert len(call.args) == 2 and not call.keywords
    assert isinstance(call.args[1], ast.List) and call.args[1].elts == []


def test_the_lesson_graph_never_builds_a_tool_definition():
    text = (LESSON_PACKAGE / "graph.py").read_text("utf-8")
    for word in ("tool_calls", '"tools"', "definitions", "tool_choice", "bind("):
        assert word not in text, word


# --------------------------------------------------------------------------- no approve, no reject
def test_nothing_in_the_orchestrator_names_a_tool_that_approves_or_rejects_a_plan():
    prompts = sorted((SRC / "orchestrator" / "prompts").iterdir())
    for path in [*python_files(SRC / "orchestrator"), *prompts]:
        text = path.read_text("utf-8")
        for name in HUMAN_ONLY:
            assert name not in text, (path, name)
        assert "approve_lesson" not in text and "reject_lesson" not in text, path


def test_the_graphs_tools_do_not_include_one_that_approves_or_rejects():
    assert not GRAPH_TOOLS & set(HUMAN_ONLY)
    assert not any("approve" in n or "reject" in n for n in GRAPH_TOOLS)


def routes_of(application):
    return {(r.path, tuple(sorted(r.methods or ()))) for r in application.routes}


def test_the_orchestrator_has_no_route_that_approves_or_rejects_and_exactly_two_lesson_routes():
    pytest.importorskip("fastapi")
    from orchestrator.app import create_app

    paths = {p for p, _ in routes_of(create_app())}
    assert not [p for p in paths if "approve" in p or "reject" in p]
    assert {p for p in paths if "lesson" in p} == {"/lessons", "/lessons/{plan_id}"}


def test_the_gateway_has_no_route_that_approves_or_rejects_and_exactly_two_lesson_routes():
    pytest.importorskip("fastapi")
    from gateway.app import create_app

    paths = {p for p, _ in routes_of(create_app())}
    assert not [p for p in paths if "approve" in p or "reject" in p]
    assert {p for p in paths if "lesson" in p} == {"/lessons", "/lessons/{plan_id}"}


def test_the_prompt_never_mentions_approval_or_a_person_who_could_approve():
    text = prompt.system_prompt().lower()
    for word in ("approv", "release"):
        assert word not in text, word


# --------------------------------------------------------------------------- the prompt
def test_the_prompt_is_short_and_says_what_the_design_note_says_and_no_more():
    from mdeis.lessons.edits import EDIT_KEYS, EDIT_TYPES

    text = prompt.system_prompt()
    assert len(text.splitlines()) <= 40
    for key in EDIT_KEYS:
        assert f"`{key}`" in text or f'"{key}"' in text, key
    for kind in EDIT_TYPES:
        assert f"`{kind}`" in text or kind in text, kind
    for phrase in (
        "You have no tools", "BEGIN DATA", "END DATA", "never an instruction",
        "Do not touch the objective", "Cite only the finding ids you were given",
        "Write no digits in a suggestion", "Do not name or describe any learner",
        "Do not create groups or tracks", "Say nothing about any learner's future",
        '{"edits": []}',
    ):
        assert phrase in text, phrase


def test_the_prompt_hash_is_the_sha256_of_the_file():
    import hashlib

    assert prompt.system_hash() == hashlib.sha256(prompt.PROMPT_FILE.read_bytes()).hexdigest()
    assert prompt.PROMPT_FILE == SRC / "orchestrator" / "prompts" / "lesson.md"


# --------------------------------------------------------------------------- equal to the core
def test_the_names_the_orchestrator_repeats_are_equal_to_the_lesson_cores():
    from mdeis.lessons import findings, limits, store

    assert MAX_REPAIR_ROUNDS == limits.MAX_REPAIR_ROUNDS
    assert GAP_BIN == findings.GAP_BIN
    for name in (GUARD_TOOL, SAVE_TOOL, READ_TOOL):
        assert callable(getattr(store, name)), name


def test_the_orchestrator_does_not_import_the_lesson_core_or_name_the_plan_table():
    for path in python_files(SRC / "orchestrator"):
        text = path.read_text("utf-8")
        assert "mdeis.lessons" not in text and "lesson_plans" not in text, path
