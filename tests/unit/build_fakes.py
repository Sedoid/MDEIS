"""Fakes for the build-graph tests (T-071): a scripted model and a tool server that answers with
the real deterministic engine (mdeis.measure_build.engine) and keeps the versions it is asked to
store in a list instead of the registry. The sandbox is the real child, run in this process.

Nothing here touches a network or a database."""

import copy

from build_kit import RULES, SANDBOX, Counter, inputs
from inprocess_runner import Calls

from mdeis.measure_build import engine
from orchestrator.model import ModelError, Reply, ToolCall

EXTRACT, FUNCTION, REVIEW = "extract", "function", "review"


def task_of(messages) -> str:
    """Which job a model call is, from the first words of its system prompt."""
    first = messages[0]["content"].lstrip().lower()
    for task in (EXTRACT, FUNCTION, REVIEW):
        if first.startswith(f"task: {task}"):
            return task
    raise AssertionError(f"an unknown task: {first[:60]!r}")


def section_of(messages) -> str:
    for message in messages:
        for line in str(message["content"]).splitlines():
            if line.startswith("SECTION_ID: "):
                return line.removeprefix("SECTION_ID: ").strip()
    return ""


class ScriptedModel:
    """Answers by task and section. `script[(task, section)]` is a list of replies (a string, a
    Reply or an Exception); each call takes the next one and the last repeats. A call with no
    script gets `default[task]`. Keeps a copy of every call."""

    def __init__(self, script=None, default=None):
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.default = {EXTRACT: '{"rules": []}', **(default or {})}
        self.calls = []  # (task, section, messages, definitions)

    async def complete(self, messages, definitions):
        task, section = task_of(messages), section_of(messages)
        self.calls.append((task, section, copy.deepcopy(messages), copy.deepcopy(definitions)))
        queue = self.script.get((task, section))
        item = (queue.pop(0) if len(queue) > 1 else queue[0]) if queue else self.default.get(task)
        if item is None:
            raise AssertionError(f"no script for {task} of section {section}")
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, Reply) else Reply(item)

    def tasks(self, task):
        return [c for c in self.calls if c[0] == task]


class PureServer:
    """The six build tools, answered by the real engine, with an in-memory store."""

    def __init__(self, content=None, issues=(), first=True, version_name="baobab-v1"):
        self.inputs = inputs(content, issues=issues, first=first, version_name=version_name)
        self.runner = Calls()
        self.counter = Counter()
        self.calls = []
        self.versions = []
        self.reviews = []

    async def call(self, name, arguments):
        self.calls.append((name, copy.deepcopy(arguments)))
        return getattr(self, name)(**arguments)

    def build_plan(self, document_id, build_anyway=None):
        return engine.plan(self.inputs, RULES, build_anyway or [], SANDBOX)

    def build_settle_rules(self, document_id, section_id, rules):
        return engine.settle_rules(self.inputs, RULES, section_id, rules, self.counter)

    def build_check_function(self, document_id, section_id, settled, proposal):
        return engine.check_function(
            self.inputs, RULES, SANDBOX, section_id, settled, proposal, self.runner
        )

    def build_store_version(self, document_id, section_id, settled, proposal):
        report = self.build_check_function(document_id, section_id, settled, proposal)
        if not report["passed"]:
            return {"status": "refused", "reason": "the checks did not pass",
                    "problems": report["problems"]}  # fmt: skip
        version = {
            "measure_id": f"MS-{len(self.versions) + 1:04d}", "version": 1,
            "status": "awaiting_review", "section_id": section_id, "source": proposal["source"],
            "tests": report["tests"], "patch": report["patch"], "clause_ids": report["clause_ids"],
            "name": proposal["measure_name"], "reads": settled["reads"],
        }  # fmt: skip
        self.versions.append(version)
        return {
            "status": "stored", "measure_id": version["measure_id"], "version": 1,
            "version_status": "awaiting_review",
            "patch_id": "TP-000001" if report["patch"] else None,
            "tests": len(report["tests"]), "clause_ids": report["clause_ids"],
        }  # fmt: skip

    def build_record_review(self, measure_id, version, result, reasons=None):
        found = next(v for v in self.versions if v["measure_id"] == measure_id)
        self.reviews.append((measure_id, result, list(reasons or [])))
        found["review"] = {"result": result, "reasons": list(reasons or [])}
        if result == "pass":
            found["status"] = "awaiting_demo"
        return {"status": "ok", "measure_id": measure_id, "version": version,
                "review_result": result, "version_status": found["status"]}  # fmt: skip

    def build_retire_removed(self, document_id, section_ids):
        return {"status": "ok", "retired": []}


class LocalServer:
    """The six build tools of the real server side (`mdeis.measure_build.api`), called in this
    process on an open connection, as the orchestrator would call them over MCP. No approver."""

    def __init__(self, conn, url):
        from mdeis.measure_build import api
        from mdeis.tools.context import ToolContext

        self.api, self.calls = api, []
        self.ctx = ToolContext(conn=conn, run_id="RUN-BUILD", approver=None, database_url=url,
                               commit=False)  # fmt: skip

    async def call(self, name, arguments):
        self.calls.append((name, copy.deepcopy(arguments)))
        return self.api.TOOLS[name](self.ctx, **arguments)


def tool_names(server):
    return [name for name, _ in server.calls]


__all__ = ["LocalServer", "ModelError", "PureServer", "ScriptedModel", "ToolCall", "tool_names"]
