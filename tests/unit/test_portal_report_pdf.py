"""The portal's "Download PDF" for a released report (T-056): the server side the page calls, the
client that asks the gateway, and the page's own files. The gateway is replaced by a fake and Open
WebUI by a fake sign-in check, so nothing here needs a network or Postgres.

What is held here: only a signed-in person gets anything; the post needs the CSRF token and the
page's own origin; the person passed on is the one Open WebUI vouched for; the body is closed; a
malformed report number never reaches the gateway; a PDF comes back as a download and a refusal as
JSON with its status; the portal builds no PDF; and the page is written the same way as the rest
(no markup built from text, one place that calls the server, the CSRF token on the post).
"""

import asyncio
from pathlib import Path

import httpx
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from test_portal import ADA, BEN, ORIGIN, FakeAuth, FakeStore, csrf  # noqa: E402
from test_portal_lessons import settings  # noqa: E402

from portal import app as portal  # noqa: E402
from portal import gateway as portal_gateway  # noqa: E402
from portal.gateway import GatewayClient  # noqa: E402

STATIC = Path(portal.__file__).parent / "static"
PAGE = (STATIC / "index.html").read_text("utf-8")
APP = (STATIC / "app.js").read_text("utf-8")
REPORT = "R-000001"
PATH = f"/api/reports/{REPORT}/pdf"
PDF = b"%PDF-1.4\nfake file for a portal test\n%%EOF\n"


class FakeGateway:
    configured = True

    def __init__(self):
        self.calls = []
        self.answer = (200, PDF)

    async def request(self, method, path, user, body=None):  # (the Lessons page; not used here)
        raise AssertionError("the report route must not use the lessons call")

    async def download(self, method, path, user):
        self.calls.append({"method": method, "path": path, "user": user})
        return self.answer


@pytest.fixture
def world(tmp_path):
    gateway = FakeGateway()
    app = portal.create_app(settings(tmp_path), auth=FakeAuth(), store=FakeStore(), gateway=gateway)
    client = TestClient(app)
    client.cookies.set("token", "ada-token")
    return client, gateway


def post(client, path=PATH, token="ada-token", **kw):
    headers = {"X-CSRF-Token": csrf(client, token), **ORIGIN, **kw.pop("headers", {})}
    return client.post(path, headers=headers, **kw)


# --------------------------------------------------------------------------- who may ask
def test_nobody_signed_in_gets_401_and_the_gateway_is_not_called(tmp_path):
    gateway = FakeGateway()
    app = portal.create_app(settings(tmp_path), auth=FakeAuth(), store=FakeStore(), gateway=gateway)
    client = TestClient(app)
    assert client.post(PATH, headers=ORIGIN).status_code == 401
    client.cookies.set("token", "not-a-real-token")
    assert client.post(PATH, headers=ORIGIN).status_code == 401
    assert gateway.calls == []


def test_the_post_needs_the_csrf_token_and_the_pages_own_origin(world):
    client, gateway = world
    good, other = csrf(client), csrf(client, "ben-token")
    client.cookies.set("token", "ada-token")
    cases = {
        "no token": ({**ORIGIN}, 403),
        "another persons token": ({"X-CSRF-Token": other, **ORIGIN}, 403),
        "a made-up token": ({"X-CSRF-Token": "1.abcd", **ORIGIN}, 403),
        "no origin": ({"X-CSRF-Token": good}, 403),
        "a foreign origin": ({"X-CSRF-Token": good, "Origin": "https://evil.example"}, 403),
    }
    for label, (headers, status) in cases.items():
        assert client.post(PATH, headers=headers).status_code == status, label
    assert gateway.calls == []


def test_a_get_is_not_accepted(world):
    client, gateway = world
    assert client.get(PATH).status_code == 405
    assert gateway.calls == []


# --------------------------------------------------------------------------- the good answer
def test_a_signed_in_person_gets_the_file_as_a_download_and_is_passed_on_as_themselves(world):
    client, gateway = world
    answer = post(client)
    assert answer.status_code == 200 and answer.content == PDF
    assert answer.headers["content-type"] == "application/pdf"
    assert answer.headers["content-disposition"] == f'attachment; filename="{REPORT}.pdf"'
    (call,) = gateway.calls
    assert call["method"] == "POST" and call["path"] == f"/portal/reports/{REPORT}/pdf"
    assert (call["user"].id, call["user"].name) == (ADA.id, ADA.name)


def test_a_second_person_is_passed_on_as_themselves(world):
    client, gateway = world
    client.cookies.set("token", "ben-token")
    assert post(client, token="ben-token").status_code == 200
    assert gateway.calls[0]["user"] == BEN


def test_the_file_name_is_made_from_the_checked_number_not_from_the_gateway(world):
    client, gateway = world
    answer = post(client)
    assert 'filename="R-000001.pdf"' in answer.headers["content-disposition"]


# --------------------------------------------------------------------------- refusals
@pytest.mark.parametrize("status,reason", [(404, "report_not_found"), (409, "report_not_released"),
                                           (503, "routes_off"), (502, "service_unavailable")])
def test_a_refusal_from_the_gateway_is_handed_back_as_json_with_its_status(world, status, reason):
    client, gateway = world
    gateway.answer = (status, {"status": "refused", "reason": reason, "detail": "plain words"})
    answer = post(client)
    assert answer.status_code == status and answer.json()["reason"] == reason
    assert PDF not in answer.content


def test_an_answer_that_is_neither_a_file_nor_an_object_is_a_plain_502(world):
    client, gateway = world
    gateway.answer = (200, ["odd"])
    answer = post(client)
    assert answer.status_code == 502 and answer.json()["reason"] == "service_unavailable"


@pytest.mark.parametrize("bad", ["R-12", "r-000001", "LP-000001", "R-000001x", "R-", "x"])
def test_a_malformed_report_number_is_not_found_and_the_gateway_is_not_called(world, bad):
    client, gateway = world
    answer = post(client, path=f"/api/reports/{bad}/pdf")
    assert answer.status_code == 404 and answer.json()["reason"] == "report_not_found"
    assert gateway.calls == []


@pytest.mark.parametrize("raw", [b'{"approver": "x"}', b'{"school": "TST"}', b"[]", b"not json"])
def test_the_body_is_closed_it_may_be_empty_or_an_empty_object_and_nothing_else(world, raw):
    client, gateway = world
    assert post(client, content=raw).status_code == 400
    assert gateway.calls == []
    assert post(client).status_code == 200
    assert post(client, content=b"{}").status_code == 200


def test_a_body_over_a_few_bytes_is_refused_as_too_large_before_anything_is_sent(world):
    client, gateway = world
    assert post(client, content=b"x" * 100).status_code == 413
    assert gateway.calls == []


def test_with_no_gateway_key_the_route_says_so_and_calls_nothing(tmp_path):
    gateway = FakeGateway()
    gateway.configured = False
    app = portal.create_app(settings(tmp_path), auth=FakeAuth(), store=FakeStore(), gateway=gateway)
    client = TestClient(app)
    client.cookies.set("token", "ada-token")
    answer = post(client)
    assert answer.status_code == 503 and answer.json()["reason"] == "reports_off"
    assert gateway.calls == []


# --------------------------------------------------------------------------- the client
def mocked(handler):
    return GatewayClient(
        "http://gateway:8200", "portal-key-not-real",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def test_the_client_returns_a_pdf_as_bytes_and_asks_as_the_signed_in_person():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})

    path = "/portal/reports/R-000001/pdf"
    status, answer = asyncio.run(mocked(handler).download("POST", path, ADA))
    assert (status, answer) == (200, PDF)
    (sent,) = seen
    assert sent.headers["authorization"] == "Bearer portal-key-not-real"
    assert sent.headers[portal_gateway.USER_ID_HEADER] == ADA.id
    assert sent.headers[portal_gateway.USER_NAME_HEADER] == ADA.name
    assert sent.content == b""


def test_the_client_returns_a_refusal_as_a_dict_with_its_status():
    body = {"status": "refused", "reason": "report_not_released", "detail": "x"}

    def handler(request):
        return httpx.Response(409, json=body)

    assert asyncio.run(mocked(handler).download("POST", "/p", ADA)) == (409, body)


def test_a_200_that_is_not_a_pdf_is_not_returned_as_bytes():
    def handler(request):
        return httpx.Response(200, json={"status": "ok"})

    status, answer = asyncio.run(mocked(handler).download("POST", "/p", ADA))
    assert status == 200 and answer == {"status": "ok"}


def test_a_dropped_connection_or_a_non_json_answer_is_one_plain_502_with_no_text_from_it():
    def down(request):
        raise httpx.ConnectError("secret detail")

    def text(request):
        return httpx.Response(500, text="secret detail")

    for handler in (down, text):
        status, answer = asyncio.run(mocked(handler).download("POST", "/p", ADA))
        assert status == 502 and answer == portal_gateway.UNAVAILABLE_BODY
        assert "secret" not in str(answer)


# --------------------------------------------------------------------------- builds nothing
def test_the_portal_has_no_pdf_writer_no_report_reader_and_no_model_code():
    folder = Path(portal.__file__).parent
    text = "".join(p.read_text("utf-8") for p in folder.rglob("*.py"))
    for word in ("render_report_pdf", "pdf_writer", "mdeis.export", "fpdf", "reportlab",
                 "FROM reports", "released_by", "OPENROUTER", "/human/", "human_routes"):
        assert word not in text, word


# --------------------------------------------------------------------------- the page
def test_the_page_has_a_download_form_with_a_report_number_and_a_download_button():
    assert 'id="report-form"' in PAGE and 'id="report-id"' in PAGE
    assert "Download PDF" in PAGE and 'id="report-message"' in PAGE
    start = PAGE.index('id="report-form"')
    form = PAGE[start: PAGE.index("</form>", start)]
    assert 'type="file"' not in form, "it is not an upload form"


def test_the_page_script_posts_with_the_csrf_token_through_the_one_call_function():
    assert APP.count("fetch(") == 1, "every call still goes through call()"
    start = APP.index("async function downloadReport")
    body = APP[start: APP.index("\n}\n", start)]
    assert 'call("api/reports/"' in body and '"/pdf"' in body
    assert 'method: "POST"' in body and '"X-CSRF-Token": csrf' in body and "asFile: true" in body
    assert "innerHTML" not in body and "eval(" not in body
    assert "/api/" not in APP, "addresses stay relative so the page works under /portal/"


def test_the_page_checks_the_number_before_it_asks_and_sends_nothing_else():
    start = APP.index("async function downloadReport")
    body = APP[start: APP.index("\n}\n", start)]
    assert "REPORT_NUMBER.test(number)" in body
    assert "JSON.stringify" not in body and "body:" not in body, "the post has no body at all"
    assert 'const REPORT_NUMBER = /^R-[0-9]{6,}$/;' in APP


def test_the_file_is_saved_from_a_local_object_and_the_address_is_let_go():
    start = APP.index("async function downloadReport")
    body = APP[start: APP.index("\n}\n", start)]
    assert "URL.createObjectURL(reply.body)" in body and "URL.revokeObjectURL(url)" in body
    assert 'link.download = number + ".pdf"' in body


def test_a_sign_out_clears_the_number_and_the_message_from_the_screen():
    start = APP.index("function showSignIn")
    body = APP[start: APP.index("\n}\n", start)]
    assert '$("report-id").value = ""' in body and '$("report-message").textContent = ""' in body


def test_the_start_up_loop_still_skips_a_boxed_form_that_is_not_an_upload_form():
    start_up = APP[APP.index('addEventListener("DOMContentLoaded"'):]
    loop = start_up[start_up.index('querySelectorAll(".box")'):][:400]
    assert "if (!form.elements.file) continue;" in loop
    assert '$("report-form").addEventListener("submit", downloadReport);' in start_up
