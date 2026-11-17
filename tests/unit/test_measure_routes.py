"""The teacher's human-only routes for a built measure (T-073), on the tool server. No database and
no network: the demo store is replaced by a fake that records what it is given, and the routes are
mounted on a plain Starlette app (the same way `test_human_routes.py` does it for lessons).

What is held here: every route needs the shared key AND a good signed token, and refuses with a
typed reason and a diary row otherwise; the person and the school come from the token and from
nowhere else; the bodies are closed (no field can name a person, a school or a status); a
refusal from the store becomes a typed reason with its HTTP status; the routes are off when the
key is not set. The real store is driven in `test_measure_demo_db.py`.
"""

import json
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("starlette")

from starlette.applications import Starlette  # noqa: E402
from starlette.routing import Route  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from mdeis import human_routes, measure_routes  # noqa: E402
from mdeis.core import approver_token  # noqa: E402
from mdeis.db import diary  # noqa: E402

SECRET = "approver-secret-not-real"
KEY = "human-key-not-real"
MS = "MS-0001"
OPEN = f"/human/measures/{MS}/1"
ENTRIES = [{"subject": "English", "term": 1, "score": "52"},
           {"subject": "English", "term": 2, "score": "58"},
           {"subject": "English", "term": 3, "score": "63"}]
TRY = {"stage": "Form 1", "entries": ENTRIES}
CASE = {**TRY, "expected": {"change": "11"}, "agreed": False}
ROUTES = [
    ("GET", "/human/measures", None),
    ("GET", OPEN, None),
    ("POST", f"{OPEN}/try", TRY),
    ("POST", f"{OPEN}/case", CASE),
    ("POST", f"{OPEN}/confirm", {"note": "checked"}),
    ("POST", f"{OPEN}/reject", {"note": "does not match section 6.4"}),
]


def token(user="u-teacher", name="Ms Teacher", school="BAO", secret=SECRET, **more):
    return approver_token.sign(secret, user, name, school=school, **more)


def good(**over):
    headers = {"X-Human-Route-Key": KEY, "X-Approver-Token": token()}
    headers.update(over)
    return {k: v for k, v in headers.items() if v is not None}


class Collector:
    """Stands in for FastMCP: the routes only need `custom_route`."""

    def __init__(self):
        self.routes = []

    def custom_route(self, path, methods, **_):
        def add(function):
            self.routes.append(Route(path, endpoint=function, methods=methods))
            return function

        return add


class FakeStore:
    """Records every call and answers with what the test set."""

    def __init__(self):
        self.calls = []
        self.answers = {
            "list": {"status": "ok", "waiting": 1, "measures": [{"measure_id": MS, "version": 1}]},
            "open": {"status": "ok", "measure_id": MS, "version": 1, "name": "Change"},
            "try": {"status": "ok", "measure_id": MS, "answer": {"change": {"exact": "11"}}},
            "case": {"status": "saved", "measure_id": MS, "name": "teacher case 1"},
            "confirm": {"status": "confirmed", "measure_id": MS, "version": 1},
            "reject": {"status": "rejected", "measure_id": MS, "version": 1},
        }

    def _note(self, what, ctx, **arguments):
        self.calls.append(SimpleNamespace(what=what, approver=ctx.approver, school=ctx.school,
                                          args=arguments))
        return dict(self.answers[what])

    def list_awaiting(self, ctx):
        return self._note("list", ctx)

    def open_demo(self, ctx, measure_id, version):
        return self._note("open", ctx, measure_id=measure_id, version=version)

    def try_measure(self, ctx, measure_id, version, stage, entries):
        return self._note("try", ctx, measure_id=measure_id, version=version, stage=stage,
                          entries=entries)

    def save_case(self, ctx, measure_id, version, stage, entries, expected, agreed=False):
        return self._note("case", ctx, measure_id=measure_id, version=version, stage=stage,
                          entries=entries, expected=expected, agreed=agreed)

    def confirm(self, ctx, measure_id, version, note=None):
        return self._note("confirm", ctx, measure_id=measure_id, version=version, note=note)

    def reject(self, ctx, measure_id, version, note):
        return self._note("reject", ctx, measure_id=measure_id, version=version, note=note)


@contextmanager
def no_database():
    yield object()


def mounted(store, env):
    collector = Collector()
    measure_routes.install(collector, store=store, connect=no_database, env=env, commit=False)
    return TestClient(Starlette(routes=collector.routes))


@pytest.fixture
def world(monkeypatch):
    rows = []

    def write(tool, **kw):
        rows.append({"tool": tool, **kw})
        return len(rows)

    monkeypatch.setattr(diary, "write", write)
    store = FakeStore()
    env = {"HUMAN_ROUTE_KEY": KEY, "APPROVER_TOKEN_SECRET": SECRET, "DATABASE_URL": "unused"}
    return SimpleNamespace(client=mounted(store, env), store=store, rows=rows, env=env)


def send(world, method, path, body=None, headers=None):
    sent = {"json": body} if body is not None else {}
    return world.client.request(method, path, headers=good() if headers is None else headers,
                                **sent)


def refusals(world):
    return [r for r in world.rows if r["tool"] == human_routes.REFUSED_TOOL]


# --------------------------------------------------------------------------- the route table
def test_the_server_adds_exactly_these_six_routes():
    collector = Collector()
    measure_routes.install(collector, store=FakeStore(), env={})
    found = {(m, r.path) for r in collector.routes for m in r.methods if m != "HEAD"}
    assert found == {
        ("GET", "/human/measures"),
        ("GET", "/human/measures/{measure_id}/{version}"),
        ("POST", "/human/measures/{measure_id}/{version}/try"),
        ("POST", "/human/measures/{measure_id}/{version}/case"),
        ("POST", "/human/measures/{measure_id}/{version}/confirm"),
        ("POST", "/human/measures/{measure_id}/{version}/reject"),
    }


def test_the_routes_use_the_same_gate_headers_as_the_lesson_routes():
    assert measure_routes.KEY_HEADER == human_routes.KEY_HEADER
    assert measure_routes.TOKEN_HEADER == human_routes.TOKEN_HEADER


# --------------------------------------------------------------------------- the key and the token
def long_ago():
    return token(now=time.time() - 10_000)


GATE_CASES = [
    ("no key", {"X-Human-Route-Key": None}, "no_key", 401),
    ("empty key", {"X-Human-Route-Key": ""}, "no_key", 401),
    ("wrong key", {"X-Human-Route-Key": "another-key"}, "wrong_key", 401),
    ("key in capitals", {"X-Human-Route-Key": KEY.upper()}, "wrong_key", 401),
    ("no token", {"X-Approver-Token": None}, "no_token", 401),
    ("forged token", {"X-Approver-Token": lambda: token(secret="not-the-secret")}, "bad_token", 401),
    ("garbage token", {"X-Approver-Token": "garbage"}, "bad_token", 401),
    ("expired token", {"X-Approver-Token": long_ago()}, "expired_token", 401),
    ("token without a school", {"X-Approver-Token": lambda: token(school=None)}, "no_school", 403),
]


@pytest.mark.parametrize("method,path,body", ROUTES)
@pytest.mark.parametrize("label,headers,reason,status", GATE_CASES, ids=[c[0] for c in GATE_CASES])
def test_every_route_refuses_without_both_the_key_and_a_good_token(
    world, method, path, body, label, headers, reason, status
):
    # A token lasts five minutes, so these are signed here, not when the tests are collected.
    headers = {k: (v() if callable(v) else v) for k, v in headers.items()}
    answer = send(world, method, path, body, good(**headers))
    assert answer.status_code == status
    assert answer.json()["status"] == "refused" and answer.json()["reason"] == reason
    assert world.store.calls == [], "the store is never reached"
    (row,) = refusals(world)
    assert row["outcome"] == "refused" and row["outputs"]["reason"] == reason
    assert row["approver_id"] is None and row["approver_name"] is None
    assert row["inputs"]["route"] == measure_routes.route_name(path)
    written = json.dumps(row, default=str)
    assert KEY not in written and "another-key" not in written
    assert good()["X-Approver-Token"] not in written


@pytest.mark.parametrize("method,path,body", ROUTES)
@pytest.mark.parametrize("unset", ["missing", "empty", "blank"])
def test_the_routes_are_off_when_the_key_is_not_set(world, method, path, body, unset):
    if unset == "missing":
        del world.env["HUMAN_ROUTE_KEY"]
    else:
        world.env["HUMAN_ROUTE_KEY"] = "" if unset == "empty" else "   "
    answer = send(world, method, path, body)
    assert answer.status_code == 503 and answer.json()["reason"] == "routes_off"
    assert world.store.calls == []


# --------------------------------------------------------------------------- who is acting
@pytest.mark.parametrize("method,path,body", ROUTES)
def test_the_person_and_the_school_come_from_the_token_and_nowhere_else(world, method, path, body):
    other = {"X-Approver-Name": "Mr Intruder", "X-Approver-Id": "u-intruder", "X-School": "XXX",
             "X-OpenWebUI-User-Id": "u-intruder", "X-OpenWebUI-User-Name": "Mr Intruder"}
    answer = world.client.request(
        method, path + "?approver=u-intruder&school=XXX&user=u-intruder",
        headers={**good(), **other}, **({"json": body} if body is not None else {}))
    assert answer.status_code == 200
    (call,) = world.store.calls
    assert (call.approver.id, call.approver.name, call.school) == ("u-teacher", "Ms Teacher", "BAO")


@pytest.mark.parametrize("field", [
    "approver", "approver_id", "approver_name", "user", "user_id", "name", "teacher", "teacher_id",
    "teacher_name", "school", "school_code", "status", "decision", "measure_id", "version",
    "learner_id", "token",
])
@pytest.mark.parametrize("path,body", [
    (f"{OPEN}/try", TRY), (f"{OPEN}/case", CASE), (f"{OPEN}/confirm", {"note": "n"}),
    (f"{OPEN}/reject", {"note": "n"}),
])
def test_no_body_can_name_a_person_a_school_a_status_or_a_decision(world, path, body, field):
    answer = send(world, "POST", path, {**body, field: "x"})
    assert answer.status_code == 422 and answer.json()["reason"] == "unknown_field"
    assert world.store.calls == []
    assert len(refusals(world)) == 1 and refusals(world)[0]["approver_id"] == "u-teacher"


@pytest.mark.parametrize("path,fields", [
    ("try", ("stage", "entries")), ("case", ("stage", "entries", "expected", "agreed")),
    ("confirm", ("note",)), ("reject", ("note",)),
])
def test_the_fields_a_body_may_hold_are_fixed(path, fields):
    assert getattr(measure_routes, f"{path.upper()}_FIELDS") == fields


@pytest.mark.parametrize("path,body", [
    (f"{OPEN}/try", {"stage": 3, "entries": ENTRIES}),
    (f"{OPEN}/try", {"stage": "Form 1", "entries": "52"}),
    (f"{OPEN}/try", {"stage": "Form 1"}),
    (f"{OPEN}/try", {"entries": ENTRIES}),
    (f"{OPEN}/case", {**TRY, "expected": "11"}),
    (f"{OPEN}/case", {**TRY}),
    (f"{OPEN}/case", {**CASE, "agreed": "yes"}),
    (f"{OPEN}/confirm", {"note": 5}),
    (f"{OPEN}/confirm", {"note": ["a"]}),
    (f"{OPEN}/reject", {"note": ""}),
    (f"{OPEN}/reject", {"note": "   "}),
    (f"{OPEN}/reject", {"note": None}),
    (f"{OPEN}/reject", {}),
])
def test_a_body_of_the_wrong_shape_is_refused_before_the_store_is_reached(world, path, body):
    answer = send(world, "POST", path, body)
    assert answer.status_code in (400, 422)
    assert answer.json()["status"] == "refused"
    assert world.store.calls == []


@pytest.mark.parametrize("raw", [b"not json", b"[]", b"null", b'"x"'])
def test_a_body_that_is_not_one_json_object_is_a_plain_400(world, raw):
    answer = world.client.post(f"{OPEN}/try", content=raw, headers=good())
    assert answer.status_code == 400 and answer.json()["reason"] == "bad_body"
    assert world.store.calls == []


def test_a_body_over_the_transport_limit_is_refused(world):
    big = {**TRY, "entries": ENTRIES * 2000}
    answer = send(world, "POST", f"{OPEN}/try", big)
    assert answer.status_code == 400 and answer.json()["reason"] == "bad_body"
    assert world.store.calls == []


def test_confirm_takes_an_empty_body_or_a_note_and_a_get_takes_none(world):
    assert world.client.post(f"{OPEN}/confirm", headers=good()).status_code == 200
    assert send(world, "POST", f"{OPEN}/confirm", {}).status_code == 200
    assert send(world, "POST", f"{OPEN}/confirm", {"note": "checked"}).status_code == 200
    assert [c.args["note"] for c in world.store.calls] == [None, None, "checked"]


@pytest.mark.parametrize("path", [
    "/human/measures/MS-1/1", "/human/measures/ms-0001/1", "/human/measures/MS-0001x/1",
    "/human/measures/x/1", f"/human/measures/{MS}/0", f"/human/measures/{MS}/-1",
    f"/human/measures/{MS}/one", f"/human/measures/{MS}/1.5",
])
def test_a_measure_number_or_version_that_is_not_one_of_ours_is_404_and_not_sent_on(world, path):
    answer = send(world, "GET", path)
    assert answer.status_code == 404 and answer.json()["reason"] == "measure_not_found"
    assert send(world, "POST", path + "/confirm", {}).status_code == 404
    assert world.store.calls == []
    assert all(r["approver_id"] == "u-teacher" for r in refusals(world))


def test_the_version_reaches_the_store_as_a_whole_number(world):
    send(world, "GET", f"/human/measures/{MS}/12")
    (call,) = world.store.calls
    assert call.args == {"measure_id": MS, "version": 12}


def test_the_try_and_the_case_reach_the_store_as_sent_after_the_shape_is_checked(world):
    send(world, "POST", f"{OPEN}/try", TRY)
    send(world, "POST", f"{OPEN}/case", CASE)
    try_call, case_call = world.store.calls
    assert try_call.args["stage"] == "Form 1" and try_call.args["entries"] == ENTRIES
    assert case_call.args["expected"] == {"change": "11"} and case_call.args["agreed"] is False
    plain = {**TRY, "expected": {"change": "11"}}
    assert send(world, "POST", f"{OPEN}/case", plain).status_code == 200
    assert world.store.calls[-1].args["agreed"] is False


# --------------------------------------------------------------------------- what the store says
def test_a_good_answer_is_handed_back_with_status_200_and_no_diary_row_of_the_routes_own(world):
    for method, path, body in ROUTES:
        assert send(world, method, path, body).status_code == 200
    assert refusals(world) == []
    assert world.rows == [], "each store function writes its own diary row"


@pytest.mark.parametrize("what,path,body,wrong", [
    ("list", "/human/measures", None, {"status": "something else"}),
    ("open", OPEN, None, {"status": "confirmed"}),
    ("try", f"{OPEN}/try", TRY, {"status": "saved"}),
    ("case", f"{OPEN}/case", CASE, {"status": "ok"}),
    ("confirm", f"{OPEN}/confirm", {}, {"status": "ok"}),
    ("confirm", f"{OPEN}/confirm", {}, {"status": "approved"}),
    ("reject", f"{OPEN}/reject", {"note": "n"}, {"status": "confirmed"}),
])
def test_an_answer_the_route_does_not_know_is_a_502_and_never_a_success(
    world, what, path, body, wrong
):
    world.store.answers[what] = wrong
    method = "GET" if body is None else "POST"
    answer = send(world, method, path, body)
    assert answer.status_code == 502 and answer.json()["reason"] == "unexpected_answer"


REFUSAL_TYPES = [
    ("no one is signed in, so this cannot be recorded", "not_signed_in", 401),
    ("[no_school] no school is attached to this session", "no_school", 403),
    ("[not_a_teacher] the signed-in person is not recorded as a teacher", "not_a_teacher", 403),
    ("the signed-in person is not recorded as a teacher in the registry", "not_a_teacher", 403),
    ("there is no version 9 of MS-0001", "measure_not_found", 404),
    ("[not_found] there is no such version", "measure_not_found", 404),
    ("[not_at_demo] this version is not waiting for a teacher", "not_at_demo", 409),
    ("a decision is taken at the demo, when the version waits for the teacher", "not_at_demo", 409),
    ("[approval_conditions_not_met] the registry would refuse it",
     "approval_conditions_not_met", 409),
    ("the registry refused it: that needs passed tests", "approval_conditions_not_met", 409),
    ("[case_mismatch] the answer differs", "case_mismatch", 409),
    ("a rejection needs a note", "note_needed", 422),
    ("[bad_score] a score is plain text", "bad_score", 422),
    ("[score_off_scale] a score is off the scale", "score_off_scale", 422),
    ("[unknown_subject] not a subject", "unknown_subject", 422),
    ("[bad_expected] expected answer", "bad_expected", 422),
    ("[runner_unavailable] the sandbox could not be reached", "runner_unavailable", 503),
    ("[measure_failed] the function did not give an answer", "measure_failed", 502),
    ("something the table does not know", "refused", 409),
    (None, "refused", 409),
]


@pytest.mark.parametrize("reason,typed,status", REFUSAL_TYPES)
def test_a_refusal_from_the_store_is_typed_with_its_own_status(world, reason, typed, status):
    world.store.answers["confirm"] = {"status": "refused", "reason": reason}
    answer = send(world, "POST", f"{OPEN}/confirm", {})
    assert answer.status_code == status
    assert answer.json()["status"] == "refused" and answer.json()["reason"] == typed
    assert measure_routes.type_refusal(reason) == (typed, status)


def test_what_is_still_missing_and_the_answer_shown_are_handed_back_with_a_refusal(world):
    gaps = [{"code": "no_independent_test", "text": "No test comes from outside the build."}]
    world.store.answers["confirm"] = {
        "status": "refused", "reason": "[approval_conditions_not_met] the registry needs more",
        "gaps": gaps}
    body = send(world, "POST", f"{OPEN}/confirm", {}).json()
    assert body["gaps"] == gaps and body["detail"]
    world.store.answers["case"] = {
        "status": "refused", "reason": "[case_mismatch] differs",
        "answer": {"change": {"value": "11.00", "exact": "11"}}, "compare": {"match": False}}
    body = send(world, "POST", f"{OPEN}/case", CASE).json()
    assert body["answer"]["change"]["exact"] == "11" and body["compare"] == {"match": False}


def test_a_refusal_hands_back_only_the_fields_the_page_may_show(world):
    world.store.answers["try"] = {"status": "refused", "reason": "[bad_score] x",
                                  "source_code": "SECRET CODE", "approver_token": "t"}
    body = send(world, "POST", f"{OPEN}/try", TRY).json()
    assert "source_code" not in body and "approver_token" not in body


def test_a_failure_inside_a_route_is_a_plain_500_with_nothing_from_the_failure(world):
    def boom(ctx, **_):
        raise RuntimeError("secret detail: Ada Lovelace")

    world.store.open_demo = boom
    answer = send(world, "GET", OPEN)
    assert answer.status_code == 500 and answer.json()["reason"] == "error"
    assert "Ada" not in answer.text and "secret" not in answer.text
    (row,) = refusals(world)
    assert row["outcome"] == "error" and "Ada" not in json.dumps(row, default=str)


def test_when_the_diary_cannot_be_written_a_refusal_is_a_503(world, monkeypatch):
    def down(*a, **k):
        raise diary.DiaryUnavailable("down")

    monkeypatch.setattr(diary, "write", down)
    answer = send(world, "GET", OPEN, headers=good(**{"X-Human-Route-Key": "wrong"}))
    assert answer.status_code == 503 and answer.json()["reason"] == "log_unavailable"


# --------------------------------------------------------------------------- the file itself
def test_the_routes_never_name_the_key_a_model_or_a_person_field():
    text = Path(measure_routes.__file__).read_text("utf-8")
    assert "HUMAN_ROUTE_KEY" not in text, "the gate is the lesson routes' gate, not a copy"
    for word in ("OPENROUTER", "tool_calls", "complete(", "call_tool"):
        assert word not in text
