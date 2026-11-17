"""The gateway's route for the portal's "Download PDF" (T-056). The tool server is a mock
(httpx.MockTransport), so nothing here touches a network.

What is held here: the route needs the portal's own key (the chat key does not open it); the person
comes from the signed-in headers and the school from the gateway's setting, both signed into the
token sent on; the shared key is added here and goes to the tool server only; a PDF comes back as
the file with its download header; a refusal comes back as JSON with its own status; a malformed
report number never reaches the tool server; and a failure is said plainly.
"""

import json

import httpx
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from test_gateway_portal_routes import (  # noqa: E402
    CHAT_KEY,
    HUMAN_KEY,
    MCP,
    PORTAL,
    PORTAL_KEY,
    SECRET,
    USER,
)

from gateway import app as gateway  # noqa: E402
from mdeis.core import approver_token  # noqa: E402

REPORT = "R-000001"
PATH = f"/portal/reports/{REPORT}/pdf"
PDF = b"%PDF-1.4\nfake file for a gateway test\n%%EOF\n"
DISPOSITION = f'attachment; filename="{REPORT}.pdf"'


class ToolServer:
    def __init__(self):
        self.seen = []
        self.down = False
        self.answer = httpx.Response(
            200, content=PDF,
            headers={"content-type": "application/pdf", "content-disposition": DISPOSITION},
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if self.down:
            raise httpx.ConnectError("refused")
        return self.answer


@pytest.fixture
def world(monkeypatch):
    for name, value in {
        "GATEWAY_API_KEY": CHAT_KEY, "GATEWAY_PORTAL_KEY": PORTAL_KEY, "HUMAN_ROUTE_KEY": HUMAN_KEY,
        "APPROVER_TOKEN_SECRET": SECRET, "ORCHESTRATOR_API_KEY": "orch-key-not-real",
        "ORCHESTRATOR_URL": "http://orchestrator:8300", "MDEIS_MCP_URL": f"{MCP}/mcp",
        "GATEWAY_SCHOOL_CODE": "TST",
    }.items():
        monkeypatch.setenv(name, value)
    server = ToolServer()
    app = gateway.create_app(
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(server.handler))
    )
    return TestClient(app), server


def test_the_portal_key_gets_the_file_with_its_download_header(world):
    client, server = world
    answer = client.post(PATH, headers=PORTAL)
    assert answer.status_code == 200 and answer.content == PDF
    assert answer.headers["content-type"] == "application/pdf"
    assert answer.headers["content-disposition"] == DISPOSITION


def test_the_tool_server_is_asked_as_the_signed_in_person_with_the_shared_key(world):
    client, server = world
    client.post(PATH, headers=PORTAL)
    (sent,) = server.seen
    assert (sent.method, sent.url.host, sent.url.path) == (
        "POST", "mdeis-mcp", f"/human/reports/{REPORT}/pdf")
    assert sent.headers["x-human-route-key"] == HUMAN_KEY
    who = approver_token.verify(SECRET, sent.headers["x-approver-token"])
    assert (who.id, who.name, who.school) == ("u-1", "Ms Tutor", "TST")
    assert json.loads(sent.content or b"{}") == {}, "no body can name a person"
    assert "authorization" not in sent.headers, "the portal's key is not passed on"


def test_the_shared_key_is_never_in_what_the_portal_gets_back(world):
    client, server = world
    answer = client.post(PATH, headers=PORTAL)
    assert HUMAN_KEY.encode() not in answer.content
    assert "x-human-route-key" not in answer.headers


@pytest.mark.parametrize("label,headers", [
    ("no key", {**USER}),
    ("the chat key", {"Authorization": f"Bearer {CHAT_KEY}", **USER}),
    ("a wrong key", {"Authorization": "Bearer another", **USER}),
    ("no word Bearer", {"Authorization": PORTAL_KEY, **USER}),
])
def test_the_route_needs_the_portals_own_key(world, label, headers):
    client, server = world
    assert client.post(PATH, headers=headers).status_code == 401
    assert server.seen == []


def test_with_the_portal_key_unset_the_route_is_locked(world, monkeypatch):
    client, server = world
    monkeypatch.setenv("GATEWAY_PORTAL_KEY", "")
    assert client.post(PATH, headers=PORTAL).status_code == 401
    assert server.seen == []


def test_nobody_signed_in_means_401_and_nothing_is_sent_on(world):
    client, server = world
    assert client.post(PATH, headers={"Authorization": f"Bearer {PORTAL_KEY}"}).status_code == 401
    assert server.seen == []


@pytest.mark.parametrize("bad", ["R-12", "r-000001", "LP-000001", "R-000001x", "R-"])
def test_a_malformed_report_number_never_reaches_the_tool_server(world, bad):
    client, server = world
    assert client.post(f"/portal/reports/{bad}/pdf", headers=PORTAL).status_code == 422
    assert server.seen == []


def test_only_post_is_accepted(world):
    client, server = world
    assert client.get(PATH, headers=PORTAL).status_code == 405
    assert server.seen == []


@pytest.mark.parametrize("status,reason", [
    (404, "report_not_found"), (409, "report_not_released"), (403, "no_school"),
    (401, "expired_token"),
])
def test_a_refusal_is_handed_back_as_json_with_its_own_status(world, status, reason):
    client, server = world
    refusal = {"status": "refused", "reason": reason, "detail": "plain words"}
    server.answer = httpx.Response(status, json=refusal)
    answer = client.post(PATH, headers=PORTAL)
    assert answer.status_code == status and answer.json() == refusal
    assert PDF not in answer.content


def test_a_200_that_is_not_a_pdf_is_not_passed_on_as_one(world):
    client, server = world
    server.answer = httpx.Response(200, json={"status": "ok"})
    answer = client.post(PATH, headers=PORTAL)
    assert answer.headers["content-type"].startswith("application/json")


def test_with_no_shared_key_set_the_routes_are_off_and_nothing_is_sent(world, monkeypatch):
    client, server = world
    monkeypatch.setenv("HUMAN_ROUTE_KEY", "")
    answer = client.post(PATH, headers=PORTAL)
    assert answer.status_code == 503 and answer.json()["reason"] == "routes_off"
    assert server.seen == []


def test_a_tool_server_that_is_down_is_a_plain_502_and_no_file(world):
    client, server = world
    server.down = True
    answer = client.post(PATH, headers=PORTAL)
    assert answer.status_code == 502 and answer.json()["reason"] == "service_unavailable"
    assert PDF not in answer.content


def test_the_gateway_still_holds_no_pdf_code():
    from pathlib import Path

    text = Path(gateway.__file__).read_text("utf-8")
    for word in ("render_report_pdf", "pdf_writer", "mdeis.export", "fpdf", "reportlab"):
        assert word not in text, word
