"""The two ends of the lesson route (T-061): the orchestrator's `/lessons` and the gateway's
pass-through. A fake model, a fake tool server and a mock orchestrator stand in for the real ones,
so there is no network and no key.

What is held here: no key means 401 and no run; a body may hold only what the tutor chose and wrote,
so it cannot name an author, a school or an approver; the signed token (not the body) reaches the
tool server and carries the school; a failure is a plain message that carries no error text; and
there is no route that approves or rejects.
"""

import json
from contextlib import asynccontextmanager

import httpx
import pytest
from lesson_graph_fakes import LessonTools
from lessonkit import LESSON, OBJECTIVE
from orchestrator_fakes import FakeModel, say

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from gateway import app as gateway  # noqa: E402
from mdeis.core import approver_token  # noqa: E402
from orchestrator import app as service  # noqa: E402
from orchestrator import tools  # noqa: E402

KEY = "test-key-not-a-real-one"
TOKEN = "signed.token"
GOOD = json.dumps({"edits": [{
    "id": "e1", "type": "reword", "anchor_quote": "Ask what the passage is about.",
    "replacement": "Ask what the passage tells us and why.", "finding_ids": ["CF-a1b2c3d4"],
    "resource_ids": [], "suggestion": "Give learners a question that asks for a reason.",
}]})
BODY = {
    "approver_token": TOKEN, "stage": "Form 3", "area": "literacy", "term": 2,
    "objective": OBJECTIVE, "notes": LESSON,
}


# --------------------------------------------------------------------------- the orchestrator
def make(monkeypatch, started, *, server=None, model=None, view=True, configured=True,
         fail=None):
    monkeypatch.setenv("ORCHESTRATOR_API_KEY", KEY)
    monkeypatch.setenv("CLASS_VIEW_ENABLED", "true" if view else "false")
    if configured:
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
        monkeypatch.setenv("OPENROUTER_MODEL", "open/weights-model")
    else:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    server = server or LessonTools()

    @asynccontextmanager
    async def servers(settings, token, run_id):
        started.append({"token": token, "run_id": run_id})
        if fail:
            raise fail
        yield {tools.MDEIS: server}

    chosen = model or FakeModel(say(GOOD))
    application = service.create_app(
        model_factory=lambda settings: chosen, lesson_servers_factory=servers
    )
    return TestClient(application), server, chosen


AUTH = {"Authorization": f"Bearer {KEY}"}


def test_a_request_without_the_key_or_with_a_wrong_one_is_401_and_starts_no_run(monkeypatch):
    started = []
    client, _, _ = make(monkeypatch, started)
    assert client.post("/lessons", json=BODY).status_code == 401
    wrong = client.post("/lessons", json=BODY, headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401
    assert client.get("/lessons/LP-000001", headers={"X-Approver-Token": TOKEN}).status_code == 401
    assert started == []


def test_a_lesson_is_run_with_the_signed_token_and_the_answer_is_json(monkeypatch):
    started = []
    client, server, model = make(monkeypatch, started)
    response = client.post("/lessons", json=BODY, headers=AUTH)
    assert response.status_code == 200
    answer = response.json()
    assert answer["outcome"] == "stored" and answer["plan"]["plan_id"] == "LP-000001"
    assert len(started) == 1 and started[0]["token"] == TOKEN
    assert started[0]["run_id"].startswith("RUN-") and answer["run_id"] == started[0]["run_id"]
    assert TOKEN not in json.dumps(model.seen), "the model is never shown the token"
    assert TOKEN not in json.dumps(answer)
    assert TOKEN not in json.dumps(server.calls), "the token is a header, not an argument"


@pytest.mark.parametrize(
    "field",
    ["author", "author_id", "school", "school_id", "approver", "approver_id", "user", "status",
     "model", "run_id"],
)
def test_a_body_that_names_an_author_a_school_or_an_approver_is_refused(monkeypatch, field):
    started = []
    client, server, model = make(monkeypatch, started)
    response = client.post("/lessons", json={**BODY, field: "someone"}, headers=AUTH)
    assert response.status_code == 422
    assert started == [] and server.calls == [] and model.seen == []


@pytest.mark.parametrize(
    "change",
    [
        {"approver_token": ""}, {"stage": ""}, {"area": ""}, {"objective": ""}, {"notes": ""},
        {"term": "2"}, {"term": 2.5}, {"notes": "x" * (service.MAX_CHARS + 1)},
    ],
    ids=["no token", "no stage", "no area", "no objective", "no notes", "term as text",
         "term as fraction", "notes too long"],
)
def test_a_body_that_is_incomplete_or_too_large_is_refused(monkeypatch, change):
    started = []
    client, _, _ = make(monkeypatch, started)
    assert client.post("/lessons", json={**BODY, **change}, headers=AUTH).status_code == 422
    assert started == []


def test_a_body_with_no_token_at_all_is_refused(monkeypatch):
    started = []
    client, _, _ = make(monkeypatch, started)
    body = {k: v for k, v in BODY.items() if k != "approver_token"}
    assert client.post("/lessons", json=body, headers=AUTH).status_code == 422
    assert started == []


def test_with_no_model_configured_the_answer_says_so_in_plain_words_and_nothing_starts(monkeypatch):
    started = []
    client, _, _ = make(monkeypatch, started, configured=False)
    response = client.post("/lessons", json=BODY, headers=AUTH)
    assert response.status_code == 503
    assert response.json()["outcome"] == "error"
    assert response.json()["statement"] == service.NOT_CONFIGURED
    assert started == []


def test_with_the_class_view_off_the_lesson_is_refused_in_plain_words_and_nothing_starts(
    monkeypatch,
):
    started = []
    client, _, model = make(monkeypatch, started, view=False)
    response = client.post("/lessons", json=BODY, headers=AUTH)
    assert response.status_code == 200 and response.json()["outcome"] == "refused"
    assert response.json()["statement"] == service.LESSONS_OFF
    assert started == [] and model.seen == []


def test_a_failure_is_a_plain_message_with_the_error_class_only(monkeypatch):
    started = []
    client, _, _ = make(monkeypatch, started, fail=RuntimeError("boom: the notes said Amara"))
    response = client.post("/lessons", json=BODY, headers=AUTH)
    assert response.status_code == 502
    text = json.dumps(response.json())
    assert "RuntimeError" in text and "Nothing was saved" in text
    assert "boom" not in text and "Amara" not in text


def test_a_plan_is_read_with_the_token_from_the_header_and_no_model(monkeypatch):
    started = []
    client, server, model = make(monkeypatch, started)
    headers = {**AUTH, "X-Approver-Token": TOKEN}
    response = client.get("/lessons/LP-000001", headers=headers)
    assert response.status_code == 200 and response.json()["plan_id"] == "LP-000001"
    assert server.calls == [("get_lesson_plan", {"plan_id": "LP-000001"})]
    assert started[0]["token"] == TOKEN and model.seen == []


@pytest.mark.parametrize("plan_id", ["LP-1", "x", "LP-00000a", "lp-000001", "LP-000001%20x"])
def test_a_plan_number_that_is_not_one_is_refused_before_anything_starts(monkeypatch, plan_id):
    started = []
    client, _, _ = make(monkeypatch, started)
    response = client.get(f"/lessons/{plan_id}", headers={**AUTH, "X-Approver-Token": TOKEN})
    assert response.status_code in (404, 422) and started == []


def test_reading_a_plan_with_no_token_is_401_and_nothing_starts(monkeypatch):
    started = []
    client, _, _ = make(monkeypatch, started)
    assert client.get("/lessons/LP-000001", headers=AUTH).status_code == 401
    assert started == []


# --------------------------------------------------------------------------- the gateway
GW_KEY = "gateway-key-not-real"
SECRET = "signing-secret-not-real"
ORCH_KEY = "orchestrator-key-not-real"
SIGNED_IN = {
    "Authorization": f"Bearer {GW_KEY}",
    "X-OpenWebUI-User-Id": "u-ada",
    "X-OpenWebUI-User-Name": "Ada Teacher",
}
TUTOR_BODY = {k: v for k, v in BODY.items() if k != "approver_token"}
ANSWER = {
    "outcome": "stored", "statement": "ok", "run_id": "RUN-1", "plan": {"plan_id": "LP-000001"},
}


class Orchestrator:
    """A mock orchestrator that keeps the requests it received."""

    def __init__(self, status=200, body=None, error=None, raw=None):
        self.status, self.body, self.error, self.raw = status, body or ANSWER, error, raw
        self.requests = []

    def handler(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        if self.raw is not None:
            return httpx.Response(self.status, content=self.raw)
        return httpx.Response(self.status, json=self.body)

    def lessons(self):
        return [r for r in self.requests if r.url.path.startswith("/lessons")]


@pytest.fixture
def gw(monkeypatch):
    monkeypatch.setenv("GATEWAY_API_KEY", GW_KEY)
    monkeypatch.setenv("APPROVER_TOKEN_SECRET", SECRET)
    monkeypatch.setenv("ORCHESTRATOR_API_KEY", ORCH_KEY)
    monkeypatch.setenv("ORCHESTRATOR_URL", "http://orchestrator:8300")
    monkeypatch.setenv("GATEWAY_SCHOOL_CODE", "OCC")

    def build(orchestrator):
        def factory():
            return httpx.AsyncClient(transport=httpx.MockTransport(orchestrator.handler))

        return TestClient(gateway.create_app(client_factory=factory))

    return build


def test_the_gateway_refuses_a_lesson_with_no_key_or_no_signed_in_user(gw):
    orchestrator = Orchestrator()
    client = gw(orchestrator)
    assert client.post("/lessons", json=TUTOR_BODY).status_code == 401
    no_user = {"Authorization": f"Bearer {GW_KEY}"}
    assert client.post("/lessons", json=TUTOR_BODY, headers=no_user).status_code == 401
    assert client.get("/lessons/LP-000001", headers=no_user).status_code == 401
    assert orchestrator.lessons() == []


def test_the_gateway_signs_the_token_with_the_user_and_the_school_and_forwards_only_the_lesson(gw):
    orchestrator = Orchestrator()
    response = gw(orchestrator).post("/lessons", json=TUTOR_BODY, headers=SIGNED_IN)
    assert response.status_code == 200 and response.json() == ANSWER
    (request,) = orchestrator.lessons()
    assert request.method == "POST" and request.url.path == "/lessons"
    assert request.headers["Authorization"] == f"Bearer {ORCH_KEY}"
    sent = json.loads(request.content)
    assert set(sent) == {"stage", "area", "term", "objective", "notes", "approver_token"}
    who = approver_token.verify(SECRET, sent["approver_token"])
    assert (who.id, who.name, who.school) == ("u-ada", "Ada Teacher", "OCC")
    assert {k: sent[k] for k in TUTOR_BODY} == TUTOR_BODY


@pytest.mark.parametrize("field", ["author", "school", "approver", "approver_id", "approver_token",
                                   "user"])
def test_the_gateway_refuses_a_body_that_names_a_person_or_a_school(gw, field):
    orchestrator = Orchestrator()
    response = gw(orchestrator).post(
        "/lessons", json={**TUTOR_BODY, field: "someone"}, headers=SIGNED_IN
    )
    assert response.status_code == 422 and orchestrator.lessons() == []


def test_the_gateway_passes_the_orchestrators_refusal_through_with_its_status(gw):
    orchestrator = Orchestrator(status=422, body={"detail": "bad"})
    response = gw(orchestrator).post("/lessons", json=TUTOR_BODY, headers=SIGNED_IN)
    assert response.status_code == 422 and response.json() == {"detail": "bad"}


@pytest.mark.parametrize(
    "orchestrator",
    [
        Orchestrator(error=httpx.ConnectError("down")),
        Orchestrator(raw=b"<html>not json</html>"),
    ],
    ids=["unreachable", "not json"],
)
def test_when_the_orchestrator_fails_the_gateway_says_so_and_makes_up_nothing(gw, orchestrator):
    response = gw(orchestrator).post("/lessons", json=TUTOR_BODY, headers=SIGNED_IN)
    assert response.status_code == 502
    body = response.json()
    assert body["outcome"] == "error" and body["plan"] is None
    assert "Nothing was saved" in body["statement"]


def test_the_gateway_reads_a_plan_with_the_signed_token_in_a_header(gw):
    orchestrator = Orchestrator(body={"status": "ok", "plan_id": "LP-000001"})
    response = gw(orchestrator).get("/lessons/LP-000001", headers=SIGNED_IN)
    assert response.status_code == 200 and response.json()["plan_id"] == "LP-000001"
    (request,) = orchestrator.lessons()
    assert request.method == "GET" and request.url.path == "/lessons/LP-000001"
    assert request.headers["Authorization"] == f"Bearer {ORCH_KEY}"
    who = approver_token.verify(SECRET, request.headers["X-Approver-Token"])
    assert (who.id, who.school) == ("u-ada", "OCC")


@pytest.mark.parametrize("plan_id", ["LP-1", "x", "LP-00000a", "lp-000001"])
def test_the_gateway_refuses_a_plan_number_that_is_not_one(gw, plan_id):
    orchestrator = Orchestrator()
    response = gw(orchestrator).get(f"/lessons/{plan_id}", headers=SIGNED_IN)
    assert response.status_code == 422 and orchestrator.lessons() == []
