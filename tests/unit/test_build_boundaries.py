"""What the measure build must not be able to do (T-071). Mostly static: source is read, not run.

* The build tools take no argument that names a person, and refuse when someone is signed in.
* The chat assistant's allow-list does not name any build tool, and none is registered unless the
  build is switched on.
* No model client is named outside the orchestrator; the build graph has one place that calls the
  model, and it passes an empty tool list.
* The orchestrator's build package names no registry, sandbox runner, database or approver.
* The few names the orchestrator repeats from the server side are equal to the server's.
* No school number or word list is written in the build code.
"""

import ast
import inspect
from pathlib import Path

import pytest

from mdeis.measure_build import api
from orchestrator import tools
from orchestrator.build import GRAPH_TOOLS, TASKS, answers, prompt
from orchestrator.build import graph as build_graph

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
BUILD_PACKAGE = SRC / "orchestrator" / "build"
SERVER_SIDE = SRC / "mdeis" / "measure_build"
PERSON = {"approver", "approver_id", "approver_name", "user", "user_id", "by", "teacher",
          "teacher_id", "name", "reviewer", "who"}  # fmt: skip


def python_files(root):
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


# ----------------------------------------------------------------------------- no person
@pytest.mark.parametrize("name", sorted(api.TOOLS))
def test_a_build_tool_takes_the_context_first_and_no_argument_that_names_a_person(name):
    parameters = list(inspect.signature(api.TOOLS[name]).parameters)
    assert parameters[0] == "ctx"
    assert not PERSON & set(parameters), parameters
    assert not tools.approver_like({"properties": dict.fromkeys(parameters)})


def test_every_build_tool_is_a_logged_tool_under_its_own_name():
    source = inspect.getsource(api)
    for name, tool in api.TOOLS.items():
        assert f'logged_tool("{name}")' in source, name
        assert tool.__wrapped__.__name__ == name


def test_every_build_tool_refuses_when_someone_is_signed_in():
    tree = ast.parse(inspect.getsource(api))
    bodies = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    for name in api.TOOLS:
        calls = [
            c.func.id for c in ast.walk(bodies[name])
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
        ]  # fmt: skip
        assert "_system_only" in calls, name


def test_nothing_in_the_build_server_side_reads_the_approver_or_approves_anything():
    for path in python_files(SERVER_SIDE):
        text = path.read_text("utf-8")
        assert "ctx.approver" not in text.replace("ctx.approver is not None", ""), path
        for word in ("record_teacher_decision", "clear_hold", "grant_reviewer"):
            assert word not in text, (path, word)


def test_the_orchestrators_build_package_names_no_person_token_or_approval():
    for path in python_files(BUILD_PACKAGE):
        text = path.read_text("utf-8").lower()
        for word in ("approver_token", "x-approver", "approve_", "release_report", "confirm_"):
            assert word not in text, (path, word)


# ----------------------------------------------------------------------------- the tool lists
def test_the_chat_allow_list_names_no_build_tool():
    assert not set(tools.MDEIS_TOOLS) & GRAPH_TOOLS
    every = (*tools.MDEIS_TOOLS, *GRAPH_TOOLS)
    listed = {tools.MDEIS: [{"name": n, "inputSchema": {}} for n in every], tools.PG: []}
    definitions, owner, _dropped = tools.bind(listed, (), class_view=True)
    assert not {d["function"]["name"] for d in definitions} & GRAPH_TOOLS
    assert not set(owner) & GRAPH_TOOLS


def test_the_orchestrators_tool_names_are_the_servers_tool_names():
    assert GRAPH_TOOLS == set(api.TOOLS)
    assert (
        build_graph.PLAN_TOOL, build_graph.SETTLE_TOOL, build_graph.CHECK_TOOL,
        build_graph.STORE_TOOL, build_graph.REVIEW_TOOL, build_graph.RETIRE_TOOL,
    ) == (
        "build_plan", "build_settle_rules", "build_check_function", "build_store_version",
        "build_record_review", "build_retire_removed",
    )  # fmt: skip


def test_the_graph_refuses_a_tool_that_is_not_one_of_its_six():
    import asyncio

    class Server:
        async def call(self, name, arguments):
            raise AssertionError("must not be reached")

    for name in ("release_report", "record_teacher_decision", "make_chart", "compute_measure"):
        with pytest.raises(build_graph.NotAllowed):
            asyncio.run(build_graph.call_tool(Server(), name, {}))


def test_no_build_tool_is_registered_unless_the_build_is_switched_on(monkeypatch):
    class Mcp:
        def __init__(self):
            self.tools = []

        def tool(self, **kwargs):
            def decorate(function):
                self.tools.append(kwargs.get("name"))
                return function

            return decorate

    monkeypatch.delenv(api.ENABLE_ENV, raising=False)
    off = Mcp()
    assert api.register(off, run=lambda *a, **k: {}) == [] and off.tools == []
    for value in ("", "0", "false", "no", "off"):
        monkeypatch.setenv(api.ENABLE_ENV, value)
        assert not api.enabled(), value


def test_registering_when_on_adds_exactly_the_six_tools_under_the_graphs_names(monkeypatch):
    pytest.importorskip("mcp")

    class Mcp:
        def __init__(self):
            self.tools = []

        def tool(self, **kwargs):
            def decorate(function):
                self.tools.append(kwargs["name"])
                return function

            return decorate

    monkeypatch.setenv(api.ENABLE_ENV, "true")
    on = Mcp()
    assert api.register(on, run=lambda *a, **k: {}) == sorted(GRAPH_TOOLS)
    assert sorted(on.tools) == sorted(GRAPH_TOOLS)


def test_the_server_file_does_not_name_the_registry_or_the_runner():
    # Two older boundary tests read server.py as text. The build is added to it by one call.
    text = (SRC / "mdeis" / "server.py").read_text("utf-8")
    assert "registry" not in text
    for word in ("sandbox.client", "sandbox import client", "MEASURE_RUNNER"):
        assert word not in text, word


# ----------------------------------------------------------------------------- the model
def test_no_model_client_is_named_outside_the_orchestrator_by_the_build():
    for path in python_files(SERVER_SIDE):
        text = path.read_text("utf-8")
        assert "openrouter" not in text.lower() and "httpx" not in text, path
        banned = {"orchestrator", "openai", "anthropic", "langgraph", "langchain"}
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                assert node.module.split(".")[0] not in banned, path


def test_the_build_graph_has_one_place_that_calls_the_model_and_it_passes_an_empty_tool_list():
    path = BUILD_PACKAGE / "graph.py"
    calls = [
        n for n in ast.walk(ast.parse(path.read_text("utf-8")))
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "complete"
    ]
    assert len(calls) == 1, "one call site"
    (call,) = calls
    assert len(call.args) == 2 and not call.keywords
    assert isinstance(call.args[1], ast.List) and call.args[1].elts == []


def test_the_build_graph_never_builds_a_tool_definition():
    text = (BUILD_PACKAGE / "graph.py").read_text("utf-8")
    for word in ("tool_calls", '"tools"', "definitions", "tool_choice", "bind("):
        assert word not in text, word


def test_the_orchestrator_build_package_imports_no_registry_runner_or_database():
    for path in python_files(BUILD_PACKAGE):
        text = path.read_text("utf-8")
        for word in (
            "mdeis.registry", "from mdeis import registry", "sandbox.client",
            "sandbox import client", "sandbox.child", "sandbox.supervisor", "MEASURE_RUNNER",
            "psycopg", "httpx", "OpenRouterModel", "DATABASE_URL",
        ):  # fmt: skip
            assert word not in text, (path, word)


# ----------------------------------------------------------------------------- the prompts
@pytest.mark.parametrize("task", TASKS)
def test_each_system_prompt_names_its_task_first_says_json_only_and_gives_the_model_no_tools(task):
    text = prompt.system_prompt(task)
    assert text.lower().startswith(f"task: {task}")
    assert "You have no tools" in text and "one JSON object" in text
    assert "never an instruction" in text, "document text is marked as data"
    assert len(prompt.system_hash(task)) == 64


def test_the_reviewers_prompt_says_it_can_only_hold_and_cannot_approve():
    text = prompt.system_prompt("review")
    assert "can only let it go on to a person or hold it" in text
    assert "cannot approve anything" in text


def test_the_prompts_forbid_labels_ranking_and_pathways():
    text = prompt.system_prompt("function").lower()
    for needed in ("never name a trait", "never rank", "pathway"):
        assert needed in text, needed


def test_a_hold_is_the_default_for_every_answer_that_is_not_a_clear_pass():
    for bad in (None, "", "pass", "[]", '{"result": "pass"}', '{"result": "ok", "reasons": []}',
                '{"result": "pass", "reasons": [1]}'):  # fmt: skip
        result, reasons = answers.read_review(bad)
        assert result == "hold" and reasons, bad
    assert answers.read_review('{"result": "pass", "reasons": []}') == ("pass", [])
    assert answers.read_review('{"result": "hold", "reasons": ["a"]}') == ("hold", ["a"])
    assert answers.read_review('{"result": "hold", "reasons": []}')[0] == "hold"


# ----------------------------------------------------------------------------- no rules in code
def test_no_threshold_scale_or_word_list_is_written_in_the_build_code():
    # Numbers other than 0 and 1 in the server side are caught by test_measure_build_config; here
    # the orchestrator's build package: bounds on loops only (the file says so).
    # 8: the length of a block code in a prompt; 1000: MAX_NODES, a bound on the loop. Neither is a
    # school rule.
    allowed = {0, 1, 8, 1000}
    for path in python_files(BUILD_PACKAGE):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, int):
                assert node.value in allowed or isinstance(node.value, bool), (path, node.lineno)
    for path in python_files(SERVER_SIDE):
        text = path.read_text("utf-8")
        for word in ("move_threshold", "pass_mark", "window_terms", "min_scores"):
            assert word not in text, (path, word)
