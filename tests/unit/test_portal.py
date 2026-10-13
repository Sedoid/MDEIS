"""The upload portal (E-18): who may upload, what is kept, and what a hostile request gets.

Open WebUI is replaced by a fake sign-in check and the database by a fake store, so nothing here
needs a network or Postgres. The service is built with `create_app`, which takes both.
"""

import asyncio
import hashlib
from pathlib import Path

import httpx
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from portal import app as portal  # noqa: E402
from portal import security  # noqa: E402
from portal.auth import OpenWebUIAuth, Unavailable, User  # noqa: E402
from portal.settings import MB, Settings  # noqa: E402

SECRET = "portal-secret-not-real"
ADA = User(id="u-ada", name="Ada Teacher", role="admin")
BEN = User(id="u-ben", name="Ben Staff", role="user")
PDF = b"%PDF-1.4\n% a made-up card\n"
MD = b"# Methodology\n\n6.4 Reading the scores\n"
ORIGIN = {"Origin": "http://testserver"}


class FakeAuth:
    """Knows two tokens. Anything else is nobody. Two people can sign in."""

    def __init__(self, down=False):
        self.users = {"ada-token": ADA, "ben-token": BEN}
        self.logins = {("ada@example.org", "right"): "ada-token"}
        self.down, self.signed_out = down, []

    async def user_for(self, token):
        return self.users.get(token)

    async def sign_in(self, email, password):
        if self.down:
            raise Unavailable
        return self.logins.get((email, password))

    async def sign_out(self, token):
        self.signed_out.append(token)


class FakeStore:
    def __init__(self, fail=False):
        self.rows, self.fail = [], fail

    def add(self, **row):
        if self.fail:
            raise RuntimeError("database is down")
        self.rows.append(row)
        return f"D-{len(self.rows):06d}"

    def list(self, uploader_id=None):
        return [r for r in self.rows if uploader_id in (None, r["uploader_id"])]

    def get(self, document_id):
        return None


def settings(tmp_path, **changes):
    values = dict(
        database_url="", inbox=str(tmp_path), webui_url="http://webui:8080",
        webui_public_url="http://127.0.0.1:3000", session_secret=SECRET, cookie_name="token",
        max_bytes=10 * MB, max_markdown_bytes=1 * MB, frame_chat=True,
    )
    values.update(changes)
    return Settings(**values)


@pytest.fixture
def world(tmp_path):
    store = FakeStore()
    app = portal.create_app(settings(tmp_path), auth=FakeAuth(), store=store)
    return TestClient(app), store, tmp_path


def as_user(client, token="ada-token"):
    client.cookies.set("token", token)
    return client


def csrf(client, token="ada-token"):
    as_user(client, token)
    return client.get("/api/csrf").json()["csrf"]


def upload(client, body, kind="results", name="card.pdf", token="ada-token", **headers):
    sent = {"X-CSRF-Token": csrf(client, token), "X-File-Name": name, **ORIGIN, **headers}
    return client.post(f"/api/documents?kind={kind}", content=body, headers=sent)


def files_in(folder: Path):
    return [p for p in folder.iterdir() if p.is_file()]


# -- who is signed in ------------------------------------------------------------------------


def test_no_login_is_401(world):
    client, store, folder = world
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/csrf").status_code == 401
    assert client.get("/api/documents").status_code == 401
    assert client.post("/api/documents?kind=results", content=PDF).status_code == 401
    assert store.rows == [] and files_in(folder) == []


def test_unknown_token_is_401(world):
    client, _, _ = world
    assert as_user(client, "forged").get("/api/me").status_code == 401


def test_me_says_who_and_whether_admin(world):
    client, _, _ = world
    assert as_user(client).get("/api/me").json() == {
        "id": "u-ada", "name": "Ada Teacher", "admin": True}
    assert as_user(client, "ben-token").get("/api/me").json()["admin"] is False


def test_the_bearer_header_also_works_for_the_cookie_name(world):
    client, _, _ = world
    assert client.get("/api/me", headers={"Authorization": "Bearer ada-token"}).status_code == 200


# -- signing in through the portal ----------------------------------------------------------


def sign_in(client, email="ada@example.org", password="right", **headers):
    return client.post("/api/signin", json={"email": email, "password": password},
                       headers={**ORIGIN, **headers})


def test_signing_in_sets_the_login_cookie_and_gives_back_no_token(world):
    client, _, _ = world
    reply = sign_in(client)
    assert reply.status_code == 200
    assert reply.json() == {"id": "u-ada", "name": "Ada Teacher", "admin": True}
    assert "ada-token" not in reply.text
    cookie = reply.headers["set-cookie"].lower()
    assert "token=ada-token" in cookie and "httponly" in cookie
    assert "samesite=lax" in cookie and "path=/" in cookie and "max-age=" in cookie
    assert "secure" not in cookie.replace("samesite", "")  # plain http in this test
    assert client.get("/api/me").json()["name"] == "Ada Teacher"  # the jar now holds the cookie


def test_the_cookie_is_secure_when_the_public_address_is_https(tmp_path):
    app = portal.create_app(
        settings(tmp_path, portal_public_url="https://school.example.org"),
        auth=FakeAuth(), store=FakeStore())
    reply = TestClient(app).post(
        "/api/signin", json={"email": "ada@example.org", "password": "right"},
        headers={"Origin": "https://school.example.org"})
    assert reply.status_code == 200 and "secure" in reply.headers["set-cookie"].lower()


def test_a_wrong_password_is_401_with_no_cookie_and_no_hint(world):
    client, _, _ = world
    reply = sign_in(client, password="wrong")
    assert reply.status_code == 401
    assert "set-cookie" not in reply.headers
    assert "wrong" not in reply.text and "ada@example.org" not in reply.text
    assert client.get("/api/me").status_code == 401


def test_sign_in_with_webui_down_is_503(tmp_path):
    app = portal.create_app(settings(tmp_path), auth=FakeAuth(down=True), store=FakeStore())
    assert sign_in(TestClient(app)).status_code == 503


@pytest.mark.parametrize("body", [{}, {"email": "a@b.c"}, {"email": "", "password": "x"},
                                  {"email": 5, "password": "x"}, [], "text"])
def test_a_bad_sign_in_body_is_400(world, body):
    client, _, _ = world
    assert client.post("/api/signin", json=body, headers=ORIGIN).status_code == 400
    assert client.post("/api/signin", content=b"not json", headers=ORIGIN).status_code == 400


def test_sign_in_from_another_origin_is_403(world):
    client, _, _ = world
    assert sign_in(client, Origin="https://evil.example").status_code == 403
    assert client.post("/api/signin", json={"email": "ada@example.org", "password": "right"}
                       ).status_code == 403


def test_repeated_failures_are_slowed_down(world):
    client, _, _ = world
    codes = [sign_in(client, password=f"bad{i}").status_code for i in range(security.TRIES + 2)]
    assert codes[: security.TRIES] == [401] * security.TRIES
    assert codes[-1] == 429
    assert sign_in(client).status_code == 429  # even the right password waits
    assert sign_in(client, email="ben@example.org", password="x").status_code == 401  # others don't


def test_the_chat_hand_over_gives_the_users_own_login_only_with_the_csrf_token(world):
    client, _, _ = world
    assert client.post("/api/chat-session", headers=ORIGIN).status_code == 401
    sign_in(client)
    assert client.post("/api/chat-session", headers=ORIGIN).status_code == 403
    token = client.get("/api/csrf").json()["csrf"]
    other = {"Origin": "https://evil.example", "X-CSRF-Token": token}
    assert client.post("/api/chat-session", headers=other).status_code == 403
    reply = client.post("/api/chat-session", headers={"X-CSRF-Token": token, **ORIGIN})
    assert reply.status_code == 200 and reply.json() == {"token": "ada-token"}
    assert reply.headers["cache-control"] == "no-store"
    assert client.get("/api/me").text.count("ada-token") == 0


def test_the_password_is_never_logged(world, caplog):
    client, _, _ = world
    with caplog.at_level("DEBUG"):
        sign_in(client, password="hunter2-not-real")
        sign_in(client)
    assert "hunter2-not-real" not in caplog.text and "ada-token" not in caplog.text


def test_signing_out_needs_the_csrf_token_and_clears_the_cookie(world):
    client, _, _ = world
    sign_in(client)
    assert client.post("/api/signout", headers=ORIGIN).status_code == 403
    token = client.get("/api/csrf").json()["csrf"]
    reply = client.post("/api/signout", headers={"X-CSRF-Token": token, **ORIGIN})
    assert reply.status_code == 200
    assert 'token=""' in reply.headers["set-cookie"] or "max-age=0" in reply.headers[
        "set-cookie"].lower()
    assert client.get("/api/me").status_code == 401


# -- uploads ---------------------------------------------------------------------------------


def test_a_report_card_is_stored_under_a_generated_name(world):
    client, store, folder = world
    reply = upload(client, PDF)
    assert reply.status_code == 201
    assert reply.json() == {"document_id": "D-000001", "status": "pending"}
    row = store.rows[0]
    assert row["kind"] == "results" and row["original_name"] == "card.pdf"
    assert row["sha256"] == hashlib.sha256(PDF).hexdigest() and row["size_bytes"] == len(PDF)
    assert row["uploader_id"] == "u-ada" and row["uploader_name"] == "Ada Teacher"
    stored = files_in(folder)
    assert [p.name for p in stored] == [row["stored_name"]]
    assert row["stored_name"].endswith(".pdf") and "card" not in row["stored_name"]
    assert stored[0].read_bytes() == PDF


def test_a_non_admin_may_upload_report_cards(world):
    client, store, _ = world
    assert upload(client, PDF, token="ben-token").status_code == 201
    assert store.rows[0]["uploader_id"] == "u-ben"


def test_methodology_is_for_admins_only(world):
    client, store, folder = world
    assert upload(client, PDF, kind="methodology", token="ben-token").status_code == 403
    assert store.rows == [] and files_in(folder) == []
    assert upload(client, PDF, kind="methodology", token="ada-token").status_code == 201


def test_methodology_may_be_markdown(world):
    client, store, _ = world
    assert upload(client, MD, kind="methodology", name="rules.md").status_code == 201
    assert store.rows[0]["stored_name"].endswith(".md")


def test_a_card_must_be_a_pdf_and_the_name_is_not_trusted(world):
    client, store, folder = world
    assert upload(client, MD, name="card.md").status_code == 415
    assert upload(client, b"MZ\x90\x00 not a pdf", name="card.pdf").status_code == 415
    assert upload(client, PDF, name="card.exe").status_code == 415
    assert upload(client, b"\xff\xfe\x00 bytes", kind="methodology", name="a.md").status_code == 415
    assert store.rows == [] and files_in(folder) == []


def test_an_unknown_kind_is_400(world):
    client, store, _ = world
    assert upload(client, PDF, kind="photos").status_code == 400
    assert store.rows == []


def test_an_oversize_file_is_413_and_nothing_is_kept(tmp_path):
    store = FakeStore()
    app = portal.create_app(settings(tmp_path, max_bytes=1000), auth=FakeAuth(), store=store)
    client = TestClient(app)
    assert upload(client, PDF + b"x" * 2000).status_code == 413
    assert store.rows == [] and files_in(tmp_path) == []


def test_markdown_has_its_own_smaller_cap(tmp_path):
    app = portal.create_app(
        settings(tmp_path, max_markdown_bytes=100), auth=FakeAuth(), store=FakeStore())
    client = TestClient(app)
    assert upload(client, MD + b"a" * 500, kind="methodology", name="m.md").status_code == 413
    assert files_in(tmp_path) == []


def test_an_empty_body_is_400(world):
    client, store, _ = world
    assert upload(client, b"").status_code == 400
    assert store.rows == []


def test_a_hostile_file_name_is_cleaned_and_never_used_on_disk(world):
    client, store, folder = world
    reply = upload(client, PDF, name="../../x<script>alert(1)</script>.pdf")
    assert reply.status_code == 201
    shown = store.rows[0]["original_name"]
    assert "/" not in shown and ".." not in shown.split("x")[0]
    assert store.rows[0]["stored_name"].count("/") == 0
    assert [p.parent for p in files_in(folder)] == [folder]


def test_a_failed_insert_leaves_no_file(tmp_path):
    app = portal.create_app(settings(tmp_path), auth=FakeAuth(), store=FakeStore(fail=True))
    client = TestClient(app, raise_server_exceptions=False)
    assert upload(client, PDF).status_code == 503
    assert files_in(tmp_path) == []


def test_no_temporary_file_is_left_after_a_refusal(world):
    client, _, folder = world
    upload(client, b"nope", name="card.pdf")
    assert list(folder.iterdir()) == []


# -- CSRF and origin -------------------------------------------------------------------------


def test_a_missing_csrf_token_is_403(world):
    client, store, folder = world
    as_user(client)
    reply = client.post("/api/documents?kind=results", content=PDF,
                        headers={"X-File-Name": "c.pdf", **ORIGIN})
    assert reply.status_code == 403
    assert store.rows == [] and files_in(folder) == []


def test_another_users_csrf_token_is_403(world):
    client, store, _ = world
    other = security.csrf_token(SECRET, "u-ben")
    as_user(client)
    reply = client.post("/api/documents?kind=results", content=PDF,
                        headers={"X-CSRF-Token": other, "X-File-Name": "c.pdf", **ORIGIN})
    assert reply.status_code == 403 and store.rows == []


def test_a_foreign_origin_is_403(world):
    client, store, _ = world
    reply = upload(client, PDF, Origin="https://evil.example")
    assert reply.status_code == 403 and store.rows == []


def test_csrf_tokens_expire_and_are_bound_to_the_user():
    token = security.csrf_token(SECRET, "u-ada", now=10_000)
    assert security.csrf_ok(SECRET, "u-ada", token, now=10_000)
    assert not security.csrf_ok(SECRET, "u-ben", token, now=10_000)
    assert not security.csrf_ok(SECRET, "u-ada", token, now=10_000 + 3 * security.CSRF_SECONDS)
    assert not security.csrf_ok("other-secret", "u-ada", token, now=10_000)
    assert not security.csrf_ok(SECRET, "u-ada", "garbage", now=10_000)
    assert not security.csrf_ok("", "u-ada", token, now=10_000)


def test_clean_name():
    assert security.clean_name("..\\..\\a/b/card.pdf") == "card.pdf"
    assert security.clean_name("a\x00b\x1f.pdf") == "ab.pdf"
    assert security.clean_name("") == "unnamed"
    assert len(security.clean_name("a" * 1000)) == security.NAME_LIMIT


# -- what the page may see -------------------------------------------------------------------


def test_the_list_shows_only_my_files_unless_admin(world):
    client, store, _ = world
    upload(client, PDF, token="ben-token")
    upload(client, PDF + b"2", token="ada-token")
    assert len(as_user(client, "ben-token").get("/api/documents").json()["documents"]) == 1
    assert len(as_user(client, "ada-token").get("/api/documents").json()["documents"]) == 2


def test_security_headers_and_no_cors(world):
    client, _, _ = world
    reply = client.get("/")
    assert reply.status_code == 200
    csp = reply.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-src http://127.0.0.1:3000" in csp
    assert "unsafe-inline" not in csp
    assert reply.headers["x-content-type-options"] == "nosniff"
    assert "access-control-allow-origin" not in reply.headers
    assert "access-control-allow-origin" not in client.get(
        "/api/me", headers={"Origin": "https://evil.example"}).headers


def test_the_chat_frame_can_be_switched_off(tmp_path):
    app = portal.create_app(
        settings(tmp_path, frame_chat=False), auth=FakeAuth(), store=FakeStore())
    csp = TestClient(app).get("/").headers["content-security-policy"]
    assert "frame-src 'none'" in csp


def test_config_names_the_chat_address(world):
    client, _, _ = world
    assert as_user(client).get("/api/config").json() == {
        "chat_url": "http://127.0.0.1:3000", "frame_chat": True,
        "max_mb": 10, "max_markdown_mb": 1}


def test_the_page_files_have_no_inline_script_or_style():
    folder = Path(portal.__file__).parent / "static"
    page = (folder / "index.html").read_text()
    assert 'type="password"' in page and 'autocomplete="current-password"' in page
    assert "<script src=" in page and "<link" in page
    for bad in ("<style", "onclick=", "onload=", "javascript:", "http://", "https://"):
        assert bad not in page
    script = (folder / "app.js").read_text()
    assert "innerHTML" not in script and "eval(" not in script


def test_no_secret_or_model_code_in_the_portal():
    folder = Path(portal.__file__).parent
    text = "".join(p.read_text() for p in folder.rglob("*.py"))
    for forbidden in ("APPROVER_TOKEN_SECRET", "OPENROUTER", "orchestrator", "mdeis_app"):
        assert forbidden not in text


# -- the sign-in check against Open WebUI ----------------------------------------------------


def webui(answer, calls):
    def handler(request):
        calls.append(request.headers.get("authorization"))
        return answer

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_a_signed_in_user_is_read_and_remembered():
    calls = []
    reply = httpx.Response(200, json={"id": "u-1", "name": "Ada", "role": "admin"})
    auth = OpenWebUIAuth("http://webui:8080", client=webui(reply, calls))
    first = asyncio.run(auth.user_for("tok"))
    assert first == User("u-1", "Ada", "admin") and calls == ["Bearer tok"]
    asyncio.run(auth.user_for("tok"))
    assert len(calls) == 1  # the second answer came from memory
    assert all("tok" not in key for key in auth._seen)  # kept under a hash, not the token


@pytest.mark.parametrize("reply", [
    httpx.Response(401), httpx.Response(200, text="not json"),
    httpx.Response(200, json={"id": "u", "role": "pending"}),
    httpx.Response(200, json={"name": "x", "role": "admin"}),
])
def test_anything_else_is_nobody(reply):
    auth = OpenWebUIAuth("http://webui:8080", client=webui(reply, []))
    assert asyncio.run(auth.user_for("tok")) is None
    assert asyncio.run(auth.user_for(None)) is None
    assert asyncio.run(auth.user_for("")) is None


def test_webui_being_down_is_nobody():
    def down(request):
        raise httpx.ConnectError("refused")

    auth = OpenWebUIAuth("http://webui:8080", client=httpx.AsyncClient(
        transport=httpx.MockTransport(down)))
    assert asyncio.run(auth.user_for("tok")) is None
