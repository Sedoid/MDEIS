"""The gateway's routes for the portal's Lessons page (T-062). The tool server and the orchestrator
are mocks (httpx.MockTransport), so nothing here touches a network.

What is held here: the portal routes need the portal's own key, and neither the chat key nor the
portal key opens the other's routes; the person comes from the signed-in headers and the school
from the gateway's setting, both signed into the token the gateway makes; the human-route key is
added here, sent to the tool server only, and never returned; bodies are closed; and a failure is
said plainly.
"""

import json
from pathlib import Path

import httpx
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from gateway import app as gateway  # noqa: E402
from mdeis import human_routes  # noqa: E402
from mdeis.core import approver_token  # noqa: E402

CHAT_KEY = "chat-key-not-real"
PORTAL_KEY = "portal-key-not-real"
HUMAN_KEY = "human-key-not-real"
SECRET = "approver-secret-not-real"
ORCH_KEY = "orchestrator-key-not-real"
MCP = "http://mdeis-mcp:8100"
PLAN = "LP-000001"
USER = {"X-OpenWebUI-User-Id": "u-1", "X-OpenWebUI-User-Name": "Ms Tutor"}
PORTAL = {"Authorization": f"Bearer {PORTAL_KEY}", **USER}
TUTOR = {
    "stage": "Form 3", "area": "literacy", "term": 2, "objective": "Explain the main idea.",
    "notes": "Objective: Explain the main idea.\n\n## Starter\nRead the passage.",
}
HUMAN_ROUTES = [
    ("GET", "/portal/lessons/options", None),
    ("GET", "/portal/lessons", None),
    ("GET", f"/portal/lessons/{PLAN}", None),
    ("POST", f"/portal/lessons/{PLAN}/approve", {"accepted_edit_ids": ["e1"]}),
    ("POST", f"/portal/lessons/{PLAN}/reject", {}),
]
ALL_ROUTES = [("POST", "/portal/lessons", TUTOR), *HUMAN_ROUTES]


class Backends:
    """A tool server and an orchestrator in one mock. It records each request it gets."""

    def __init__(self):
        self.seen = []
        self.answer = (200, {"status": "ok"})
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if self.down:
            raise httpx.ConnectError("refused")
        status, body = self.answer
        return httpx.Response(status, json=body)

    def to(self, host):
        return [r for r in self.seen if r.url.host == host]


@pytest.fixture
def world(monkeypatch):
    for name, value in {
        "GATEWAY_API_KEY": CHAT_KEY, "GATEWAY_PORTAL_KEY": PORTAL_KEY, "HUMAN_ROUTE_KEY": HUMAN_KEY,
        "APPROVER_TOKEN_SECRET": SECRET, "ORCHESTRATOR_API_KEY": ORCH_KEY,
        "ORCHESTRATOR_URL": "http://orchestrator:8300", "MDEIS_MCP_URL": f"{MCP}/mcp",
        "GATEWAY_SCHOOL_CODE": "TST",
    }.items():
        monkeypatch.setenv(name, value)
    backends = Backends()
    app = gateway.create_app(
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(backends.handler))
    )
    return TestClient(app), backends


def call(client, method, path, body, headers=None):
    sent = {"json": body} if body is not None else {}
    return client.request(method, path, headers=PORTAL if headers is None else headers, **sent)


# --------------------------------------------------------------------------- the keys
KEY_CASES = {
    "no key": {**USER},
    "the chat key": {"Authorization": f"Bearer {CHAT_KEY}", **USER},
    "a wrong key": {"Authorization": "Bearer another", **USER},
    "no word Bearer": {"Authorization": PORTAL_KEY, **USER},
    "an empty key": {"Authorization": "Bearer ", **USER},
}


@pytest.mark.parametrize("method,path,body", ALL_ROUTES)
@pytest.mark.parametrize("label", list(KEY_CASES))
def test_a_portal_route_needs_the_portals_own_key(world, method, path, body, label):
    client, backends = world
    assert call(client, method, path, body, KEY_CASES[label]).status_code == 401
    assert backends.seen == []


@pytest.mark.parametrize("method,path,body", ALL_ROUTES)
def test_with_the_portal_key_unset_every_portal_route_is_locked(
    world, monkeypatch, method, path, body
):
    client, backends = world
    monkeypatch.setenv("GATEWAY_PORTAL_KEY", "")
    for sent in (PORTAL, {"Authorization": "Bearer ", **USER}, {"Authorization": "Bearer", **USER}):
        assert call(client, method, path, body, sent).status_code == 401
    assert backends.seen == []


def test_the_portal_key_opens_nothing_that_belongs_to_the_chat(world):
    client, backends = world
    assert client.get("/v1/models", headers=PORTAL).status_code == 401
    chat = {"messages": [{"role": "user", "content": "hi"}]}
    assert client.post("/v1/chat/completions", json=chat, headers=PORTAL).status_code == 401
    assert client.post("/lessons", json=TUTOR, headers=PORTAL).status_code == 401
    assert client.get(f"/lessons/{PLAN}", headers=PORTAL).status_code == 401
    assert backends.seen == []


def test_the_chat_key_opens_no_human_route_through_the_gateway(world):
    client, backends = world
    chat = {"Authorization": f"Bearer {CHAT_KEY}", **USER}
    for method, path, body in ALL_ROUTES:
        assert call(client, method, path, body, chat).status_code == 401
    assert backends.seen == []


# --------------------------------------------------------------------------- the signed-in person
@pytest.mark.parametrize("method,path,body", ALL_ROUTES)
def test_nobody_signed_in_means_401_and_nothing_is_sent_on(world, method, path, body):
    client, backends = world
    sent = {"Authorization": f"Bearer {PORTAL_KEY}"}
    assert call(client, method, path, body, sent).status_code == 401
    blank = {**sent, "X-OpenWebUI-User-Id": "  "}
    assert call(client, method, path, body, blank).status_code == 401
    assert backends.seen == []


@pytest.mark.parametrize("method,path,body", HUMAN_ROUTES)
def test_a_human_route_reaches_the_tool_server_with_the_key_and_a_token_for_that_person(
    world, method, path, body
):
    client, backends = world
    backends.answer = (200, {"status": "ok", "marker": "from the tool server"})
    answer = call(client, method, path, body)
    assert answer.status_code == 200 and answer.json()["marker"] == "from the tool server"
    (sent,) = backends.seen
    assert sent.url.host == "mdeis-mcp" and sent.url.port == 8100
    assert str(sent.url).startswith(f"{MCP}/human/lessons")
    assert sent.url.path == path.replace("/portal/lessons", "/human/lessons")
    assert sent.method == method
    assert sent.headers["x-human-route-key"] == HUMAN_KEY
    who = approver_token.verify(SECRET, sent.headers["x-approver-token"])
    assert (who.id, who.name, who.school) == ("u-1", "Ms Tutor", "TST")
    assert "authorization" not in sent.headers, "neither the portal's key nor the chat key goes on"
    assert HUMAN_KEY not in answer.text and PORTAL_KEY not in answer.text


def test_the_body_that_reaches_the_tool_server_is_only_what_the_tutor_chose(world):
    client, backends = world
    call(client, "POST", f"/portal/lessons/{PLAN}/approve", {"accepted_edit_ids": ["e1", "e2"]})
    call(client, "POST", f"/portal/lessons/{PLAN}/reject", {})
    approve, reject = backends.seen
    assert json.loads(approve.content) == {"accepted_edit_ids": ["e1", "e2"]}
    assert json.loads(reject.content) == {}


def test_what_the_tool_server_says_is_handed_back_with_its_status(world):
    client, backends = world
    refusal = {"status": "refused", "reason": "not_the_author", "detail": "plain words"}
    backends.answer = (403, refusal)
    answer = call(client, "POST", f"/portal/lessons/{PLAN}/approve", {"accepted_edit_ids": ["e1"]})
    assert answer.status_code == 403 and answer.json() == refusal


def test_no_human_route_goes_through_the_orchestrator_and_the_orchestrators_key_never_leaves(world):
    client, backends = world
    for method, path, body in HUMAN_ROUTES:
        call(client, method, path, body)
    assert backends.to("orchestrator") == []
    assert all(ORCH_KEY not in str(r.headers) for r in backends.seen)


# --------------------------------------------------------------------------- closed bodies
NAMING_SOMEONE = [
    "approver", "approver_id", "approver_name", "author", "author_id", "user", "user_id", "name",
    "school", "school_id", "school_code", "status", "plan_id", "approver_token",
]


@pytest.mark.parametrize("field", NAMING_SOMEONE)
def test_an_approve_body_cannot_name_a_person_or_a_school(world, field):
    client, backends = world
    body = {"accepted_edit_ids": ["e1"], field: "u-9"}
    assert call(client, "POST", f"/portal/lessons/{PLAN}/approve", body).status_code == 422
    assert backends.seen == []


@pytest.mark.parametrize("field", NAMING_SOMEONE)
def test_a_reject_body_and_a_create_body_cannot_either(world, field):
    client, backends = world
    assert call(client, "POST", f"/portal/lessons/{PLAN}/reject", {field: "u-9"}).status_code == 422
    assert call(client, "POST", "/portal/lessons", {**TUTOR, field: "u-9"}).status_code == 422
    assert backends.seen == []


@pytest.mark.parametrize("body", [{}, {"accepted_edit_ids": "e1"}, {"accepted_edit_ids": [1]},
                                  {"accepted_edit_ids": None}, []])
def test_the_accepted_ids_must_be_a_list_of_text(world, body):
    client, backends = world
    assert call(client, "POST", f"/portal/lessons/{PLAN}/approve", body).status_code == 422
    assert backends.seen == []


@pytest.mark.parametrize("plan", ["LP-1", "lp-000001", "LP-000001x", "x"])
def test_a_plan_number_that_is_not_one_of_ours_is_refused_here(world, plan):
    client, backends = world
    cases = (("GET", "", None), ("POST", "/approve", {"accepted_edit_ids": ["e1"]}),
             ("POST", "/reject", {}))
    for method, tail, body in cases:
        assert call(client, method, f"/portal/lessons/{plan}{tail}", body).status_code == 422
    assert backends.seen == []


# --------------------------------------------------------------------------- when something is off
def test_with_the_human_route_key_unset_the_human_routes_say_so_and_send_nothing(
    world, monkeypatch
):
    client, backends = world
    monkeypatch.setenv("HUMAN_ROUTE_KEY", " ")
    for method, path, body in HUMAN_ROUTES:
        answer = call(client, method, path, body)
        assert answer.status_code == 503 and answer.json()["reason"] == "routes_off"
    assert backends.seen == []


def test_with_no_signing_secret_the_gateway_says_so_and_sends_nothing(world, monkeypatch):
    client, backends = world
    monkeypatch.setenv("APPROVER_TOKEN_SECRET", "")
    for method, path, body in ALL_ROUTES:
        assert call(client, method, path, body).status_code == 500
    assert backends.seen == []


def test_a_tool_server_that_is_down_is_a_plain_message_with_no_error_text(world):
    client, backends = world
    backends.down = True
    for method, path, body in HUMAN_ROUTES:
        answer = call(client, method, path, body)
        assert answer.status_code == 502
        assert answer.json() == {
            "status": "refused", "reason": "service_unavailable",
            "detail": gateway.PORTAL_UNAVAILABLE,
        }
        assert "ConnectError" not in answer.text and HUMAN_KEY not in answer.text


def test_a_tool_server_that_answers_with_something_that_is_not_json_is_the_same_message(world):
    client, backends = world
    backends.handler = lambda request: httpx.Response(200, text="<html>oops</html>")
    transport = httpx.MockTransport(backends.handler)
    app = gateway.create_app(lambda: httpx.AsyncClient(transport=transport))
    answer = call(TestClient(app), "GET", "/portal/lessons", None)
    assert answer.status_code == 502 and answer.json()["reason"] == "service_unavailable"


# --------------------------------------------------------------------------- making a plan
def test_a_new_lesson_goes_to_the_orchestrator_with_its_key_and_the_signed_token(world):
    client, backends = world
    backends.answer = (200, {"outcome": "stored", "plan": {"plan_id": PLAN}})
    answer = call(client, "POST", "/portal/lessons", TUTOR)
    assert answer.status_code == 200 and answer.json()["outcome"] == "stored"
    (sent,) = backends.seen
    assert sent.url.host == "orchestrator" and sent.url.path == "/lessons"
    assert sent.headers["authorization"] == f"Bearer {ORCH_KEY}"
    assert "x-human-route-key" not in sent.headers, "the key goes to the tool server only"
    body = json.loads(sent.content)
    assert set(body) == {"stage", "area", "term", "objective", "notes", "approver_token"}
    who = approver_token.verify(SECRET, body["approver_token"])
    assert (who.id, who.name, who.school) == ("u-1", "Ms Tutor", "TST")
    assert HUMAN_KEY not in sent.content.decode()


# --------------------------------------------------------------------------- the route table
def paths(application):
    return {(r.path, m) for r in application.routes for m in (getattr(r, "methods", None) or ())
            if m not in ("HEAD", "OPTIONS")}


def test_the_gateway_has_exactly_these_routes_that_approve_reject_or_read_through_the_human_path():
    found = paths(gateway.create_app())
    assert {p for p in found if "approve" in p[0] or "reject" in p[0]} == {
        ("/portal/lessons/{plan_id}/approve", "POST"),
        ("/portal/lessons/{plan_id}/reject", "POST"),
    }
    assert {p for p in found if p[0].startswith("/portal/")} == {
        ("/portal/lessons", "POST"), ("/portal/lessons", "GET"),
        ("/portal/lessons/options", "GET"), ("/portal/lessons/{plan_id}", "GET"),
        ("/portal/lessons/{plan_id}/approve", "POST"),
        ("/portal/lessons/{plan_id}/reject", "POST"),
    }
    assert {p for p in found if "lesson" in p[0] and not p[0].startswith("/portal/")} == {
        ("/lessons", "POST"), ("/lessons/{plan_id}", "GET"),
    }, "the chat side keeps its two routes"


def test_the_gateway_names_the_header_the_tool_server_expects():
    assert gateway.HUMAN_KEY_HEADER.lower() == human_routes.KEY_HEADER
    assert gateway.APPROVER_TOKEN_HEADER.lower() == human_routes.TOKEN_HEADER


def test_the_gateway_still_does_not_import_the_lesson_package_or_name_the_plan_table():
    text = Path(gateway.__file__).read_text("utf-8")
    assert "mdeis.lessons" not in text and "lesson_plans" not in text
    assert "approve_lesson" not in text and "reject_lesson" not in text
