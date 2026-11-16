"""The human-only routes on the tool server (T-062). No database and no network: the lesson store
is replaced by a fake that records what it is given, and the routes are mounted on a plain
Starlette app.

What is held here: every route needs the shared key AND a good signed token, and refuses with a
typed reason and a diary row otherwise; the person and the school come from the token and from
nowhere else; the body is closed (no field can name a person or a school); a store refusal becomes
a typed reason; the routes are off when the key is not set. The real store is driven in
`test_human_routes_db.py`.
"""

import json
import re
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("starlette")

from lessonkit import content  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.routing import Route  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from mdeis import human_routes  # noqa: E402
from mdeis.core import approver_token  # noqa: E402
from mdeis.db import diary  # noqa: E402
from mdeis.lessons.rules import LessonsUnavailable, lesson_rules_from_content  # noqa: E402

SECRET = "approver-secret-not-real"
KEY = "human-key-not-real"
PLAN = "LP-000001"
ROUTES = [
    ("GET", "/human/lessons/options"),
    ("GET", "/human/lessons"),
    ("GET", f"/human/lessons/{PLAN}"),
    ("POST", f"/human/lessons/{PLAN}/approve"),
    ("POST", f"/human/lessons/{PLAN}/reject"),
]
ACCEPT_ONE = {"accepted_edit_ids": ["e1"]}


def token(user="u-1", name="Ms Tutor", school="TST", secret=SECRET, **more):
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
    """Records every call and answers with what the test set. It has no list function unless the
    test gives it one."""

    def __init__(self):
        self.calls = []
        self.answers = {
            "approve": {
                "status": "approved", "plan_id": PLAN, "accepted_edit_ids": ["e1"],
                "left_out_edit_ids": [], "final_sha256": "f" * 64, "approver_name": "Ms Tutor",
            },
            "reject": {"status": "rejected", "plan_id": PLAN},
            "get": {"status": "ok", "plan_id": PLAN, "plan_status": "proposed", "edits": []},
        }

    def _note(self, what, ctx, *args):
        self.calls.append(
            SimpleNamespace(what=what, approver=ctx.approver, school=ctx.school, args=args)
        )
        return dict(self.answers[what])

    def approve_lesson_plan(self, ctx, plan_id, accepted_edit_ids):
        return self._note("approve", ctx, plan_id, accepted_edit_ids)

    def reject_lesson_plan(self, ctx, plan_id):
        return self._note("reject", ctx, plan_id)

    def get_lesson_plan(self, ctx, plan_id):
        return self._note("get", ctx, plan_id)


@contextmanager
def no_database():
    yield object()


def rulebook(conn):
    return lesson_rules_from_content(content(), "t-lessons", "a" * 64)


def mounted(store, env, load_rules=rulebook):
    collector = Collector()
    human_routes.install(
        collector, store=store, connect=no_database, env=env, commit=False, load_rules=load_rules
    )
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


def send(world, method, path, headers=None, **kw):
    return world.client.request(method, path, headers=good() if headers is None else headers, **kw)


def approve(world, body=None, **kw):
    sent = {"json": ACCEPT_ONE} if body is None else {"json": body}
    return send(world, "POST", f"/human/lessons/{PLAN}/approve", **sent, **kw)


def refusals(world):
    return [r for r in world.rows if r["tool"] == human_routes.REFUSED_TOOL]


# --------------------------------------------------------------------------- the route table
def test_the_server_adds_exactly_these_five_routes():
    collector = Collector()
    human_routes.install(collector, store=FakeStore(), env={})
    found = {(m, r.path) for r in collector.routes for m in r.methods if m != "HEAD"}
    assert found == {
        ("GET", "/human/lessons/options"), ("GET", "/human/lessons"),
        ("GET", "/human/lessons/{plan_id}"), ("POST", "/human/lessons/{plan_id}/approve"),
        ("POST", "/human/lessons/{plan_id}/reject"),
    }


def test_the_options_route_is_registered_before_the_one_that_takes_a_plan_number():
    collector = Collector()
    human_routes.install(collector, store=FakeStore(), env={})
    paths = [r.path for r in collector.routes if "GET" in r.methods]
    assert paths.index("/human/lessons/options") < paths.index("/human/lessons/{plan_id}")


# --------------------------------------------------------------------------- the key and the token
def long_ago():
    return token(now=time.time() - 10_000)


GATE_CASES = [
    ("no key", {"X-Human-Route-Key": None}, "no_key", 401),
    ("empty key", {"X-Human-Route-Key": ""}, "no_key", 401),
    ("wrong key", {"X-Human-Route-Key": "another-key"}, "wrong_key", 401),
    ("key in capitals", {"X-Human-Route-Key": KEY.upper()}, "wrong_key", 401),
    ("no token", {"X-Approver-Token": None}, "no_token", 401),
    ("forged token", {"X-Approver-Token": token(secret="not-the-secret")}, "bad_token", 401),
    ("garbage token", {"X-Approver-Token": "garbage"}, "bad_token", 401),
    ("expired token", {"X-Approver-Token": long_ago()}, "expired_token", 401),
    ("token without a school", {"X-Approver-Token": token(school=None)}, "no_school", 403),
]


@pytest.mark.parametrize("method,path", ROUTES)
@pytest.mark.parametrize("label,headers,reason,status", GATE_CASES, ids=[c[0] for c in GATE_CASES])
def test_every_route_refuses_without_both_the_key_and_a_good_token(
    world, method, path, label, headers, reason, status
):
    body = {"json": ACCEPT_ONE} if path.endswith("/approve") else {}
    answer = send(world, method, path, good(**headers), **body)
    assert answer.status_code == status
    assert answer.json()["status"] == "refused" and answer.json()["reason"] == reason
    assert world.store.calls == [], "the store is never reached"
    (row,) = refusals(world)
    assert row["outcome"] == "refused" and row["outputs"]["reason"] == reason
    assert row["approver_id"] is None and row["approver_name"] is None, "nothing is taken on trust"
    assert row["inputs"]["route"] == path.replace(PLAN, "{plan_id}")
    written = json.dumps(row, default=str)
    assert KEY not in written and "another-key" not in written
    assert good()["X-Approver-Token"] not in written


@pytest.mark.parametrize("method,path", ROUTES)
@pytest.mark.parametrize("unset", ["missing", "empty", "blank"])
def test_the_routes_are_off_when_the_key_is_not_set(world, method, path, unset):
    if unset == "missing":
        del world.env["HUMAN_ROUTE_KEY"]
    else:
        world.env["HUMAN_ROUTE_KEY"] = "" if unset == "empty" else "   "
    for sent in (good(), good(**{"X-Human-Route-Key": ""}), good(**{"X-Human-Route-Key": None})):
        answer = send(world, method, path, sent)
        assert answer.status_code == 503 and answer.json()["reason"] == "routes_off"
    assert world.store.calls == []
    assert all(r["outputs"]["reason"] == "routes_off" for r in refusals(world))


@pytest.mark.parametrize("method,path", ROUTES)
def test_no_signing_secret_means_no_token_can_be_good(world, method, path):
    del world.env["APPROVER_TOKEN_SECRET"]
    answer = send(world, method, path)
    assert answer.status_code == 401 and answer.json()["reason"] == "bad_token"
    assert world.store.calls == []


def test_a_diary_that_cannot_be_written_stops_the_call(world, monkeypatch):
    def down(tool, **kw):
        raise diary.DiaryUnavailable("no diary")

    monkeypatch.setattr(diary, "write", down)
    answer = approve(world, headers=good(**{"X-Human-Route-Key": ""}))
    assert answer.status_code == 503 and answer.json()["reason"] == "log_unavailable"
    assert world.store.calls == []


def test_the_key_is_compared_in_constant_time_and_is_never_written_in_the_source():
    source = Path(human_routes.__file__).read_text("utf-8")
    assert "compare_digest" in source
    assert KEY not in source
    assert not re.search(r'HUMAN_ROUTE_KEY["\']?\s*[:=]\s*["\'][^"\']', source)


# --------------------------------------------------------------------------- who and which school
def test_the_person_and_the_school_come_from_the_token_and_nowhere_else(world):
    sent = good(**{
        "X-Approver-Token": token("u-7", "Ms Seven", "SCH-7"),
        "X-Approver-Id": "u-9", "X-School": "OTHER", "X-User-Id": "u-9",
    })
    path = f"/human/lessons/{PLAN}/approve?approver_id=u-9&school=OTHER"
    answer = world.client.post(path, headers=sent, json=ACCEPT_ONE)
    assert answer.status_code == 200 and answer.json()["status"] == "approved"
    (call,) = world.store.calls
    assert (call.approver.id, call.approver.name, call.school) == ("u-7", "Ms Seven", "SCH-7")
    assert call.args == (PLAN, ["e1"])


NAMING_SOMEONE = [
    "approver", "approver_id", "approver_name", "author", "author_id", "user", "user_id",
    "user_name", "name", "school", "school_id", "school_code", "status", "plan_id",
]


@pytest.mark.parametrize("field", NAMING_SOMEONE)
def test_no_field_in_the_approve_body_can_name_a_person_or_a_school(world, field):
    answer = approve(world, {**ACCEPT_ONE, field: "u-9"})
    assert answer.status_code == 422 and answer.json()["reason"] == "unknown_field"
    assert world.store.calls == []
    (row,) = refusals(world)
    assert row["outcome"] == "refused" and row["outputs"]["reason"] == "unknown_field"
    assert row["approver_id"] == "u-1", "a good token is known, so the diary names the person"


@pytest.mark.parametrize("field", NAMING_SOMEONE)
def test_no_field_in_the_reject_body_either(world, field):
    answer = send(world, "POST", f"/human/lessons/{PLAN}/reject", json={field: "u-9"})
    assert answer.status_code == 422 and answer.json()["reason"] == "unknown_field"
    assert world.store.calls == []


BAD_BODIES = [
    b"", b"not json", b"[]", b'"e1"', b"null", b'{"accepted_edit_ids": ["e1"]} trailing',
    b'{"accepted_edit_ids": ["e1"]}' + b" " * 20_000,
]


@pytest.mark.parametrize("raw", BAD_BODIES, ids=[
    "empty", "not json", "list", "string", "null", "trailing text", "too long"])
def test_an_approve_body_that_is_not_a_small_json_object_is_refused(world, raw):
    answer = send(world, "POST", f"/human/lessons/{PLAN}/approve", content=raw)
    assert answer.status_code == 400 and answer.json()["reason"] == "bad_body"
    assert world.store.calls == []
    assert len(refusals(world)) == 1


def test_an_approve_body_without_the_accepted_ids_is_refused_before_the_store(world):
    answer = approve(world, {})
    assert answer.status_code == 422 and answer.json()["reason"] == "accepted_edits_invalid"
    assert world.store.calls == []


@pytest.mark.parametrize("accepted", ["e1", None, 1, [1], [None], {"e1": True}])
def test_the_accepted_ids_must_be_a_list_of_text(world, accepted):
    answer = approve(world, {"accepted_edit_ids": accepted})
    assert answer.status_code == 422 and answer.json()["reason"] == "accepted_edits_invalid"
    assert world.store.calls == []


@pytest.mark.parametrize("raw", [None, b"", b"{}"], ids=["no body", "empty", "empty object"])
def test_reject_takes_no_body(world, raw):
    sent = {} if raw is None else {"content": raw}
    answer = send(world, "POST", f"/human/lessons/{PLAN}/reject", **sent)
    assert answer.status_code == 200 and answer.json()["status"] == "rejected"
    (call,) = world.store.calls
    assert (call.what, call.args) == ("reject", (PLAN,))
    assert (call.approver.id, call.school) == ("u-1", "TST")


@pytest.mark.parametrize("plan", ["LP-1", "lp-000001", "LP-000001x", "x" * 200])
def test_a_plan_number_that_is_not_one_of_ours_is_not_found_and_not_looked_up(world, plan):
    for method, tail in (("GET", ""), ("POST", "/approve"), ("POST", "/reject")):
        sent = {"json": ACCEPT_ONE} if tail == "/approve" else {}
        answer = send(world, method, f"/human/lessons/{plan}{tail}", **sent)
        assert answer.status_code == 404 and answer.json()["reason"] == "plan_not_found"
    assert world.store.calls == []


# --------------------------------------------------------------------------- what the store says
STORE_REFUSALS = [
    ("there is no lesson plan LP-000009 for this school", "plan_not_found", 404),
    ("this lesson plan belongs to another author", "not_the_author", 403),
    ("the plan is approved, so it cannot be approved", "plan_not_open", 409),
    ("the plan is rejected, so it cannot be approved", "plan_not_open", 409),
    ("at least one edit must be accepted", "accepted_edits_invalid", 422),
    ("these are not edits of this plan: e9", "accepted_edits_invalid", 422),
    ("an edit is listed twice", "accepted_edits_invalid", 422),
    ("accepted_edit_ids must be edit ids", "accepted_edits_invalid", 422),
    ("the objective would change, so the plan cannot be approved",
     "approval_conditions_not_met", 409),
    ("the database refused it: approval needs a passed check, at least one accepted edit that is "
     "one of the kept edits, and the author as the approver", "approval_conditions_not_met", 409),
    ("no one is signed in, so this cannot be recorded", "not_signed_in", 401),
    ("no school is attached to this session", "no_school", 403),
    ("something the table does not know", "refused", 409),
]


@pytest.mark.parametrize("reason,typed,status", STORE_REFUSALS)
def test_a_refusal_from_the_store_becomes_a_typed_reason_with_its_plain_words(
    world, reason, typed, status
):
    world.store.answers["approve"] = {"status": "refused", "reason": reason}
    answer = approve(world)
    assert answer.status_code == status
    assert answer.json() == {"status": "refused", "reason": typed, "detail": reason}


def test_an_answer_the_store_does_not_know_how_to_give_is_not_passed_on_as_success(world):
    world.store.answers["approve"] = {"status": "approved-ish"}
    answer = approve(world)
    assert answer.status_code == 502 and answer.json()["reason"] == "unexpected_answer"


def test_approve_and_reject_hand_back_what_the_store_returned(world):
    assert approve(world).json() == world.store.answers["approve"]
    rejected = send(world, "POST", f"/human/lessons/{PLAN}/reject")
    assert rejected.json() == world.store.answers["reject"]


def test_a_failure_inside_a_route_is_a_plain_error_with_no_text_from_the_failure(world):
    def boom(ctx, plan_id, accepted_edit_ids):
        raise RuntimeError("password=hunter2 in notes: Amara Nfor")

    world.store.approve_lesson_plan = boom
    answer = approve(world)
    assert answer.status_code == 500 and answer.json()["reason"] == "error"
    assert "hunter2" not in answer.text and "Amara" not in answer.text


# --------------------------------------------------------------------------- reading
def test_reading_a_plan_goes_through_the_store_for_the_signed_in_person(world):
    answer = send(world, "GET", f"/human/lessons/{PLAN}")
    assert answer.status_code == 200 and answer.json()["plan"]["plan_id"] == PLAN
    (call,) = world.store.calls
    assert (call.what, call.args, call.approver.id) == ("get", (PLAN,), "u-1")


def test_a_refused_read_is_typed_too(world):
    reason = "this lesson plan belongs to another author"
    world.store.answers["get"] = {"status": "refused", "reason": reason}
    answer = send(world, "GET", f"/human/lessons/{PLAN}")
    assert answer.status_code == 403 and answer.json()["reason"] == "not_the_author"


def test_the_list_says_plainly_when_the_store_has_no_list_function(world):
    answer = send(world, "GET", "/human/lessons")
    assert answer.status_code == 501 and answer.json()["reason"] == "list_unavailable"
    assert world.store.calls == []


def test_the_list_is_the_signed_in_persons_own_plans_from_the_store(world):
    seen = []

    def list_lesson_plans(ctx):
        seen.append((ctx.approver.id, ctx.school))
        return {"status": "ok", "plans": [{"plan_id": "LP-000002"}, {"plan_id": PLAN}]}

    world.store.list_lesson_plans = list_lesson_plans
    answer = send(world, "GET", "/human/lessons?author=u-9")
    assert answer.status_code == 200
    assert [p["plan_id"] for p in answer.json()["plans"]] == ["LP-000002", PLAN]
    assert seen == [("u-1", "TST")], "the query string names nobody"


# --------------------------------------------------------------------------- the form's lists
def test_the_options_are_read_from_the_rulebook_foundation_areas_first(world):
    answer = send(world, "GET", "/human/lessons/options")
    assert answer.status_code == 200
    got = answer.json()
    assert got["status"] == "ok" and got["school"] == "TST"
    assert got["template_version"] == "t-lessons"
    assert got["terms"] == [1, 2, 3] and got["max_notes_chars"] == 8000
    assert got["stages"] == [
        {"stage": "Form 1", "areas": [{"area": "literacy", "foundation": True}]},
        {"stage": "Form 3", "areas": [
            {"area": "literacy", "foundation": True}, {"area": "numeracy", "foundation": True},
        ]},
    ]


def test_a_new_area_comes_after_the_foundation_areas_and_the_lists_follow_the_rulebook(world):
    def more(c):
        extra = {"stage": "Form 3", "range": [1, 2], "text": "x"}
        c["stage_expectations"].insert(2, {"id": "ST-9", "area": "science", **extra})
        c["stage_expectations"].insert(2, {"id": "ST-8", "area": "art", **extra})
        c["calculations"][0]["terms_per_year"] = 4
        c["lessons"][0]["max_notes_chars"] = 5000

    changed = content(more)
    client = mounted(
        world.store, world.env, lambda conn: lesson_rules_from_content(changed, "t2", "b" * 64)
    )
    got = client.get("/human/lessons/options", headers=good()).json()
    form3 = next(s for s in got["stages"] if s["stage"] == "Form 3")
    assert [a["area"] for a in form3["areas"]] == ["literacy", "numeracy", "art", "science"]
    assert [a["foundation"] for a in form3["areas"]] == [True, True, False, False]
    assert got["terms"] == [1, 2, 3, 4] and got["max_notes_chars"] == 5000
    assert got["template_version"] == "t2"


def test_a_rulebook_without_the_lesson_rules_is_a_plain_refusal(world):
    def off(conn):
        raise LessonsUnavailable("the rulebook v1 has no lessons section (LS)")

    client = mounted(world.store, world.env, off)
    answer = client.get("/human/lessons/options", headers=good())
    assert answer.status_code == 409 and answer.json()["reason"] == "rulebook_unavailable"
    assert "lessons section" in answer.json()["detail"]


def test_the_answers_never_carry_the_key_or_the_token(world):
    for method, path in ROUTES:
        sent = {"json": ACCEPT_ONE} if path.endswith("/approve") else {}
        answer = send(world, method, path, **sent)
        assert KEY not in answer.text and good()["X-Approver-Token"] not in answer.text
