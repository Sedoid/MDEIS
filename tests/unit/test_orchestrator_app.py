"""The orchestrator's HTTP side: the shared key, the event stream, and the headers each run sends.

A fake model and fake tool servers stand in for the real ones (no network, no key). The service is
built with `create_app`, which takes them as arguments.
"""

import json
from contextlib import asynccontextmanager

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from orchestrator import app as service  # noqa: E402
from orchestrator import tools  # noqa: E402
from orchestrator.config import load_settings  # noqa: E402
from orchestrator.model import Reply  # noqa: E402

KEY = "test-key-not-a-real-one"
TOKEN = "signed.token"


class Answers:
    """A model that always answers with the same words and never asks for a tool."""

    def __init__(self, settings):
        self.settings = settings

    async def complete(self, messages, definitions):
        return Reply("The records show a steady trend [A-000001].")


class Server:
    async def list_tools(self):
        return []

    async def call(self, name, arguments):
        return {}


def make_client(monkeypatch, started, configured=True):
    monkeypatch.setenv("ORCHESTRATOR_API_KEY", KEY)
    if configured:
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
        monkeypatch.setenv("OPENROUTER_MODEL", "open/weights-model")
    else:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_MODEL", raising=False)

    @asynccontextmanager
    async def servers(settings, token, run_id):
        started.append({"token": token, "run_id": run_id})
        yield {tools.MDEIS: Server()}

    return TestClient(service.create_app(model_factory=Answers, servers_factory=servers))


BODY = {
    "conversation_id": "c-1",
    "messages": [{"role": "user", "content": "How is Amina doing in English?"}],
    "approver_token": TOKEN,
}


def events(response):
    """The server-sent events of a response, as (name, data) pairs in order."""
    out = []
    for block in response.text.strip().split("\n\n"):
        name, data = block.split("\n")
        out.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


def test_a_request_without_the_key_gets_401_and_starts_no_run(monkeypatch):
    started = []
    client = make_client(monkeypatch, started)
    assert client.post("/run", json=BODY).status_code == 401
    wrong = client.post("/run", json=BODY, headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401
    assert started == [], "no tool server was opened, so no run started"


def test_a_service_with_no_key_set_refuses_everything(monkeypatch):
    started = []
    client = make_client(monkeypatch, started)
    monkeypatch.setenv("ORCHESTRATOR_API_KEY", "")
    assert client.post("/run", json=BODY, headers={"Authorization": "Bearer "}).status_code == 401


def test_a_run_streams_status_then_tokens_then_the_final_answer(monkeypatch):
    started = []
    client = make_client(monkeypatch, started)
    response = client.post("/run", json=BODY, headers={"Authorization": f"Bearer {KEY}"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    names = [n for n, _ in events(response)]
    assert names[0] == "status" and names[-1] == "final"
    assert "token" in names and names.index("token") > names.index("status")
    final = events(response)[-1][1]
    assert final["answer"] == "The records show a steady trend [A-000001]."
    assert final["run_id"] == started[0]["run_id"]


def test_every_run_gets_its_own_run_number_and_the_token_is_passed_on(monkeypatch):
    started = []
    client = make_client(monkeypatch, started)
    headers = {"Authorization": f"Bearer {KEY}"}
    client.post("/run", json=BODY, headers=headers)
    client.post("/run", json=BODY, headers=headers)
    first, second = started
    assert first["run_id"].startswith("RUN-") and first["run_id"] != second["run_id"]
    assert first["token"] == TOKEN, "handed to the tool-server client for the header"


def test_a_missing_token_is_allowed_the_server_refuses_anything_that_needs_it(monkeypatch):
    started = []
    client = make_client(monkeypatch, started)
    body = {k: v for k, v in BODY.items() if k != "approver_token"}
    response = client.post("/run", json=body, headers={"Authorization": f"Bearer {KEY}"})
    assert response.status_code == 200 and started[0]["token"] is None


def test_a_service_without_a_model_says_so_in_plain_words(monkeypatch):
    started = []
    client = make_client(monkeypatch, started, configured=False)
    response = client.post("/run", json=BODY, headers={"Authorization": f"Bearer {KEY}"})
    [(name, data)] = events(response)
    assert name == "error" and "OPENROUTER_MODEL" in data["text"]
    assert started == []


@pytest.mark.parametrize(
    "body",
    [
        {**BODY, "messages": []},
        {**BODY, "conversation_id": ""},
        {**BODY, "messages": [{"role": "user", "content": "x" * 20_001}]},
    ],
)
def test_a_badly_formed_request_is_refused_before_a_run_starts(monkeypatch, body):
    started = []
    client = make_client(monkeypatch, started)
    response = client.post("/run", json=body, headers={"Authorization": f"Bearer {KEY}"})
    assert response.status_code == 422 and started == []


def test_the_real_server_opener_sends_the_token_to_mdeis_mcp_only(monkeypatch):
    import asyncio

    opened = []

    class Recorder:
        def __init__(self, name, url, headers, sse=False):
            opened.append((name, url, dict(headers), sse))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    monkeypatch.setattr(service, "McpServer", Recorder)
    settings = load_settings({"MDEIS_MCP_URL": "http://mdeis-mcp:8100/mcp"})

    async def go():
        async with service.open_servers(settings, TOKEN, "RUN-7") as servers:
            return set(servers)

    assert asyncio.run(go()) == {tools.MDEIS, tools.PG}
    by_name = {name: headers for name, _, headers, _ in opened}
    assert by_name[tools.MDEIS] == {"X-Run-Id": "RUN-7", "X-Approver-Token": TOKEN}
    assert by_name[tools.PG] == {"X-Run-Id": "RUN-7"}
    assert [sse for _, _, _, sse in opened] == [False, True]


def test_graph_mode_runs_the_graph_and_health_says_which_mode_is_running(monkeypatch):
    started = []
    client = make_client(monkeypatch, started)
    monkeypatch.setenv("ORCHESTRATOR_MODE", "graph")
    response = client.post("/run", json=BODY, headers={"Authorization": f"Bearer {KEY}"})
    pairs = events(response)
    assert [n for n, _ in pairs][-1] == "final"
    assert pairs[-1][1]["answer"] == "The records show a steady trend [A-000001]."
    health = client.get("/healthz").json()  # no database in a unit test: it still answers
    assert health["mode"] == "graph"
    assert health["engine"] in ("langgraph", "graph without langgraph")
    monkeypatch.setenv("ORCHESTRATOR_MODE", "linear")
    assert client.get("/healthz").json()["engine"] == "plain loop"
