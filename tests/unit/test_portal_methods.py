"""The portal's Methods page (T-073): the server side that the page calls, and the page's own files.

Same shape as `test_portal_lessons.py`. The gateway is replaced by a fake that records what it is
given, and Open WebUI by a fake sign-in check, so nothing here needs a network or Postgres. Held
here: only a signed-in person gets anything; every post needs the CSRF token and the page's own
origin; the person passed on is the one Open WebUI vouched for (never one named in a body); a body
may hold only what the teacher chose or wrote (no learner, no name, no school, no decision field);
the portal holds no model client and no key for one; the page is written the same way as the other
two (no markup built from text, no inline script or style, no outside address); and the page's own
rules (what the buttons need, what the bodies hold) are run under Node when it is there.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from test_portal import ADA, BEN, ORIGIN, FakeAuth, FakeStore, csrf  # noqa: E402

from portal import app as portal  # noqa: E402
from portal import security  # noqa: E402
from portal.settings import MB, Settings  # noqa: E402

STATIC = Path(portal.__file__).parent / "static"
MS = "MS-0001"
ENTRIES = [{"subject": "English", "term": 1, "score": "52"},
           {"subject": "English", "term": 2, "score": "58"}]
TRY = {"stage": "Form 1", "entries": ENTRIES}
CASE = {**TRY, "expected": {"change": "6"}, "agreed": False}
LISTED = [
    ("GET", "/api/measures", None, "/portal/measures"),
    ("GET", f"/api/measures/{MS}/1", None, f"/portal/measures/{MS}/1"),
    ("POST", f"/api/measures/{MS}/1/try", TRY, f"/portal/measures/{MS}/1/try"),
    ("POST", f"/api/measures/{MS}/1/case", CASE, f"/portal/measures/{MS}/1/case"),
    ("POST", f"/api/measures/{MS}/1/confirm", {"note": "checked"},
     f"/portal/measures/{MS}/1/confirm"),
    ("POST", f"/api/measures/{MS}/1/reject", {"note": "no"}, f"/portal/measures/{MS}/1/reject"),
    # T-077: the approver's held list and the clearing of one hold
    ("GET", "/api/measures/held", None, "/portal/measures/held"),
    ("POST", f"/api/measures/{MS}/1/clear-hold", {"note": "read it"},
     f"/portal/measures/{MS}/1/clear-hold"),
]
POSTS = [row for row in LISTED if row[0] == "POST"]


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
@pytest.mark.parametrize("method,path,body,_t", LISTED)
def test_nobody_signed_in_gets_401_and_the_gateway_is_not_called(tmp_path, method, path, body, _t):
    gateway = FakeGateway()
    app = portal.create_app(settings(tmp_path), auth=FakeAuth(), store=FakeStore(), gateway=gateway)
    client = TestClient(app)
    sent = {"json": body} if body is not None else {}
    assert client.request(method, path, headers=ORIGIN, **sent).status_code == 401
    client.cookies.set("token", "not-a-real-token")
    assert client.request(method, path, headers=ORIGIN, **sent).status_code == 401
    assert gateway.calls == []


@pytest.mark.parametrize("method,path,body,target", LISTED)
def test_a_signed_in_person_is_passed_on_with_the_name_open_webui_gave(
    world, method, path, body, target
):
    client, gateway = world
    answer = go(client, method, path, body)
    assert answer.status_code == 200 and answer.json()["marker"] == "from the gateway"
    (call,) = gateway.calls
    assert (call["method"], call["path"]) == (method, target)
    assert (call["user"].id, call["user"].name) == (ADA.id, ADA.name)
    if method == "POST":
        assert call["body"] == body


def test_a_second_person_is_passed_on_as_themselves(world):
    client, gateway = world
    client.cookies.set("token", "ben-token")
    assert client.get("/api/measures").status_code == 200
    assert gateway.calls[0]["user"] == BEN


# --------------------------------------------------------------------------- CSRF and origin
@pytest.mark.parametrize("method,path,body,_t", POSTS)
def test_every_post_needs_the_csrf_token_and_the_pages_own_origin(world, method, path, body, _t):
    client, gateway = world
    good = csrf(client)
    other = csrf(client, "ben-token")
    client.cookies.set("token", "ada-token")
    cases = {
        "no token": {**ORIGIN},
        "another persons token": {"X-CSRF-Token": other, **ORIGIN},
        "a made-up token": {"X-CSRF-Token": "1.abcd", **ORIGIN},
        "no origin": {"X-CSRF-Token": good},
        "a foreign origin": {"X-CSRF-Token": good, "Origin": "https://evil.example"},
    }
    for label, headers in cases.items():
        assert client.post(path, json=body, headers=headers).status_code == 403, label
    assert gateway.calls == []


@pytest.mark.parametrize("method,path,body,_t", POSTS)
def test_an_old_csrf_token_is_refused(world, method, path, body, _t):
    client, gateway = world
    old = security.csrf_token("portal-secret-not-real", ADA.id, now=1_000_000)
    assert client.post(path, json=body, headers={"X-CSRF-Token": old, **ORIGIN}).status_code == 403
    assert gateway.calls == []


def test_reading_needs_no_csrf_token_but_still_needs_a_sign_in(world):
    client, _ = world
    assert client.get("/api/measures").status_code == 200


# --------------------------------------------------------------------------- closed bodies
NAMING = ["approver", "approver_id", "approver_name", "user", "user_id", "name", "teacher",
          "teacher_name", "school", "school_code", "status", "decision", "measure_id", "version",
          "learner_id", "approver_token", "model"]


@pytest.mark.parametrize("field", NAMING)
def test_no_body_can_name_a_person_a_school_a_status_or_a_decision(world, field):
    client, gateway = world
    for _, path, body, _t in POSTS:
        assert post(client, path, {**body, field: "x"}).status_code == 400, path
    assert gateway.calls == []


@pytest.mark.parametrize("field", ["learner_id", "name", "school", "approver", "id"])
def test_an_entry_holds_a_subject_a_term_and_a_score_and_nothing_that_names_anyone(world, field):
    client, gateway = world
    entry = {**ENTRIES[0], field: "x"}
    assert post(client, f"/api/measures/{MS}/1/try", {**TRY, "entries": [entry]}).status_code == 400
    wrong = {**CASE, "entries": [entry]}
    assert post(client, f"/api/measures/{MS}/1/case", wrong).status_code == 400
    assert gateway.calls == []


@pytest.mark.parametrize("path,body", [
    (f"/api/measures/{MS}/1/try", {"stage": 3, "entries": ENTRIES}),
    (f"/api/measures/{MS}/1/try", {"stage": "Form 1", "entries": "52"}),
    (f"/api/measures/{MS}/1/try", {"stage": "Form 1", "entries": ["52"]}),
    (f"/api/measures/{MS}/1/try", {"stage": "Form 1"}),
    (f"/api/measures/{MS}/1/case", {**TRY}),
    (f"/api/measures/{MS}/1/case", {**TRY, "expected": "6"}),
    (f"/api/measures/{MS}/1/case", {**CASE, "agreed": "yes"}),
    (f"/api/measures/{MS}/1/confirm", {"note": 5}),
    (f"/api/measures/{MS}/1/reject", {}),
    (f"/api/measures/{MS}/1/reject", {"note": ""}),
    (f"/api/measures/{MS}/1/reject", {"note": "   "}),
    (f"/api/measures/{MS}/1/clear-hold", {}),
    (f"/api/measures/{MS}/1/clear-hold", {"note": ""}),
    (f"/api/measures/{MS}/1/clear-hold", {"note": "   "}),
    (f"/api/measures/{MS}/1/clear-hold", {"note": 5}),
    (f"/api/measures/{MS}/1/clear-hold", {"note": None}),
])
def test_a_body_of_the_wrong_shape_is_a_plain_400_and_is_not_sent_on(world, path, body):
    client, gateway = world
    assert post(client, path, body).status_code == 400
    assert gateway.calls == []


@pytest.mark.parametrize("raw", [b"not json", b"[]", b"null", b'"x"'])
def test_a_body_that_is_not_a_json_object_is_a_plain_400(world, raw):
    client, gateway = world
    headers = {"X-CSRF-Token": csrf(client), **ORIGIN}
    for _, path, _b, _t in POSTS:
        assert client.post(path, content=raw, headers=headers).status_code == 400, path
    assert gateway.calls == []


def test_a_confirm_takes_an_empty_body_or_a_note(world):
    client, gateway = world
    headers = {"X-CSRF-Token": csrf(client), **ORIGIN}
    assert client.post(f"/api/measures/{MS}/1/confirm", headers=headers).status_code == 200
    assert post(client, f"/api/measures/{MS}/1/confirm", {}).status_code == 200
    assert [c["body"] for c in gateway.calls] == [{}, {}]


def test_a_body_over_the_portals_size_cap_is_refused_before_anything_is_sent(tmp_path):
    gateway = FakeGateway()
    small = settings(tmp_path, max_markdown_bytes=1000)
    app = portal.create_app(small, auth=FakeAuth(), store=FakeStore(), gateway=gateway)
    client = TestClient(app)
    client.cookies.set("token", "ada-token")
    big = {"note": "x" * 5000}
    assert post(client, f"/api/measures/{MS}/1/reject", big).status_code == 413
    assert gateway.calls == []


@pytest.mark.parametrize("measure,version", [
    ("MS-1", "1"), ("ms-0001", "1"), ("MS-0001x", "1"), ("x", "1"),
    (MS, "0"), (MS, "x"), (MS, "-1"), (MS, "1.5"),
])
def test_a_measure_number_or_version_that_is_not_one_of_ours_is_404_and_not_sent_on(
    world, measure, version
):
    client, gateway = world
    base = f"/api/measures/{measure}/{version}"
    assert client.get(base).status_code == 404
    for tail, body in (("try", TRY), ("case", CASE), ("confirm", {}), ("reject", {"note": "n"})):
        assert post(client, f"{base}/{tail}", body).status_code == 404
    assert gateway.calls == []


# --------------------------------------------------------------------------- what comes back
def test_what_the_gateway_says_is_handed_back_with_its_status(world):
    client, gateway = world
    refusal = {"status": "refused", "reason": "approval_conditions_not_met", "detail": "plain",
               "gaps": [{"code": "no_independent_test", "text": "x"}]}
    gateway.answer = (409, refusal)
    answer = post(client, f"/api/measures/{MS}/1/confirm", {})
    assert answer.status_code == 409 and answer.json() == refusal


def test_an_answer_that_is_not_an_object_is_a_plain_502(world):
    client, gateway = world
    gateway.answer = (200, ["not", "an", "object"])
    answer = client.get("/api/measures")
    assert answer.status_code == 502 and answer.json()["reason"] == "service_unavailable"


def test_with_no_gateway_key_the_methods_routes_say_so_and_call_nothing(tmp_path):
    app = portal.create_app(settings(tmp_path), auth=FakeAuth(), store=FakeStore())
    client = TestClient(app)
    client.cookies.set("token", "ada-token")
    for method, path, body, _t in LISTED:
        answer = go(client, method, path, body)
        assert answer.status_code == 503 and answer.json()["reason"] == "methods_off"


def test_the_portal_still_holds_no_model_client_no_secret_and_no_route_to_the_human_door():
    folder = Path(portal.__file__).parent
    text = "".join(p.read_text() for p in folder.rglob("*.py"))
    for forbidden in ("openai", "anthropic", "langchain", "langgraph", "OPENROUTER", "complete(",
                      "APPROVER_TOKEN_SECRET", "HUMAN_ROUTE_KEY", "ORCHESTRATOR", "/human/"):
        assert forbidden not in text, forbidden


# --------------------------------------------------------------------------- the page's files
SCRIPT_FILE = STATIC / "methods.js"
SCRIPT = SCRIPT_FILE.read_text()
PAGE = (STATIC / "index.html").read_text()
APP = (STATIC / "app.js").read_text()
CSS = (STATIC / "style.css").read_text()
VIEW = PAGE[PAGE.index('<section id="view-methods"'):]
VIEW = VIEW[: VIEW.index("</section>")]


def test_the_page_loads_the_methods_script_the_same_way_as_the_others():
    assert PAGE.count("<script src=") == 3
    assert '<script src="static/methods.js" defer></script>' in PAGE
    assert PAGE.index("static/app.js") < PAGE.index("static/lessons.js") < PAGE.index(
        "static/methods.js")
    for bad in ("<style", "onclick=", "onload=", "onsubmit=", "javascript:"):
        assert bad not in PAGE
    for bad in ("http://", "https://", "javascript:", "<script", "<style", "style="):
        assert bad not in VIEW
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


def test_the_page_names_the_methods_view_and_the_nav_link_beside_files_and_lessons():
    assert 'id="view-methods"' in PAGE and 'href="#/methods"' in PAGE
    assert 'data-view="methods"' in PAGE and 'id="methods-count"' in PAGE
    nav = PAGE[PAGE.index('<nav class="nav"'):PAGE.index("</nav>")]
    assert nav.index('data-view="files"') < nav.index('data-view="lessons"') < nav.index(
        'data-view="methods"') < nav.index('data-view="chat"')
    assert '"methods"' in APP and "methodsView" in APP
    assert "Methods" in nav


def test_the_view_says_synthetic_data_only_and_has_no_field_for_a_learner_or_a_name():
    assert "ynthetic" in VIEW
    assert not re.search(r'<input[^>]*name="(learner|name|id|school)', VIEW)
    for word in ("learner_id", "learnerId", "student", "pupil"):
        assert word not in SCRIPT, word


def function_source(name):
    start = SCRIPT.index(f"function {name}")
    line = SCRIPT.rindex("\n", 0, start) + 1
    indent = SCRIPT[line:start]
    indent = indent[: len(indent) - len(indent.lstrip())]
    return SCRIPT[start: SCRIPT.index("\n" + indent + "}\n", start)]


def test_the_only_things_the_page_sends_are_what_the_bodies_make():
    for name, keys in (("tryBody", ["stage", "entries", "subject", "term", "score"]),
                       ("caseBody", ["expected", "agreed"]), ("noteBody", ["note"])):
        body = function_source(name)
        assert all(k in body for k in keys), name
        sends = r"approver|author|school|user|status|decision|token|learner"
        assert not re.search(sends, body), name
    assert SCRIPT.count("JSON.stringify(") == 1, "one place sends a body"
    assert SCRIPT.count("X-CSRF-Token") == 1 and "X-CSRF-Token" in function_source("postJson")
    assert SCRIPT.count("api.call(") == 2, "one for reading and one for posting"


def test_the_page_posts_only_what_the_bodies_make():
    calls = re.findall(r"(?<!function )\bpostJson\(([^;]*)\);", SCRIPT)
    assert calls, "the page posts"
    for sent in calls:
        assert re.search(r"tryBody\(|caseBody\(|noteBody\(|rejectBody\(", sent), sent


def test_the_page_shows_what_the_brief_lists():
    for text in ("Where it comes from", "Checks", "Worked examples", "Try your own",
                 "waiting for your review", "PLACEHOLDER", "Confirm", "Reject",
                 "will be recorded", "my expected answer"):
        assert text in SCRIPT, text
    for key in ("quotes", "clauses", "skipped", "build_anyway", "review", "checks", "examples",
                "approval", "gaps", "values_used", "answer"):
        assert key in SCRIPT, key


def test_the_page_never_offers_an_approve_that_the_server_has_not_allowed():
    shown = function_source("confirmState")
    assert "can_confirm" in shown and "awaiting_demo" in shown


def test_the_page_names_no_person_in_a_body_and_says_who_the_name_will_be():
    assert "api.me.name" in SCRIPT
    assert "name will be recorded" in SCRIPT or "will be recorded" in SCRIPT


def test_the_page_has_no_label_rank_or_pathway_wording():
    for word in ("weak", "remedial", "gifted", "lazy", "slow", "failing", "brilliant", "rank",
                 "top group", "bottom group", "career", "pathway", "subject choice"):
        assert not re.search(rf"\b{word}\b", SCRIPT + VIEW, re.IGNORECASE), word


def test_the_start_up_loop_still_skips_every_boxed_form_that_has_no_file_field():
    forms = re.findall(r"<form\b([^>]*)>(.*?)</form>", PAGE, flags=re.S)
    boxed = [(a, b) for a, b in forms if re.search(r'class="[^"]*\bbox\b', a)]
    not_uploads = [a for a, b in boxed if 'type="file"' not in b]
    assert not_uploads
    start_up = APP[APP.index('addEventListener("DOMContentLoaded"'):]
    loop = start_up[start_up.index('querySelectorAll(".box")'):][:400]
    assert "if (!form.elements.file) continue;" in loop


def test_the_stylesheet_has_the_page_s_classes():
    for name in (".methods-", ".method-state"):
        assert name in CSS, name


# --------------------------------------------------------------------------- the page's rules, run
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node is not installed: the page's rules were "
                                "not run (this is a skip, not a pass)")


def logic(expression: str):
    program = (
        "const vm = require('vm'); const fs = require('fs');"
        f"const window = {{}}; vm.runInNewContext(fs.readFileSync({json.dumps(str(SCRIPT_FILE))},"
        "'utf8'), {window});"
        f"const L = window.methodsLogic; process.stdout.write(JSON.stringify({expression}));"
    )
    done = subprocess.run([NODE, "-e", program], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def view(**over):
    base = {"version_status": "awaiting_demo", "approval": {"can_confirm": True, "gaps": []}}
    base.update(over)
    return base


@needs_node
def test_a_try_body_holds_the_stage_and_the_filled_rows_with_the_term_as_a_number():
    rows = [{"subject": "English", "term": "1", "score": " 52 "},
            {"subject": "English", "term": "2", "score": "58"},
            {"subject": "English", "term": "3", "score": ""},
            {"subject": "English", "term": "", "score": ""}]
    got = logic(f"L.tryBody('Form 1', {json.dumps(rows)})")
    assert got == {"stage": "Form 1", "entries": [
        {"subject": "English", "term": 1, "score": "52"},
        {"subject": "English", "term": 2, "score": "58"}]}


@needs_node
def test_a_body_made_from_extra_fields_on_a_row_holds_only_the_three_fields():
    rows = [{"subject": "English", "term": "1", "score": "52", "learner": "L-1", "name": "A"}]
    got = logic(f"L.tryBody('Form 1', {json.dumps(rows)})")
    assert set(got["entries"][0]) == {"subject", "term", "score"}
    assert set(got) == {"stage", "entries"}


@needs_node
def test_a_case_body_adds_the_expected_numbers_without_blanks_and_the_agreed_flag():
    rows = [{"subject": "English", "term": "1", "score": "60"},
            {"subject": "English", "term": "2", "score": "70"}]
    got = logic(f"L.caseBody('Form 1', {json.dumps(rows)}, "
                "{change: ' 10 ', threshold_met: '', scores_used: '2'}, 1)")
    assert got["expected"] == {"change": "10", "scores_used": "2"} and got["agreed"] is True
    assert set(got) == {"stage", "entries", "expected", "agreed"}
    again = logic(f"L.caseBody('Form 1', {json.dumps(rows)}, {{change: '10'}}, false)")
    assert again["agreed"] is False


@needs_node
def test_a_decision_body_holds_the_note_or_nothing():
    assert logic("L.noteBody('  checked it  ')") == {"note": "checked it"}
    assert logic("L.noteBody('   ')") == {}
    assert logic("L.noteBody('')") == {}
    assert logic("L.noteBody(undefined)") == {}


@needs_node
def test_confirm_is_on_only_when_the_server_says_it_can_be_and_the_version_is_at_the_demo():
    assert logic(f"L.confirmState({json.dumps(view())})")["enabled"] is True
    held = view(approval={"can_confirm": False, "gaps": [{"code": "no_independent_test",
                                                           "text": "No test from outside."}]})
    off = logic(f"L.confirmState({json.dumps(held)})")
    assert off["enabled"] is False and "No test from outside." in off["why"]
    for status in ("approved", "rejected", "awaiting_review", None):
        shown = logic(f"L.confirmState({json.dumps(view(version_status=status))})")
        assert shown["enabled"] is False


@needs_node
def test_reject_needs_a_note_and_a_version_at_the_demo():
    assert logic(f"L.rejectState({json.dumps(view())}, 'does not match 6.4')")["enabled"] is True
    for note in ("", "   ", None):
        shown = logic(f"L.rejectState({json.dumps(view())}, {json.dumps(note)})")
        assert shown["enabled"] is False and shown["why"]
    done = view(version_status="approved")
    assert logic(f"L.rejectState({json.dumps(done)}, 'x')")["enabled"] is False


@needs_node
def test_the_waiting_count_is_said_in_plain_words_for_none_one_and_many():
    assert logic("L.waitingText(0)") == "Nothing is waiting for your review."
    assert logic("L.waitingText(1)") == "1 measure is waiting for your review."
    assert logic("L.waitingText(3)") == "3 measures are waiting for your review."
    assert logic("L.waitingText(undefined)") == "Nothing is waiting for your review."


@needs_node
def test_an_example_is_said_to_match_not_match_or_not_run_never_blank():
    assert "Matches" in logic("L.exampleWord({match: true})")
    assert "Does not match" in logic("L.exampleWord({match: false})")
    assert "not run" in logic("L.exampleWord({match: null, problem: 'runner_unavailable'})")


@needs_node
def test_a_value_is_called_a_placeholder_until_a_teacher_has_confirmed_it():
    placeholder = logic("L.stateWord({state: 'placeholder', confirmed_by: null})")
    assert placeholder.startswith("PLACEHOLDER")
    assert "Ms Teacher" in logic("L.stateWord({state: 'confirmed', confirmed_by: 'Ms Teacher'})")
    assert "rulebook" in logic("L.stateWord({state: 'in_rulebook'})").lower()
    assert "missing" in logic("L.stateWord({state: 'missing'})").lower()


@needs_node
def test_the_answer_is_listed_field_by_field_in_a_fixed_order_with_the_exact_form():
    answer = {"threshold_met": {"value": "1.00", "exact": "1"},
              "change": {"value": "11.00", "exact": "11"}}
    got = logic(f"L.answerLines({json.dumps(answer)})")
    assert got == ["change: 11.00 (exact 11)", "threshold_met: 1.00 (exact 1)"]


# --------------------------------------------------------------------------- T-077: held versions
def test_a_clear_hold_with_no_body_at_all_is_a_plain_400_and_is_not_sent_on(world):
    client, gateway = world
    headers = {"X-CSRF-Token": csrf(client), **ORIGIN}
    assert client.post(f"/api/measures/{MS}/1/clear-hold", headers=headers).status_code == 400
    assert gateway.calls == []


def test_the_held_list_is_not_taken_for_a_measure_and_an_unknown_action_is_404(world):
    client, gateway = world
    assert client.get("/api/measures/held").status_code == 200
    assert gateway.calls[-1]["path"] == "/portal/measures/held"
    assert post(client, f"/api/measures/{MS}/1/clear_hold", {"note": "n"}).status_code == 404
    assert post(client, f"/api/measures/{MS}/1/approve-hold", {"note": "n"}).status_code == 404
    assert len(gateway.calls) == 1


def test_a_clear_hold_note_is_passed_on_as_written_and_nothing_else(world):
    client, gateway = world
    answer = post(client, f"/api/measures/{MS}/1/clear-hold", {"note": "read section 6.4"})
    assert answer.status_code == 200
    (call,) = gateway.calls
    assert call["body"] == {"note": "read section 6.4"}
    assert (call["user"].id, call["user"].name) == (ADA.id, ADA.name)


def test_the_portal_names_no_registry_function_for_a_hold():
    text = Path(portal.__file__).read_text("utf-8")
    for word in ("clear_hold", "lift_hold", "list_held", "record_review", "mdeis.registry"):
        assert word not in text, word


def test_the_page_has_a_held_block_that_reads_the_held_list_and_clears_with_a_note():
    for text in ("Held by the automated review", "Clear the hold", "api/measures/held",
                 "clear-hold", "approves nothing", "clearHoldState", "heldText"):
        assert text in SCRIPT, text
    assert "innerHTML" not in function_source("heldBlock") + function_source("heldItem")
    clearing = function_source("clearHold(")
    assert "postJson(" in clearing and "noteBody(" in clearing
    assert not re.search(r"approver|school|user|status|decision|token|learner", function_source(
        "clearHoldState"))


def test_the_page_says_a_cleared_hold_approves_nothing_and_goes_to_the_demo():
    shown = function_source("heldBlock")
    assert "approves nothing" in shown and "demo" in shown and "teacher" in shown
    assert "decides" in shown


def test_the_held_block_is_made_by_the_script_and_adds_nothing_to_the_page_file():
    assert "methods-held" in SCRIPT
    assert "methods-held" not in PAGE, "the container is made by methods.js, not by index.html"


def test_the_held_block_has_no_label_rank_or_pathway_wording():
    block = "".join(function_source(n) for n in ("heldBlock", "clearHold(", "heldText"))
    for word in ("weak", "remedial", "gifted", "rank", "career", "pathway", "subject choice"):
        assert not re.search(rf"\b{word}\b", block, re.IGNORECASE), word


@needs_node
def test_a_hold_can_be_cleared_only_when_a_note_is_written():
    assert logic("L.clearHoldState('read section 6.4')")["enabled"] is True
    for note in ("", "   ", None):
        shown = logic(f"L.clearHoldState({json.dumps(note)})")
        assert shown["enabled"] is False and shown["why"]
    assert logic("L.clearHoldState(undefined)")["enabled"] is False


@needs_node
def test_the_held_count_is_said_in_plain_words_for_none_one_and_many():
    assert logic("L.heldText(0)") == "Nothing is held by the automated review."
    assert logic("L.heldText(1)") == "1 version is held by the automated review."
    assert logic("L.heldText(4)") == "4 versions are held by the automated review."
    assert logic("L.heldText(undefined)") == "Nothing is held by the automated review."
