"""The human-only routes against the REAL lesson store (T-062). Integration: needs DATABASE_URL, and
works in a throwaway schema with one connection per test, as `test_lessons_store.py` does.

What is held here: the approver in the plan and in the diary is the person in the token; a person
who is not the author is refused; a plan of another school is not found; a plan whose checks did
not pass cannot be approved; approval needs an accepted edit; approving twice is REFUSED (not
repeated); a refusal leaves the plan as it was, gives a typed reason and leaves a row in the
diary; and the words the store refuses with are the ones the route knows how to type.
"""

# The `db` and `ctx` fixtures are imported from test_lessons_store and used as test arguments, which
# ruff reads as a redefinition. This file-level directive covers that one rule in this file only.
# ruff: noqa: F811

from contextlib import contextmanager

import pytest

pytest.importorskip("starlette")

from lessonkit import A_ASK, A_PLENARY, LESSON  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402
from test_human_routes import Collector  # noqa: E402
from test_lessons_store import (  # noqa: E402,F401
    AUTHOR,
    OTHER,
    SCHOOL,
    class_read,
    ctx,
    db,
    edit,
    output,
    save,
    second_edit,
    stored,
)

from mdeis import human_routes  # noqa: E402
from mdeis.core import approver_token  # noqa: E402
from mdeis.lessons import store  # noqa: E402

pytestmark = pytest.mark.integration
SECRET = "approver-secret-not-real"
KEY = "human-key-not-real"


def token(who=AUTHOR, school=SCHOOL, **more):
    return approver_token.sign(SECRET, who.id, who.name, school=school, **more)


def headers(who=AUTHOR, school=SCHOOL, **over):
    sent = {"X-Human-Route-Key": KEY, "X-Approver-Token": token(who, school)}
    sent.update(over)
    return {k: v for k, v in sent.items() if v is not None}


def client_for(db, **env):
    @contextmanager
    def connect():
        yield db

    settings = {
        "HUMAN_ROUTE_KEY": KEY, "APPROVER_TOKEN_SECRET": SECRET, "DATABASE_URL": db.url, **env,
    }
    collector = Collector()
    human_routes.install(collector, connect=connect, env=settings, commit=False)
    return TestClient(Starlette(routes=collector.routes))


def two_edits(db):
    class_read(db)
    return save(db, output(edit(), second_edit()))["plan_id"]


def one_edit(db):
    class_read(db)
    return save(db)["plan_id"]


def decide(client, plan, what, body=None, **kw):
    sent = {"json": body} if body is not None else {}
    sent["headers"] = kw.pop("headers", headers())
    return client.post(f"/human/lessons/{plan}/{what}", **sent)


def rows(db, tool):
    return db.cursor(row_factory=dict_row).execute(
        "SELECT * FROM action_log WHERE tool = %s ORDER BY event_id", (tool,)
    ).fetchall()


def unchanged(db, plan):
    got = stored(db, plan)
    assert got["status"] == "proposed" and got["approver_id"] is None
    assert got["final_text"] is None and got["accepted_edit_ids"] is None
    return got


# --------------------------------------------------------------------------- approving
def test_the_author_approves_with_a_good_key_and_the_approver_is_the_person_in_the_token(db):
    plan = two_edits(db)
    answer = decide(client_for(db), plan, "approve", {"accepted_edit_ids": ["e2"]})
    assert answer.status_code == 200, answer.text
    assert answer.json()["status"] == "approved" and answer.json()["left_out_edit_ids"] == ["e1"]
    got = stored(db, plan)
    assert (got["status"], got["approver_id"], got["approver_name"]) == (
        "approved", AUTHOR.id, AUTHOR.name)
    assert got["accepted_edit_ids"] == ["e2"]
    expected = LESSON.replace(A_PLENARY, A_PLENARY + " Ask each pair for one reason.")
    assert got["final_text"] == expected
    assert A_ASK in got["final_text"], "the change that was left out is not in the final text"
    (row,) = rows(db, "lesson_approve_plan")
    assert (row["approver_id"], row["approver_name"], row["outcome"]) == (
        AUTHOR.id, AUTHOR.name, "ok")


def test_a_field_in_the_body_cannot_name_the_approver_and_nothing_is_written(db):
    plan = one_edit(db)
    answer = decide(client_for(db), plan, "approve",
                    {"accepted_edit_ids": ["e1"], "approver_id": "u-9", "approver_name": "X"})
    assert answer.status_code == 422 and answer.json()["reason"] == "unknown_field"
    unchanged(db, plan)
    assert rows(db, "lesson_approve_plan") == []
    (row,) = rows(db, human_routes.REFUSED_TOOL)
    assert row["outcome"] == "refused" and row["outputs"]["reason"] == "unknown_field"


def test_a_person_who_is_not_the_author_is_refused_and_the_diary_names_that_person(db):
    plan = one_edit(db)
    answer = decide(client_for(db), plan, "approve", {"accepted_edit_ids": ["e1"]},
                    headers=headers(OTHER))
    assert answer.status_code == 403 and answer.json()["reason"] == "not_the_author"
    unchanged(db, plan)
    (row,) = rows(db, "lesson_approve_plan")
    assert (row["approver_id"], row["outcome"]) == (OTHER.id, "refused")


def test_a_plan_of_another_school_is_not_found(db):
    plan = one_edit(db)
    answer = decide(client_for(db), plan, "approve", {"accepted_edit_ids": ["e1"]},
                    headers=headers(school="OTHER"))
    assert answer.status_code == 404 and answer.json()["reason"] == "plan_not_found"
    unchanged(db, plan)


COPIED = (
    "school_code, stage, area, term, author_id, author_name, original_text, original_sha256, "
    "objective_text, objective_sha256, template_version, evidence, pins, edits, {check}, "
    "revised_text, revised_sha256, diff, model, prompt_sha256, run_id"
)
CHECK_FAILED = "jsonb_set(check_result, '{{passed}}', 'false'::jsonb)"


def failed_copy(db, plan):
    """A proposed plan like `plan` whose check did not pass. The store never makes one, so it is
    made here by copying a stored row with the passed flag turned off."""
    insert = COPIED.format(check="check_result")
    select = COPIED.format(check=CHECK_FAILED.format())
    return db.execute(
        f"INSERT INTO lesson_plans ({insert}) SELECT {select} FROM lesson_plans "
        "WHERE plan_id = %s RETURNING plan_id",
        (plan,),
    ).fetchone()[0]


def test_a_plan_whose_checks_did_not_pass_cannot_be_approved(db):
    copy = failed_copy(db, one_edit(db))
    assert stored(db, copy)["check_result"]["passed"] is False
    answer = decide(client_for(db), copy, "approve", {"accepted_edit_ids": ["e1"]})
    assert answer.status_code == 409 and answer.json()["reason"] == "approval_conditions_not_met"
    unchanged(db, copy)
    assert rows(db, "lesson_approve_plan")[-1]["outcome"] == "refused"


@pytest.mark.parametrize("accepted", [[], ["e9"], ["e1", "e1"], ["e1", "e9"]],
                         ids=["none", "unknown", "twice", "one unknown"])
def test_approval_needs_at_least_one_accepted_edit_and_each_must_be_one_of_the_plans(db, accepted):
    plan = one_edit(db)
    answer = decide(client_for(db), plan, "approve", {"accepted_edit_ids": accepted})
    assert answer.status_code == 422 and answer.json()["reason"] == "accepted_edits_invalid"
    unchanged(db, plan)
    assert rows(db, "lesson_approve_plan")[-1]["outcome"] == "refused"


def test_approving_twice_is_refused_and_does_not_change_the_plan(db):
    plan = one_edit(db)
    client = client_for(db)
    assert decide(client, plan, "approve", {"accepted_edit_ids": ["e1"]}).status_code == 200
    first = stored(db, plan)
    again = decide(client, plan, "approve", {"accepted_edit_ids": ["e1"]})
    assert again.status_code == 409 and again.json()["reason"] == "plan_not_open"
    assert stored(db, plan)["approved_at"] == first["approved_at"]
    assert [r["outcome"] for r in rows(db, "lesson_approve_plan")] == ["ok", "refused"]


# --------------------------------------------------------------------------- rejecting
def test_the_author_rejects_a_plan_and_it_is_closed(db):
    plan = one_edit(db)
    client = client_for(db)
    answer = decide(client, plan, "reject")
    assert answer.status_code == 200 and answer.json() == {"status": "rejected", "plan_id": plan}
    got = stored(db, plan)
    assert got["status"] == "rejected" and got["approver_id"] is None
    (row,) = rows(db, "lesson_reject_plan")
    assert (row["approver_id"], row["outcome"]) == (AUTHOR.id, "ok")
    late = decide(client, plan, "approve", {"accepted_edit_ids": ["e1"]})
    assert late.status_code == 409 and late.json()["reason"] == "plan_not_open"
    assert decide(client, plan, "reject").json()["reason"] == "plan_not_open"


def test_a_person_who_is_not_the_author_cannot_reject(db):
    plan = one_edit(db)
    answer = decide(client_for(db), plan, "reject", headers=headers(OTHER))
    assert answer.status_code == 403 and answer.json()["reason"] == "not_the_author"
    unchanged(db, plan)


# ----------------------------------------------------------------------- the gate, on a real plan
GATE = {
    "no key": {"X-Human-Route-Key": None},
    "wrong key": {"X-Human-Route-Key": "another-key"},
    "no token": {"X-Approver-Token": None},
    "forged token": {
        "X-Approver-Token": approver_token.sign("not-the-secret", "u-1", "x", school="TST")
    },
    "expired token": {"X-Approver-Token": approver_token.sign(SECRET, "u-1", "Ms Tutor",
                                                              school="TST", now=1_000_000)},
}


@pytest.mark.parametrize("label", list(GATE))
def test_without_the_key_or_a_good_token_a_real_plan_is_left_alone_and_the_refusal_is_logged(
    db, label
):
    plan = one_edit(db)
    for what, body in (("approve", {"accepted_edit_ids": ["e1"]}), ("reject", None)):
        answer = decide(client_for(db), plan, what, body, headers=headers(**GATE[label]))
        assert answer.status_code in (401, 403) and answer.json()["status"] == "refused"
    unchanged(db, plan)
    assert rows(db, "lesson_approve_plan") == [] and rows(db, "lesson_reject_plan") == []
    assert len(rows(db, human_routes.REFUSED_TOOL)) == 2


def test_with_the_key_unset_a_real_plan_cannot_be_approved(db):
    plan = one_edit(db)
    client = client_for(db, HUMAN_ROUTE_KEY="")
    answer = decide(client, plan, "approve", {"accepted_edit_ids": ["e1"]})
    assert answer.status_code == 503 and answer.json()["reason"] == "routes_off"
    unchanged(db, plan)


# --------------------------------------------------------------------------- reading and the lists
def test_the_author_reads_the_plan_and_nobody_else_does(db):
    plan = one_edit(db)
    mine = client_for(db).get(f"/human/lessons/{plan}", headers=headers())
    assert mine.status_code == 200 and mine.json()["plan"]["plan_status"] == "proposed"
    assert mine.json()["plan"]["edits"][0]["evidence"][0].startswith("Evidence (literacy")
    other = client_for(db).get(f"/human/lessons/{plan}", headers=headers(OTHER))
    assert other.status_code == 403 and other.json()["reason"] == "not_the_author"


def test_the_options_come_from_the_active_rulebook_and_the_school_from_the_token(db):
    got = client_for(db).get("/human/lessons/options", headers=headers()).json()
    assert got["status"] == "ok" and got["school"] == SCHOOL
    assert [s["stage"] for s in got["stages"]] == ["Form 1", "Form 3"]
    assert got["terms"] == [1, 2, 3]


@pytest.mark.skipif(not hasattr(store, "list_lesson_plans"),
                    reason="the store has no list function yet (T-062 Needs decision d)")
def test_the_list_holds_only_my_plans_newest_first(db):
    class_read(db)
    first = save(db)["plan_id"]
    second = save(db)["plan_id"]
    save(db, who=OTHER)  # another author's plan is not in my list
    plans = client_for(db).get("/human/lessons", headers=headers()).json()["plans"]
    assert [p["plan_id"] for p in plans] == [second, first]


# ----------------------------------------------------------------------- the words are the store's
def refusal(result):
    assert result["status"] == "refused", result
    return human_routes.type_refusal(result["reason"])[0]


def test_every_refusal_the_store_gives_is_one_the_route_knows_how_to_type(db):
    """The route types a refusal from the store's own words. If those words change, this fails."""
    plan = one_edit(db)
    approve = store.approve_lesson_plan
    assert refusal(approve(ctx(db, OTHER), plan_id=plan, accepted_edit_ids=["e1"])) == (
        "not_the_author")
    assert refusal(approve(ctx(db), plan_id="LP-999999", accepted_edit_ids=["e1"])) == (
        "plan_not_found")
    assert refusal(approve(ctx(db), plan_id=plan, accepted_edit_ids=[])) == (
        "accepted_edits_invalid")
    assert refusal(approve(ctx(db), plan_id=plan, accepted_edit_ids=["e9"])) == (
        "accepted_edits_invalid")
    assert refusal(approve(ctx(db, None), plan_id=plan, accepted_edit_ids=["e1"])) == (
        "not_signed_in")
    assert refusal(approve(ctx(db, school=None), plan_id=plan, accepted_edit_ids=["e1"])) == (
        "no_school")
    copy = failed_copy(db, plan)
    assert refusal(approve(ctx(db), plan_id=copy, accepted_edit_ids=["e1"])) == (
        "approval_conditions_not_met")
    assert approve(ctx(db), plan_id=plan, accepted_edit_ids=["e1"])["status"] == "approved"
    assert refusal(approve(ctx(db), plan_id=plan, accepted_edit_ids=["e1"])) == "plan_not_open"
    other = one_edit(db)
    assert store.reject_lesson_plan(ctx(db), plan_id=other)["status"] == "rejected"
    assert refusal(store.reject_lesson_plan(ctx(db), plan_id=other)) == "plan_not_open"
