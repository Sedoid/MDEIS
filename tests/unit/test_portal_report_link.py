"""The portal page opened from a link in the chat (T-076): `#/files?report=R-000001`.

When the Files page is opened with a report number in the address, the page puts that number in the
report field and, once sign-in is confirmed, starts the existing download ONCE, through the same
`call()` and the same CSRF header, and says the result on the existing message line (a success or
the plain refusal). The button stays for manual use. Nothing in the page is inline, and nothing of
one person's session stays on screen after a sign-out.

Two kinds of test. The first reads the page files (no Node needed). The second runs `app.js` in
Node's `vm` with a small fake page and a fake `fetch`, and records what it asked for; it is skipped
without Node (a skip is not a pass), as the Methods page tests are.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from portal import app as portal  # noqa: E402

STATIC = Path(portal.__file__).parent / "static"
APP_FILE = STATIC / "app.js"
PAGE = (STATIC / "index.html").read_text("utf-8")
APP = APP_FILE.read_text("utf-8")


def source_of(name):
    start = APP.index(name)
    return APP[start: APP.index("\n}\n", start)]


# --------------------------------------------------------------------------- the files, read
def test_the_page_still_has_no_inline_script_or_style_and_one_fetch():
    assert "<script src=" in PAGE and "<link" in PAGE
    for bad in ("<style", "onclick=", "onload=", "javascript:", "http://", "https://"):
        assert bad not in PAGE
    assert re.findall(r"<script(?![^>]*\bsrc=)", PAGE) == [], "every script is a file"
    assert "innerHTML" not in APP and "eval(" not in APP
    assert APP.count("fetch(") == 1, "every call still goes through call()"


def test_the_address_is_read_with_the_same_strict_number_pattern_as_the_download():
    body = source_of("function reportInAddress")
    assert "location.hash" in body and "decodeURIComponent" in body
    assert "REPORT_NUMBER.test(" in body
    assert 'const REPORT_NUMBER = /^R-[0-9]{6,}$/;' in APP


def test_the_view_name_is_taken_before_the_question_mark():
    assert '.split("?")[0]' in source_of("function route")


def test_route_prefills_and_asks_only_for_the_files_view():
    body = source_of("function route")
    assert 'if (name === "files") openReportFromAddress();' in body


def test_the_download_starts_once_after_sign_in_is_confirmed_and_the_address_is_cleaned():
    body = source_of("async function askForWantedReport")
    assert "if (!wantedReport || !csrf || !me) return;" in body
    assert body.index("wantedReport = null;") < body.index("await downloadReport();")
    assert 'history.replaceState(null, "", "#/files");' in body
    assert body.count("downloadReport(") == 1
    start = source_of("async function start")
    assert start.index('csrf = (await get("api/csrf")).body.csrf;') < start.index(
        "await askForWantedReport();")
    assert start.count("askForWantedReport()") == 1


def test_the_existing_button_and_its_handler_are_unchanged():
    assert '$("report-form").addEventListener("submit", downloadReport);' in APP
    body = source_of("async function downloadReport")
    assert "if (event) event.preventDefault();" in body
    assert 'call("api/reports/"' in body and '"X-CSRF-Token": csrf' in body
    assert "asFile: true" in body and "REPORT_NUMBER.test(number)" in body
    assert "innerHTML" not in body


def test_a_sign_out_forgets_the_waiting_number_the_field_and_the_message():
    body = source_of("function showSignIn")
    assert "wantedReport = null;" in body
    assert '$("report-id").value = ""' in body and '$("report-message").textContent = ""' in body


def test_the_page_makes_no_address_for_a_file_itself_the_link_target_is_the_pages_own_hash():
    """The link is made by the system in the chat. The page only reads the hash it was opened
    with and downloads through the portal's own route."""
    assert "/portal/" not in APP and "http://" not in APP and "https://" not in APP
    assert APP.count('call("api/reports/"') == 1


# --------------------------------------------------------------------------- the page, run
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(
    NODE is None, reason="Node is not installed: the page was not run (a skip, not a pass)")

HARNESS = r"""
(async () => {
  const vm = require("vm"), fs = require("fs");
  const scenario = JSON.parse(process.env.SCENARIO);
  const source = fs.readFileSync(process.env.APP_JS, "utf8");
  const out = { fetches: [], downloads: [], replaced: [] };
  const els = {};
  function element(id) {
    const listeners = {}, classes = new Set();
    return {
      id, value: "", textContent: "", hidden: false, disabled: false, className: "", href: "",
      src: "", title: "", scrollTop: 0, dataset: {}, elements: {}, listeners,
      classList: {
        toggle(name, on) {
          const want = on === undefined ? !classes.has(name) : on;
          if (want) classes.add(name); else classes.delete(name);
          return want;
        },
        contains: (name) => classes.has(name), add: (n) => classes.add(n),
        remove: (n) => classes.delete(n),
      },
      addEventListener(name, fn) { (listeners[name] = listeners[name] || []).push(fn); },
      setAttribute() {}, removeAttribute() {}, appendChild() {}, replaceChildren() {}, focus() {},
      remove() {},
      click() { if (this.download) out.downloads.push({ file: this.download, href: this.href }); },
    };
  }
  const $el = (id) => (els[id] = els[id] || element(id));
  const docListeners = {}, winListeners = {};
  const document = {
    getElementById: $el, createElement: (tag) => element("<" + tag + ">"),
    createTextNode: (t) => ({ t }), querySelectorAll: () => [], title: "",
    documentElement: { dataset: {} }, body: { appendChild() {} },
    addEventListener(name, fn) { (docListeners[name] = docListeners[name] || []).push(fn); },
  };
  const location = { hash: scenario.hash };
  const window = {
    addEventListener(name, fn) { (winListeners[name] = winListeners[name] || []).push(fn); },
    matchMedia: () => ({ matches: false }),
  };
  const history = {
    replaceState(state, title, url) {
      out.replaced.push(url);
      location.hash = "#" + String(url).split("#")[1];
    },
  };
  const localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  const answers = scenario.answers;
  async function fetch(path, init) {
    init = init || {};
    const headers = init.headers || {};
    out.fetches.push({ path, method: init.method || "GET", csrf: headers["X-CSRF-Token"] || null });
    const [status, body] = answers[path] || [200, {}];
    return {
      ok: status >= 200 && status < 300, status, json: async () => body,
      blob: async () => ({ blob: true, of: path }),
    };
  }
  const context = vm.createContext({
    document, window, location, history, localStorage, fetch, console,
    URL: { createObjectURL: () => "blob:fake", revokeObjectURL() {} },
    setTimeout: () => 0, clearTimeout() {},
  });
  vm.runInContext(source, context);
  const settle = () => new Promise((resolve) => setTimeout(resolve, 30));
  for (const fn of docListeners.DOMContentLoaded || []) fn();
  await settle();
  for (const step of scenario.steps || []) {
    if (step.answers) Object.assign(answers, step.answers);
    if (step.type !== undefined) $el("report-id").value = step.type;
    if (step.hash !== undefined) {
      location.hash = step.hash;
      for (const fn of winListeners.hashchange || []) fn();
    }
    if (step.submit) {
      for (const fn of $el("report-form").listeners.submit || []) await fn({ preventDefault() {} });
    }
    if (step.signin) {
      for (const fn of $el("signin-form").listeners.submit || []) await fn({ preventDefault() {} });
    }
    if (step.signout) for (const fn of $el("signout").listeners.click || []) await fn();
    await settle();
  }
  out.reportId = $el("report-id").value;
  out.message = $el("report-message").textContent;
  out.messageClass = $el("report-message").className;
  out.hash = location.hash;
  out.filesHidden = $el("view-files").hidden;
  out.uploadHidden = $el("view-upload").hidden;
  out.chatHidden = $el("view-chat").hidden;
  out.shellHidden = $el("shell").hidden;
  out.signinHidden = $el("signin").hidden;
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})();
"""

SIGNED_IN = {
    "api/config": [200, {"chat_url": "http://chat.example", "frame_chat": False}],
    "api/me": [200, {"name": "Ada Teacher", "admin": False}],
    "api/csrf": [200, {"csrf": "csrf-1"}],
    "api/chat-session": [200, {}],
    "api/documents": [500, {}],  # the file list is not what is tested: refresh stops here
    "api/signin": [200, {}],
    "api/signout": [200, {}],
}
PDF_1 = "api/reports/R-000001/pdf"
PDF_2 = "api/reports/R-000002/pdf"
PDF_3 = "api/reports/R-000003/pdf"
GOOD = [200, {}]
NOT_RELEASED = [409, {"status": "refused", "reason": "report_not_released",
                      "detail": "Only a released report can be exported."}]
NOT_FOUND = [404, {"status": "refused", "reason": "report_not_found",
                   "detail": "There is no such report."}]


def page(hash_, answers=None, steps=None):
    scenario = {"hash": hash_, "answers": {**SIGNED_IN, **(answers or {})}, "steps": steps or []}
    done = subprocess.run(
        [NODE, "-e", HARNESS], capture_output=True, text=True, timeout=60,
        env={**os.environ, "SCENARIO": json.dumps(scenario), "APP_JS": str(APP_FILE)},
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def asked_for(out):
    return [f for f in out["fetches"] if f["path"].endswith("/pdf")]


@needs_node
def test_a_link_with_a_report_number_prefills_the_field_and_downloads_once_as_a_post_with_csrf():
    out = page("#/files?report=R-000001", {PDF_1: GOOD})
    (asked,) = asked_for(out)
    assert asked == {"path": PDF_1, "method": "POST", "csrf": "csrf-1"}
    assert out["downloads"] == [{"file": "R-000001.pdf", "href": "blob:fake"}]
    assert out["reportId"] == "R-000001"
    assert out["message"] == "Downloaded R-000001.pdf." and out["messageClass"] == "message"
    assert out["filesHidden"] is False and out["uploadHidden"] is True


@needs_node
def test_the_download_waits_for_sign_in_to_be_confirmed_and_the_token_to_be_read():
    out = page("#/files?report=R-000001", {PDF_1: GOOD})
    paths = [f["path"] for f in out["fetches"]]
    assert paths.index("api/me") < paths.index("api/csrf") < paths.index(PDF_1)


@needs_node
def test_the_address_is_cleaned_so_a_refresh_does_not_download_again():
    out = page("#/files?report=R-000001", {PDF_1: GOOD})
    assert out["replaced"] == ["#/files"] and out["hash"] == "#/files"


@needs_node
def test_a_refusal_is_said_plainly_on_the_message_line_and_nothing_is_downloaded():
    out = page("#/files?report=R-000001", {PDF_1: NOT_RELEASED})
    assert len(asked_for(out)) == 1 and out["downloads"] == []
    assert out["message"] == "Only a released report can be exported."
    assert out["messageClass"] == "message bad"
    assert out["reportId"] == "R-000001"


@needs_node
def test_an_unknown_report_is_said_plainly_too():
    out = page("#/files?report=R-000001", {PDF_1: NOT_FOUND})
    assert out["message"] == "There is no such report." and out["downloads"] == []


@needs_node
def test_a_person_who_is_not_signed_in_asks_for_nothing_and_sees_nothing_of_the_link():
    out = page("#/files?report=R-000001", {"api/me": [401, {}]})
    assert asked_for(out) == [] and out["downloads"] == []
    assert "api/csrf" not in [f["path"] for f in out["fetches"]]
    assert out["shellHidden"] is True and out["signinHidden"] is False
    assert out["reportId"] == "", "the number is not left in the field before sign-in"


@needs_node
def test_after_a_later_sign_in_the_link_downloads_once():
    out = page(
        "#/files?report=R-000001", {"api/me": [401, {}], PDF_1: GOOD},
        steps=[{"answers": {"api/me": [200, {"name": "Ada Teacher", "admin": False}]},
                "signin": True}],
    )
    assert len(asked_for(out)) == 1
    assert out["downloads"] == [{"file": "R-000001.pdf", "href": "blob:fake"}]


@needs_node
def test_going_to_the_files_page_again_does_not_download_again_but_a_new_link_does():
    out = page(
        "#/files?report=R-000001", {PDF_1: GOOD, PDF_2: GOOD},
        steps=[{"hash": "#/files"}, {"hash": "#/files?report=R-000002"}],
    )
    assert [f["path"] for f in asked_for(out)] == [PDF_1, PDF_2]
    assert [d["file"] for d in out["downloads"]] == ["R-000001.pdf", "R-000002.pdf"]
    assert out["reportId"] == "R-000002"


@needs_node
@pytest.mark.parametrize("address", [
    "#/files?report=R-12", "#/files?report=r-000001", "#/files?report=R-000001x",
    "#/files?report=R-000001%0A", "#/files?report=..%2F..%2Fx", "#/files?report=",
    "#/files?report=%E0%A4%A", "#/files?other=R-000001", "#/files",
])
def test_a_missing_or_malformed_number_in_the_address_asks_for_nothing_and_fills_nothing(address):
    out = page(address)
    assert asked_for(out) == [] and out["downloads"] == []
    assert out["reportId"] == "" and out["message"] == ""
    assert out["filesHidden"] is False, "the Files page still opens"


@needs_node
def test_a_number_in_the_address_of_another_page_is_ignored():
    out = page("#/chat?report=R-000001", {PDF_1: GOOD})
    assert asked_for(out) == [] and out["chatHidden"] is False and out["reportId"] == ""


@needs_node
def test_the_files_page_is_chosen_by_the_name_before_the_question_mark():
    assert page("#/files?report=R-000001", {PDF_1: GOOD})["uploadHidden"] is True
    assert page("")["uploadHidden"] is False, "no address still opens Upload"


@needs_node
def test_the_button_still_works_by_hand_and_checks_the_number_first():
    out = page(
        "#/files", {PDF_3: GOOD},
        steps=[{"submit": True}, {"type": "r-000003", "submit": True}],
    )
    assert [f["path"] for f in asked_for(out)] == [PDF_3]
    assert out["downloads"] == [{"file": "R-000003.pdf", "href": "blob:fake"}]
    first = page("#/files", steps=[{"submit": True}])
    assert asked_for(first) == []
    assert first["message"] == "Enter a report number such as R-000001."
    assert first["messageClass"] == "message bad"


@needs_node
def test_a_sign_out_clears_the_number_and_the_message_and_shows_the_sign_in():
    out = page("#/files?report=R-000001", {PDF_1: GOOD}, steps=[{"signout": True}])
    assert out["reportId"] == "" and out["message"] == ""
    assert out["shellHidden"] is True and out["signinHidden"] is False


@needs_node
def test_a_link_no_longer_in_the_address_is_not_replayed_for_the_next_person_to_sign_in():
    out = page(
        "#/files?report=R-000001", {"api/me": [401, {}], PDF_1: GOOD},
        steps=[{"hash": "#/upload"},
               {"answers": {"api/me": [200, {"name": "Ben Staff", "admin": False}]},
                "signin": True}],
    )
    assert asked_for(out) == [], "the address no longer named a report when the next person came"
