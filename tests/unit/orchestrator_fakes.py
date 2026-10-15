"""Fakes for the orchestrator tests: a scripted model and tool servers that record their calls."""

import copy
import json

from orchestrator import tools
from orchestrator.model import Reply, ToolCall


def spec(name, properties=None):
    return {"name": name, "description": name, "inputSchema": {"properties": properties or {}}}


MDEIS_LISTING = [
    spec(n) for n in (*tools.MDEIS_TOOLS, "record_verification", "check_annotation")
]
PG_LISTING = [spec("execute_sql"), spec("list_schemas"), spec("analyze_workload_indexes")]


class FakeServer:
    """A tool server that records the calls it gets and answers from a table.

    A table entry that is a list is a sequence: each call to that tool gets the next answer, and the
    last answer repeats.
    """

    def __init__(self, listing, results=None):
        self.listing, self.results, self.calls = listing, results or {}, []

    async def list_tools(self):
        return self.listing

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        answer = self.results.get(name, {"status": "ok"})
        if isinstance(answer, list):
            return answer.pop(0) if len(answer) > 1 else answer[0]
        return answer


class FakeModel:
    """A model that answers from a script and keeps a copy of everything it was shown."""

    def __init__(self, *script):
        self.script, self.seen = list(script), []

    async def complete(self, messages, definitions):
        self.seen.append((copy.deepcopy(messages), copy.deepcopy(definitions)))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ask(name, arguments=None, id="c1"):
    return Reply("", (ToolCall(id, name, json.dumps(arguments or {})),))


def say(text):
    return Reply(text)
