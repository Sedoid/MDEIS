"""The portal shows why a methodology was turned back (T-051). No database, no browser.

The store is a fake, as in test_portal.py. The page script cannot run here, so what is checked is
what the server sends (the faults as plain JSON text, for administrators and for no one else) and
how the script puts them on the page (as text, never as markup).
"""

import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from test_portal import FakeAuth, FakeStore, as_user, settings  # noqa: E402

from portal import app as portal  # noqa: E402

STATIC = Path(portal.__file__).parent / "static"
SCRIPT = (STATIC / "app.js").read_text(encoding="utf-8")
HOSTILE = '<img src=x onerror="alert(1)"> & <script>alert(2)</script>'

REJECTED = {
    "document_id": "D-000004", "kind": "methodology", "original_name": "new.md",
    "status": "failed", "failure_kind": "methodology_unclear", "failure_page": None,
    "failure_row": None, "duplicate_of": None, "active": False, "acknowledged_by": None,
    "created_at": "2026-11-16T10:00:00+00:00", "uploader_id": "u-ada",
}
FAULTS = [
    {"kind": "vague_term", "ref_id": "2.5", "page": 3, "row": None,
     "detail": 'Give the value for "suitable", or say where it comes from. (GT-4)\n'
               f'Quote: "{HOSTILE}"'},
    {"kind": "required_setting_missing", "ref_id": "3.1", "page": 4, "row": None,
     "detail": 'Define the setting "Evaluation type" in section 3.1, with what it is for. (GT-1)'},
]


class RejectedStore(FakeStore):
    def __init__(self):
        super().__init__()
        self.method = {"in_force": None, "waiting": [], "earlier": [REJECTED]}

    def get(self, document_id):
        return {**REJECTED, "issues": FAULTS} if document_id == "D-000004" else None


@pytest.fixture
def world(tmp_path):
    store = RejectedStore()
    app = portal.create_app(settings(tmp_path), auth=FakeAuth(), store=store)
    return TestClient(app)


def function_body(name):
    start = SCRIPT.index(f"function {name}(")
    end = SCRIPT.index("\n}\n", start)
    return SCRIPT[start:end]


# --------------------------------------------------------------------------- what the server sends
def test_an_administrator_gets_the_faults_of_a_rejected_upload_as_json_text(world):
    reply = as_user(world).get("/api/documents/D-000004")
    assert reply.status_code == 200
    assert reply.headers["content-type"].startswith("application/json")
    issues = reply.json()["issues"]
    assert [i["ref_id"] for i in issues] == ["2.5", "3.1"] and issues[0]["page"] == 3
    assert HOSTILE in issues[0]["detail"], "sent as it is: the page is what makes it safe"
    assert "uploader_id" not in reply.json()


def test_the_panel_lists_the_rejected_upload_with_its_kind_of_failure(world):
    reply = as_user(world).get("/api/methodology")
    assert reply.status_code == 200
    earlier = reply.json()["earlier"]
    shown = [(d["status"], d["failure_kind"]) for d in earlier]
    assert shown == [("failed", "methodology_unclear")]


def test_the_faults_are_for_administrators_and_for_the_uploader_only(world):
    assert world.get("/api/documents/D-000004").status_code == 401
    assert as_user(world, "ben-token").get("/api/documents/D-000004").status_code == 404
    assert as_user(world, "ada-token").get("/api/documents/D-000004").status_code == 200


# --------------------------------------------------------------------------- what the page does
def test_the_page_knows_the_new_kind_of_failure():
    assert "methodology_unclear" in SCRIPT
    first = SCRIPT.index("const FAILURES")
    block = SCRIPT[first : SCRIPT.index("};", first)]
    assert "methodology_unclear:" in block


def test_the_faults_are_put_on_the_page_as_text_and_never_as_markup():
    body = function_body("showFaults")
    for field in ("fault.ref_id", "fault.page", "fault.detail"):
        assert field in body
    assert 'text("strong", where)' in body and 'text("div", fault.detail' in body
    assert re.search(r"\.(innerHTML|outerHTML)\s*=|insertAdjacentHTML|document\.write|eval\(",
                     body) is None


def test_nothing_in_the_script_writes_markup_at_all():
    assert re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(|new Function",
                     SCRIPT) is None


def test_the_panel_asks_for_the_faults_of_the_newest_rejected_upload_only():
    body = function_body("renderMethod")
    assert 'failure_kind === "methodology_unclear"' in body
    assert "showFaults(latest, panel, stamp)" in body
    assert body.count("showFaults(") == 1
    assert "latest.created_at > inForce.created_at" in body


def test_a_late_answer_for_an_older_drawing_of_the_panel_is_dropped():
    assert "if (stamp !== methodStamp) return;" in function_body("showFaults")


def test_the_page_script_keeps_its_addresses_relative():
    assert "/api/" not in SCRIPT


def test_the_wording_has_no_label_and_no_dash():
    body = function_body("showFaults")
    assert chr(0x2014) not in body and chr(0x2013) not in body
    for word in ("weak", "remedial", "gifted", "rank", "pathway"):
        assert word not in body.lower()
