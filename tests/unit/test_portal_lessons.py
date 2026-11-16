"""The portal's Lessons page (T-062): the server side that the page calls, and the page's own files.

The gateway is replaced by a fake that records what it is given, and Open WebUI by a fake sign-in
check, so nothing here needs a network or Postgres. What is held here: only a signed-in person gets
anything; every post needs the CSRF token and the page's own origin; the person passed on is the one
Open WebUI vouched for (never one named in a body); a body may hold only what the tutor chose and
wrote; the portal holds no model client and no key for one; the page is written the same way as the
existing one (no markup built from text, no inline script or style, no outside address); and the
page's own rules (when Approve is on, how the two columns are cut) are run under Node when it is
there.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from test_portal import ADA, BEN, ORIGIN, FakeAuth, FakeStore, csrf  # noqa: E402

from portal import app as portal  # noqa: E402
from portal import gateway as portal_gateway  # noqa: E402
from portal import security  # noqa: E402
from portal.auth import User  # noqa: E402
from portal.settings import MB, Settings, load_settings  # noqa: E402

STATIC = Path(portal.__file__).parent / "static"
PLAN = "LP-000001"
TUTOR = {
    "stage": "Form 3", "area": "literacy", "term": 2, "objective": "Explain the main idea.",
    "notes": "Objective: Explain the main idea.\n\n## Starter\nRead the passage aloud.",
}
LISTED = [
    ("GET", "/api/lessons/options", None),
    ("GET", "/api/lessons", None),
    ("GET", f"/api/lessons/{PLAN}", None),
    ("POST", "/api/lessons", TUTOR),
    ("POST", f"/api/lessons/{PLAN}/approve", {"accepted_edit_ids": ["e1"]}),
    ("POST", f"/api/lessons/{PLAN}/reject", {}),
]
POSTS = [row for row in LISTED if row[0] == "POST"]
GATEWAY_PATHS = {
    "/api/lessons/options": "/portal/lessons/options",
    "/api/lessons": "/portal/lessons",
    f"/api/lessons/{PLAN}": f"/portal/lessons/{PLAN}",
    f"/api/lessons/{PLAN}/approve": f"/portal/lessons/{PLAN}/approve",
    f"/api/lessons/{PLAN}/reject": f"/portal/lessons/{PLAN}/reject",
}


class FakeGateway:
    configured = True

    def __init__(self):
        self.calls = []
        self.answer = (200, {"status": "ok", "marker": "from the gateway"})

    async def request(self, method, path, user, body=None):
        self.calls.append({"method": method, "path": path, "user": user, "body": body})
        return self.answer


def settings(tmp_path, **changes):
    values = dict(
        database_url="", inbox=str(tmp_path), webui_url="http://webui:8080",
        webui_public_url="http://127.0.0.1:3000", session_secret="portal-secret-not-real",
        cookie_name="token", max_bytes=10 * MB, max_markdown_bytes=1 * MB, frame_chat=True,
    )
    values.update(changes)
    return Settings(**values)


@pytest.fixture
def world(tmp_path):
    gateway = FakeGateway()
    app = portal.create_app(settings(tmp_path), auth=FakeAuth(), store=FakeStore(), gateway=gateway)
    client = TestClient(app)
    client.cookies.set("token", "ada-token")
    return client, gateway


def post(client, path, body, token="ada-token", **headers):
    sent = {"X-CSRF-Token": csrf(client, token), **ORIGIN, **headers}
    return client.post(path, json=body, headers=sent)


def go(client, method, path, body):
    if method == "GET":
        return client.get(path)
    return post(client, path, body)


# --------------------------------------------------------------------------- signed in only
@pytest.mark.parametrize("method,path,body", LISTED)
def test_nobody_signed_in_gets_401_and_the_gateway_is_not_called(tmp_path, method, path, body):
    gateway = FakeGateway()
    app = portal.create_app(settings(tmp_path), auth=FakeAuth(), store=FakeStore(), gateway=gateway)
    client = TestClient(app)
    sent = {"json": body} if body is not None else {}
    assert client.request(method, path, headers=ORIGIN, **sent).status_code == 401
    client.cookies.set("token", "not-a-real-token")
    assert client.request(method, path, headers=ORIGIN, **sent).status_code == 401
    assert gateway.calls == []


@pytest.mark.parametrize("method,path,body", LISTED)
def test_a_signed_in_person_is_passed_on_with_the_name_open_webui_gave(world, method, path, body):
    client, gateway = world
    answer = go(client, method, path, body)
    assert answer.status_code == 200 and answer.json()["marker"] == "from the gateway"
    (call,) = gateway.calls
    assert call["method"] == method
    assert call["path"] == GATEWAY_PATHS.get(path, "/portal/lessons")
    assert (call["user"].id, call["user"].name) == (ADA.id, ADA.name)
    if method == "POST":
        assert call["body"] == (body or {})


def test_a_second_person_is_passed_on_as_themselves(world):
    client, gateway = world
    client.cookies.set("token", "ben-token")
    assert client.get("/api/lessons").status_code == 200
    assert gateway.calls[0]["user"] == BEN


# --------------------------------------------------------------------------- CSRF and origin
@pytest.mark.parametrize("method,path,body", POSTS)
def test_every_post_needs_the_csrf_token_and_the_pages_own_origin(world, method, path, body):
    client, gateway = world
    good = csrf(client)
    other = csrf(client, "ben-token")
    client.cookies.set("token", "ada-token")
    cases = {
        "no token": ({**ORIGIN}, 403),
        "another persons token": ({"X-CSRF-Token": other, **ORIGIN}, 403),
        "a made-up token": ({"X-CSRF-Token": "1.abcd", **ORIGIN}, 403),
        "no origin": ({"X-CSRF-Token": good}, 403),
        "a foreign origin": ({"X-CSRF-Token": good, "Origin": "https://evil.example"}, 403),
    }
    for label, (headers, status) in cases.items():
        answer = client.post(path, json=body, headers=headers)
        assert answer.status_code == status, label
    assert gateway.calls == []


@pytest.mark.parametrize("method,path,body", POSTS)
def test_an_old_csrf_token_is_refused(world, method, path, body):
    client, gateway = world
    old = security.csrf_token("portal-secret-not-real", ADA.id, now=1_000_000)
    answer = client.post(path, json=body, headers={"X-CSRF-Token": old, **ORIGIN})
    assert answer.status_code == 403 and gateway.calls == []


def test_reading_needs_no_csrf_token_but_still_needs_a_sign_in(world):
    client, gateway = world
    assert client.get("/api/lessons").status_code == 200


# --------------------------------------------------------------------------- closed bodies
NAMING_SOMEONE = [
    "approver", "approver_id", "approver_name", "author", "author_id", "user", "user_id", "name",
    "school", "school_id", "school_code", "status", "plan_id", "approver_token", "model",
]


@pytest.mark.parametrize("field", NAMING_SOMEONE)
def test_no_body_can_name_a_person_a_school_or_a_status(world, field):
    client, gateway = world
    assert post(client, "/api/lessons", {**TUTOR, field: "x"}).status_code == 400
    assert post(client, f"/api/lessons/{PLAN}/approve",
                {"accepted_edit_ids": ["e1"], field: "x"}).status_code == 400
    assert post(client, f"/api/lessons/{PLAN}/reject", {field: "x"}).status_code == 400
    assert gateway.calls == []


@pytest.mark.parametrize("change", [
    {"stage": ""}, {"stage": 3}, {"area": ""}, {"area": None}, {"term": "2"}, {"term": 2.5},
    {"term": True}, {"objective": ""}, {"objective": 5}, {"notes": ""}, {"notes": ["a"]},
])
def test_a_new_lesson_needs_the_five_fields_with_the_right_types(world, change):
    client, gateway = world
    assert post(client, "/api/lessons", {**TUTOR, **change}).status_code == 400
    missing = {k: v for k, v in TUTOR.items() if k not in change}
    assert post(client, "/api/lessons", missing).status_code == 400
    assert gateway.calls == []


@pytest.mark.parametrize("body", [{}, {"accepted_edit_ids": "e1"}, {"accepted_edit_ids": [1]},
                                  {"accepted_edit_ids": None}, {"accepted_edit_ids": [["e1"]]}])
def test_the_accepted_ids_must_be_a_list_of_text(world, body):
    client, gateway = world
    assert post(client, f"/api/lessons/{PLAN}/approve", body).status_code == 400
    assert gateway.calls == []


@pytest.mark.parametrize("raw", [b"not json", b"[]", b"null", b'"x"', b""])
def test_a_body_that_is_not_a_json_object_is_a_plain_400(world, raw):
    client, gateway = world
    headers = {"X-CSRF-Token": csrf(client), **ORIGIN}
    assert client.post("/api/lessons", content=raw, headers=headers).status_code == 400
    approve = client.post(f"/api/lessons/{PLAN}/approve", content=raw, headers=headers)
    assert approve.status_code == 400
    assert gateway.calls == []


def test_a_body_over_the_portals_size_cap_is_refused_before_anything_is_sent(tmp_path):
    gateway = FakeGateway()
    small = settings(tmp_path, max_markdown_bytes=1000)
    app = portal.create_app(small, auth=FakeAuth(), store=FakeStore(), gateway=gateway)
    client = TestClient(app)
    client.cookies.set("token", "ada-token")
    answer = post(client, "/api/lessons", {**TUTOR, "notes": "x" * 5000})
    assert answer.status_code == 413 and gateway.calls == []


@pytest.mark.parametrize("plan", ["LP-1", "lp-000001", "LP-000001x", "x"])
def test_a_plan_number_that_is_not_one_of_ours_is_404_and_not_sent_on(world, plan):
    client, gateway = world
    assert client.get(f"/api/lessons/{plan}").status_code == 404
    accept = {"accepted_edit_ids": ["e1"]}
    assert post(client, f"/api/lessons/{plan}/approve", accept).status_code == 404
    assert post(client, f"/api/lessons/{plan}/reject", {}).status_code == 404
    assert gateway.calls == []


def test_the_reject_route_takes_an_empty_body_or_none(world):
    client, gateway = world
    headers = {"X-CSRF-Token": csrf(client), **ORIGIN}
    assert client.post(f"/api/lessons/{PLAN}/reject", headers=headers).status_code == 200
    again = client.post(f"/api/lessons/{PLAN}/reject", content=b"{}", headers=headers)
    assert again.status_code == 200
    assert [c["body"] for c in gateway.calls] == [{}, {}]


# --------------------------------------------------------------------------- what comes back
def test_what_the_gateway_says_is_handed_back_with_its_status(world):
    client, gateway = world
    refusal = {"status": "refused", "reason": "not_the_author", "detail": "plain words"}
    gateway.answer = (403, refusal)
    answer = post(client, f"/api/lessons/{PLAN}/approve", {"accepted_edit_ids": ["e1"]})
    assert answer.status_code == 403 and answer.json() == refusal


def test_an_answer_that_is_not_an_object_is_a_plain_502(world):
    client, gateway = world
    gateway.answer = (200, ["not", "an", "object"])
    answer = client.get("/api/lessons")
    assert answer.status_code == 502 and answer.json()["reason"] == "service_unavailable"


def test_with_no_gateway_key_the_lessons_routes_say_so_and_call_nothing(tmp_path):
    app = portal.create_app(settings(tmp_path), auth=FakeAuth(), store=FakeStore())
    client = TestClient(app)
    client.cookies.set("token", "ada-token")
    for method, path, body in LISTED:
        answer = go(client, method, path, body)
        assert answer.status_code == 503 and answer.json()["reason"] == "lessons_off"


def test_the_settings_read_the_gateway_address_and_its_key_from_the_environment():
    env = {"GATEWAY_URL": "http://gateway:8000/", "GATEWAY_PORTAL_KEY": "k"}
    got = load_settings(env)
    assert (got.gateway_url, got.gateway_key) == ("http://gateway:8000", "k")
    assert load_settings({}).gateway_key == ""


def test_the_gateway_key_is_not_in_the_config_or_in_any_answer(tmp_path):
    key = "portal-key-not-real"
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"status": "ok"})

    talk = portal_gateway.GatewayClient(
        "http://gateway:8000", key, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    app = portal.create_app(settings(tmp_path), auth=FakeAuth(), store=FakeStore(), gateway=talk)
    client = TestClient(app)
    client.cookies.set("token", "ada-token")
    for answer in (client.get("/api/config"), client.get("/api/lessons"),
                   client.get("/api/lessons/options")):
        assert key not in answer.text and key not in str(answer.headers)
    assert seen and all(key not in str(r.url) for r in seen)


# --------------------------------------------------------------------------- the gateway client
def mocked(handler, key="portal-key-not-real"):
    return portal_gateway.GatewayClient(
        "http://gateway:8000/", key,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def run(coro):
    import asyncio

    return asyncio.run(coro)


def test_the_client_sends_the_portal_key_and_the_person_and_nothing_of_the_persons_login():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(201, json={"status": "ok"})

    talk = mocked(handler)
    status, body = run(talk.request("POST", "/portal/lessons", User("u-1", "Ms Tutor", "user"),
                                    {"stage": "Form 3"}))
    assert (status, body) == (201, {"status": "ok"})
    (sent,) = seen
    assert str(sent.url) == "http://gateway:8000/portal/lessons" and sent.method == "POST"
    assert sent.headers["authorization"] == "Bearer portal-key-not-real"
    assert sent.headers["x-openwebui-user-id"] == "u-1"
    assert sent.headers["x-openwebui-user-name"] == "Ms Tutor"
    assert "cookie" not in sent.headers
    assert json.loads(sent.content) == {"stage": "Form 3"}


def test_the_client_says_plainly_when_the_gateway_is_down_or_answers_nonsense():
    def down(request):
        raise httpx.ConnectError("refused: secret detail")

    for handler in (down, lambda request: httpx.Response(200, text="<html>")):
        status, body = run(mocked(handler).request("GET", "/portal/lessons", ADA))
        assert status == 502
        assert body == {"status": "refused", "reason": "service_unavailable",
                        "detail": portal_gateway.UNAVAILABLE}
        assert "secret" not in json.dumps(body)


def test_a_client_with_no_key_or_no_address_is_not_configured_and_sends_nothing():
    def never(request):
        raise AssertionError("nothing may be sent")

    for url, key in (("", "k"), ("http://gateway:8000", ""), ("", "")):
        talk = portal_gateway.GatewayClient(
            url, key, client=httpx.AsyncClient(transport=httpx.MockTransport(never)))
        assert talk.configured is False


# --------------------------------------------------------------------------- no model, no key
def test_the_portal_holds_no_model_client_and_no_key_for_one():
    folder = Path(portal.__file__).parent
    text = "".join(p.read_text() for p in folder.rglob("*.py"))
    for forbidden in ("openai", "anthropic", "langchain", "langgraph", "OPENROUTER", "complete(",
                      "APPROVER_TOKEN_SECRET", "HUMAN_ROUTE_KEY", "ORCHESTRATOR"):
        assert forbidden not in text, forbidden
    page = (STATIC / "lessons.js").read_text() + (STATIC / "index.html").read_text()
    for forbidden in ("OPENROUTER", "api_key", "apikey", "Bearer", "X-Human-Route-Key"):
        assert forbidden not in page, forbidden


# --------------------------------------------------------------------------- the page's files
SCRIPT_FILE = STATIC / "lessons.js"
SCRIPT = SCRIPT_FILE.read_text()
PAGE = (STATIC / "index.html").read_text()
APP = (STATIC / "app.js").read_text()


LESSONS_VIEW = PAGE[PAGE.index('<section id="view-lessons"'):]
LESSONS_VIEW = LESSONS_VIEW[: LESSONS_VIEW.index("</section>")]


def test_the_page_loads_the_lessons_script_the_same_way_as_the_other_one():
    assert PAGE.count("<script src=") == 2
    assert '<script src="static/lessons.js" defer></script>' in PAGE
    assert PAGE.index("static/app.js") < PAGE.index("static/lessons.js")
    for bad in ("<style", "onclick=", "onload=", "onsubmit=", "javascript:"):
        assert bad not in PAGE
    # Only the new view is held to "no outside address" here: the existing page has a README link,
    # which the older page test (test_the_page_files_have_no_inline_script_or_style) disagrees with.
    for bad in ("http://", "https://", "javascript:", "<script", "<style", "style="):
        assert bad not in LESSONS_VIEW
    for bad in ("http://", "https://", "javascript:"):
        assert bad not in SCRIPT


def test_nothing_is_built_from_text_as_markup_and_no_code_is_made_from_text():
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                "new Function", "setTimeout(\"", "srcdoc", ".src =", "createContextualFragment",
                "DOMParser", "<script", "<style", "fetch(", "XMLHttpRequest", "sendBeacon",
                "WebSocket", "localStorage", "sessionStorage", "document.cookie"):
        assert bad not in SCRIPT, bad
    assert SCRIPT.count("textContent") >= 3
    assert "/api/" not in SCRIPT, "addresses stay relative so the page works under /portal/"
    assert APP.count("fetch(") == 1, "every call still goes through call()"
    assert "innerHTML" not in APP and "eval(" not in APP


def test_the_page_names_the_lessons_view_and_the_nav_link():
    assert 'id="view-lessons"' in PAGE and 'href="#/lessons"' in PAGE
    assert '"lessons"' in APP and "lessonsView" in APP


def function_source(name):
    """The source of one function in the script, whatever its indentation."""
    start = SCRIPT.index(f"function {name}")
    line = SCRIPT.rindex("\n", 0, start) + 1
    indent = SCRIPT[line:start]
    indent = indent[: len(indent) - len(indent.lstrip())]  # not the "async " before "function"
    return SCRIPT[start: SCRIPT.index("\n" + indent + "}\n", start)]


def test_the_school_is_shown_and_not_chosen():
    assert 'id="lesson-school"' in PAGE
    start = PAGE.index('id="lesson-form"')
    form = PAGE[start: PAGE.index("</form>", start)]
    assert 'name="school' not in form and "lesson-school" not in form.replace(
        'id="lesson-school"', "")
    assert "school" not in function_source("createBody")


def test_the_form_has_the_fields_the_brief_names_and_marks_the_objective_as_locked():
    for field in ("lesson-stage", "lesson-area", "lesson-term", "lesson-objective", "lesson-notes"):
        assert f'id="{field}"' in PAGE
    assert PAGE.count("<select") == 3 and PAGE.count("<textarea") == 2
    objective = PAGE[PAGE.index('for="lesson-objective"'):]
    assert "ocked" in objective[: objective.index("</label>")]
    assert 'for="lesson-notes"' in PAGE and "Markdown" in PAGE


def test_the_lists_in_the_form_come_from_the_server_and_are_not_written_in_the_page():
    for word in ("literacy", "numeracy", "Form 1", "Form 2", "Form 3", "Form 4", "Grade", "Term 1"):
        assert word not in PAGE and word not in SCRIPT, word
    assert 'const LESSONS = "api/lessons";' in SCRIPT  # relative, as the rest of the page
    assert 'LESSONS + "/options"' in SCRIPT  # the lists are fetched, from the server
    for guess in ('"literacy"', '"numeracy"', "stages = [", "terms = [", "areas = ["):
        assert guess not in SCRIPT, guess
    assert re.search(r"<select[^>]*id=\"lesson-area\"[^>]*>\s*</select>", PAGE)
    assert re.search(r"<select[^>]*id=\"lesson-stage\"[^>]*>\s*</select>", PAGE)
    assert re.search(r"<select[^>]*id=\"lesson-term\"[^>]*>\s*</select>", PAGE)


def test_the_review_view_has_what_the_brief_lists():
    for text in ("Suggestion, the tutor decides", "Original", "Revised", "Accept", "Reject",
                 "Approve", "will be recorded", "Evidence"):
        assert text in SCRIPT, text
    for word in ("reword", "add_example", "add_check"):
        assert word not in SCRIPT, "the names of the types are shown as the server gives them"
    for key in ("anchor_quote", "replacement", "evidence", "suggestion", "dropped", "notices"):
        assert key in SCRIPT, key


def test_a_dropped_change_is_shown_by_its_id_its_check_and_its_reason_never_its_text():
    shown = function_source("droppedLine")
    assert ".lc" in shown and ".reason" in shown and ".id" in shown
    for text_field in ("anchor_quote", "replacement", "suggestion", "before", "after"):
        assert text_field not in shown


def test_the_only_things_the_page_sends_are_the_five_fields_and_the_accepted_ids():
    for name, keys in (("createBody", ["stage", "area", "term", "objective", "notes"]),
                       ("approveBody", ["accepted_edit_ids"])):
        body = function_source(name)
        assert all(k in body for k in keys), name
        assert not re.search(r"approver|author|school|user|status|plan_id|token", body), name
    assert SCRIPT.count("JSON.stringify(") == 1, "one place sends a body"
    assert SCRIPT.count("X-CSRF-Token") == 1 and "X-CSRF-Token" in function_source("postJson")
    assert SCRIPT.count("api.call(") == 2, "one for reading and one for posting"


def test_the_page_posts_only_what_the_three_bodies_make():
    calls = re.findall(r"(?<!function )\b(?:postJson|finish)\(([^;]*)\);", SCRIPT)
    assert len(calls) == 4, calls  # create, finish (twice: approve and reject), and finish's own
    for sent in calls:
        assert re.search(r"createBody\(|approveBody\(|\{\}|, body$", sent.strip()), sent
    assert "postJson(path, body)" in function_source("finish")


def test_the_existing_page_rules_still_hold_for_the_page_as_a_whole():
    assert 'type="password"' in PAGE and 'autocomplete="current-password"' in PAGE
    assert "<script src=" in PAGE and "<link" in PAGE


# --------------------------------------------------------------------------- the page's rules, run
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node is not installed: the page's rules were "
                                "not run (this is a skip, not a pass)")


def logic(expression: str):
    """Run one expression against the page's pure functions under Node, as the script loads it with
    no page around it. Returns the JSON of the result."""
    program = (
        "const vm = require('vm'); const fs = require('fs');"
        f"const window = {{}}; vm.runInNewContext(fs.readFileSync({json.dumps(str(SCRIPT_FILE))},"
        "'utf8'), {window});"
        f"const L = window.lessonsLogic; process.stdout.write(JSON.stringify({expression}));"
    )
    done = subprocess.run([NODE, "-e", program], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


PLAN_OPEN = {"plan_status": "proposed", "checks_passed": True,
             "edits": [{"id": "e1"}, {"id": "e2"}, {"id": "e3"}]}


def state(plan, decisions):
    return logic(f"L.approveState({json.dumps(plan)}, {json.dumps(decisions)})")


@needs_node
def test_approve_is_on_only_with_passed_checks_and_at_least_one_accepted_change():
    on = state(PLAN_OPEN, {"e1": "accepted", "e2": "rejected"})
    assert on["enabled"] is True and on["ids"] == ["e1"]
    assert state(PLAN_OPEN, {"e1": "accepted", "e2": "accepted", "e3": "accepted"})["ids"] == [
        "e1", "e2", "e3"]
    nothing_accepted = ({}, {"e1": "rejected", "e2": "rejected", "e3": "rejected"},
                        {"e1": "undecided"})
    for decisions in nothing_accepted:
        off = state(PLAN_OPEN, decisions)
        assert off["enabled"] is False and off["ids"] == [] and off["why"], decisions


@needs_node
def test_approve_is_off_when_the_checks_failed_or_the_plan_is_not_open():
    accepted = {"e1": "accepted"}
    assert state({**PLAN_OPEN, "checks_passed": False}, accepted)["enabled"] is False
    for status in ("approved", "rejected", "superseded", None):
        assert state({**PLAN_OPEN, "plan_status": status}, accepted)["enabled"] is False, status


@needs_node
def test_a_decision_about_a_change_the_plan_does_not_have_is_not_sent():
    got = state(PLAN_OPEN, {"e1": "accepted", "e9": "accepted"})
    assert got["ids"] == ["e1"]


@needs_node
def test_a_plan_with_no_checked_flag_is_treated_as_checked_because_the_store_keeps_no_other():
    plan = {k: v for k, v in PLAN_OPEN.items() if k != "checks_passed"}
    assert state(plan, {"e1": "accepted"})["enabled"] is True


@needs_node
def test_the_two_columns_are_cut_by_code_points_and_always_join_back_to_the_text():
    text = "Hello \U0001F600 world. Ask why."
    changes = [{"edit_id": "e1", "start": 15, "end": 23, "new_start": 15, "new_end": 36}]
    original = logic(f"L.segments({json.dumps(text)}, {json.dumps(changes)}, 'original')")
    assert "".join(s["text"] for s in original) == text
    assert [s["id"] for s in original] == [None, "e1"] and original[1]["text"] == "Ask why."
    revised_text = "Hello \U0001F600 world. Ask why, and say how."
    revised = logic(f"L.segments({json.dumps(revised_text)}, {json.dumps(changes)}, 'revised')")
    assert "".join(s["text"] for s in revised) == revised_text
    assert revised[1]["text"] == "Ask why, and say how."


@needs_node
@pytest.mark.parametrize("changes", [
    [], [{"edit_id": "a", "start": 5, "end": 2, "new_start": 5, "new_end": 2}],
    [{"edit_id": "a", "start": 0, "end": 99, "new_start": 0, "new_end": 99}],
    [{"edit_id": "a", "start": 0, "end": 6, "new_start": 0, "new_end": 6},
     {"edit_id": "b", "start": 3, "end": 9, "new_start": 3, "new_end": 9}],
], ids=["none", "backwards", "past the end", "overlap"])
def test_a_bad_change_list_never_loses_or_repeats_text(changes):
    text = "Read the passage."
    for side in ("original", "revised"):
        parts = logic(f"L.segments({json.dumps(text)}, {json.dumps(changes)}, '{side}')")
        assert "".join(p["text"] for p in parts) == text


@needs_node
def test_the_suggestion_is_read_from_a_plain_string_or_from_the_orchestrators_object():
    assert logic("L.suggestionText({suggestion: 'Ask for a reason.'})") == "Ask for a reason."
    assert logic("L.suggestionText({suggestion: {label: 'x', text: 'Ask for a reason.'}})") == (
        "Ask for a reason.")
    assert logic("L.suggestionText({})") == ""


@needs_node
def test_the_bodies_the_page_sends_hold_only_the_fields_they_should():
    body = logic("L.createBody({stage: 'S', area: 'A', term: '2', objective: 'O', notes: 'N', "
                 "school: 'X', author: 'Y'})")
    assert body == {"stage": "S", "area": "A", "term": 2, "objective": "O", "notes": "N"}
    assert logic("L.approveBody(['e1', 'e2'])") == {"accepted_edit_ids": ["e1", "e2"]}


@needs_node
def test_the_list_is_shown_newest_first_whatever_order_it_arrives_in():
    plans = [{"plan_id": "LP-000001", "created_at": "2026-11-01T10:00:00+00:00"},
             {"plan_id": "LP-000003", "created_at": "2026-11-03T10:00:00+00:00"},
             {"plan_id": "LP-000002", "created_at": "2026-11-03T10:00:00+00:00"}]
    got = logic(f"L.newestFirst({json.dumps(plans)}).map((p) => p.plan_id)")
    assert got == ["LP-000003", "LP-000002", "LP-000001"]
