"""The gateway's routes for the portal's Methods page (T-073). The tool server is a mock
(httpx.MockTransport), so nothing here touches a network.

Held here, in the same way as for the Lessons routes (`test_gateway_portal_routes.py`): the routes
need the portal's own key (not the chat key); the person comes from the signed-in headers and the
school from the gateway's setting, both signed into the token the gateway makes; the human-route key
is added here, sent to the tool server only and never returned; bodies are closed so no field can
name a person, a school, a status or a decision; a measure number and a version are checked here;
and a failure is said plainly. Nothing goes through the orchestrator.
"""

import json

import httpx
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from gateway import app as gateway  # noqa: E402
from mdeis.core import approver_token  # noqa: E402

CHAT_KEY = "chat-key-not-real"
PORTAL_KEY = "portal-key-not-real"
HUMAN_KEY = "human-key-not-real"
SECRET = "approver-secret-not-real"
ORCH_KEY = "orchestrator-key-not-real"
MCP = "http://mdeis-mcp:8100"
MS = "MS-0001"
BASE = f"/portal/measures/{MS}/1"
USER = {"X-OpenWebUI-User-Id": "u-1", "X-OpenWebUI-User-Name": "Ms Teacher"}
PORTAL = {"Authorization": f"Bearer {PORTAL_KEY}", **USER}
ENTRIES = [{"subject": "English", "term": 1, "score": "52"},
           {"subject": "English", "term": 2, "score": "58"}]
TRY = {"stage": "Form 1", "entries": ENTRIES}
CASE = {**TRY, "expected": {"change": "6"}, "agreed": True}
ROUTES = [
    ("GET", "/portal/measures", None, "/human/measures"),
    ("GET", BASE, None, f"/human/measures/{MS}/1"),
    ("POST", f"{BASE}/try", TRY, f"/human/measures/{MS}/1/try"),
    ("POST", f"{BASE}/case", CASE, f"/human/measures/{MS}/1/case"),
    ("POST", f"{BASE}/confirm", {"note": "checked"}, f"/human/measures/{MS}/1/confirm"),
    ("POST", f"{BASE}/reject", {"note": "no"}, f"/human/measures/{MS}/1/reject"),
]


class Backends:
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


@pytest.mark.parametrize("method,path,body,_target", ROUTES)
@pytest.mark.parametrize("label", list(KEY_CASES))
def test_a_methods_route_needs_the_portals_own_key(world, method, path, body, _target, label):
    client, backends = world
    assert call(client, method, path, body, KEY_CASES[label]).status_code == 401
    assert backends.seen == []


@pytest.mark.parametrize("method,path,body,_target", ROUTES)
def test_with_the_portal_key_unset_every_methods_route_is_locked(
    world, monkeypatch, method, path, body, _target
):
    client, backends = world
    monkeypatch.setenv("GATEWAY_PORTAL_KEY", "")
    for sent in (PORTAL, {"Authorization": "Bearer ", **USER}):
        assert call(client, method, path, body, sent).status_code == 401
    assert backends.seen == []


def test_the_portal_key_still_opens_nothing_that_belongs_to_the_chat(world):
    client, backends = world
    assert client.get("/v1/models", headers=PORTAL).status_code == 401
    assert backends.seen == []


@pytest.mark.parametrize("method,path,body,_target", ROUTES)
def test_nobody_signed_in_means_401_and_nothing_is_sent_on(world, method, path, body, _target):
    client, backends = world
    headers = {"Authorization": f"Bearer {PORTAL_KEY}"}
    assert call(client, method, path, body, headers).status_code == 401
    assert backends.seen == []


# --------------------------------------------------------------------------- what is sent on
@pytest.mark.parametrize("method,path,body,target", ROUTES)
def test_a_route_reaches_the_tool_server_with_the_key_and_a_token_for_that_person(
    world, method, path, body, target
):
    client, backends = world
    backends.answer = (200, {"status": "ok", "marker": "from the tool server"})
    answer = call(client, method, path, body)
    assert answer.status_code == 200 and answer.json()["marker"] == "from the tool server"
    (sent,) = backends.seen
    assert (sent.method, str(sent.url)) == (method, MCP + target)
    assert sent.headers["x-human-route-key"] == HUMAN_KEY
    who = approver_token.verify(SECRET, sent.headers["x-approver-token"])
    assert (who.id, who.name, who.school) == ("u-1", "Ms Teacher", "TST")
    assert "authorization" not in sent.headers, "the portal's key does not travel on"
    if body is not None:
        assert json.loads(sent.content) == body


@pytest.mark.parametrize("method,path,body,_target", ROUTES)
def test_the_human_key_and_the_orchestrators_key_never_come_back(
    world, method, path, body, _target
):
    client, backends = world
    answer = call(client, method, path, body)
    assert HUMAN_KEY not in answer.text and ORCH_KEY not in answer.text
    assert HUMAN_KEY not in str(answer.headers) and SECRET not in answer.text


def test_no_methods_route_goes_through_the_orchestrator(world):
    client, backends = world
    for method, path, body, _ in ROUTES:
        call(client, method, path, body)
    assert {r.url.host for r in backends.seen} == {"mdeis-mcp"}


def test_what_the_tool_server_says_is_handed_back_with_its_status(world):
    client, backends = world
    refusal = {"status": "refused", "reason": "approval_conditions_not_met", "detail": "plain",
               "gaps": [{"code": "no_independent_test", "text": "x"}]}
    backends.answer = (409, refusal)
    answer = call(client, "POST", f"{BASE}/confirm", {"note": "x"})
    assert answer.status_code == 409 and answer.json() == refusal


# --------------------------------------------------------------------------- closed bodies
NAMING = ["approver", "approver_id", "approver_name", "user", "user_id", "name", "teacher",
          "teacher_name", "school", "school_code", "status", "decision", "measure_id", "version",
          "learner_id", "approver_token"]


@pytest.mark.parametrize("field", NAMING)
@pytest.mark.parametrize("path,body", [
    (f"{BASE}/try", TRY), (f"{BASE}/case", CASE), (f"{BASE}/confirm", {"note": "n"}),
    (f"{BASE}/reject", {"note": "n"}),
])
def test_a_body_cannot_name_a_person_a_school_a_status_or_a_decision(world, path, body, field):
    client, backends = world
    assert call(client, "POST", path, {**body, field: "x"}).status_code == 422
    assert backends.seen == []


@pytest.mark.parametrize("field", ["learner_id", "name", "school", "approver"])
def test_an_entry_cannot_carry_a_learner_a_name_a_school_or_a_person(world, field):
    client, backends = world
    entry = {**ENTRIES[0], field: "x"}
    assert call(client, "POST", f"{BASE}/try", {**TRY, "entries": [entry]}).status_code == 422
    assert backends.seen == []


@pytest.mark.parametrize("path,body", [
    (f"{BASE}/try", {"stage": "Form 1"}),
    (f"{BASE}/try", {"entries": ENTRIES}),
    (f"{BASE}/try", {"stage": "Form 1", "entries": "52"}),
    (f"{BASE}/try", {"stage": "Form 1", "entries": [{"subject": "English", "term": "1",
                                                        "score": "5"}]}),
    (f"{BASE}/try", {"stage": "Form 1", "entries": [{"subject": "English", "term": 1,
                                                        "score": 5.5}]}),
    (f"{BASE}/case", {**TRY}),
    (f"{BASE}/case", {**TRY, "expected": ["11"]}),
    (f"{BASE}/reject", {}),
    (f"{BASE}/reject", {"note": ""}),
    (f"{BASE}/confirm", {"note": 3}),
])
def test_a_body_of_the_wrong_shape_is_refused_here_and_sent_nowhere(world, path, body):
    client, backends = world
    assert call(client, "POST", path, body).status_code == 422
    assert backends.seen == []


@pytest.mark.parametrize("path", [
    "/portal/measures/MS-1/1", "/portal/measures/ms-0001/1", "/portal/measures/MS-0001x/1",
    f"/portal/measures/{MS}/0", f"/portal/measures/{MS}/x", f"/portal/measures/{MS}/-1",
])
def test_a_measure_number_or_version_that_is_not_one_of_ours_is_refused_here(world, path):
    client, backends = world
    assert call(client, "GET", path, None).status_code == 422
    assert call(client, "POST", path + "/confirm", {}).status_code == 422
    assert backends.seen == []


def test_a_confirm_may_carry_no_body_at_all(world):
    client, backends = world
    answer = client.post(f"{BASE}/confirm", headers=PORTAL)
    assert answer.status_code == 200 and json.loads(backends.seen[0].content or b"{}") in ({},)


# --------------------------------------------------------------------------- when things are off
@pytest.mark.parametrize("method,path,body,_target", ROUTES)
def test_with_the_human_route_key_unset_the_methods_routes_say_so_and_send_nothing(
    world, monkeypatch, method, path, body, _target
):
    client, backends = world
    monkeypatch.setenv("HUMAN_ROUTE_KEY", "")
    answer = call(client, method, path, body)
    assert answer.status_code == 503 and answer.json()["reason"] == "routes_off"
    assert backends.seen == []


def test_a_tool_server_that_is_down_is_a_plain_message_with_no_error_text(world):
    client, backends = world
    backends.down = True
    answer = call(client, "GET", "/portal/measures", None)
    assert answer.status_code == 502 and answer.json()["reason"] == "service_unavailable"
    assert answer.json()["detail"] and "ConnectError" not in answer.text


# --------------------------------------------------------------------------- the route table
def test_the_gateway_has_exactly_these_methods_routes_and_none_is_a_chat_route():
    found = {(r.path, m) for r in gateway.create_app().routes
             for m in (getattr(r, "methods", None) or ()) if m not in ("HEAD", "OPTIONS")}
    assert {p for p in found if p[0].startswith("/portal/measures")} == {
        ("/portal/measures", "GET"),
        ("/portal/measures/{measure_id}/{version}", "GET"),
        ("/portal/measures/{measure_id}/{version}/try", "POST"),
        ("/portal/measures/{measure_id}/{version}/case", "POST"),
        ("/portal/measures/{measure_id}/{version}/confirm", "POST"),
        ("/portal/measures/{measure_id}/{version}/reject", "POST"),
    }
    assert not [p for p in found if "measure" in p[0] and not p[0].startswith("/portal/")]
