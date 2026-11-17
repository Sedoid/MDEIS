"""`REVIEWERS_ARE_APPROVERS` against Postgres (T-077), on a scratch schema, via the real registry.

The same set-up as the teacher demo tests (tests/unit/demokit.py): versions are made by the
registry's own functions and walked to the demo. Here the person who acts is a signed-in approver
who is NOT in `registry_reviewers`. With the setting off that person is refused, as before. With it
on, a person whose token carries a school can list, open, try, save a case, confirm, reject and
clear a hold, and the registry still holds every condition: a missing condition still blocks an
approval, the automated review still only holds, a hold is cleared only by an explicit call of a
signed-in person, and a session with no school or no token is refused.

Each test that needs a new person makes one (`fresh_person`), because the world is shared and a
person who was accepted once has a row from then on. Expected values are written by hand.
"""

import itertools
import os

import pytest

pytest.importorskip("psycopg")

import demokit  # noqa: E402
import psycopg  # noqa: E402
from demokit import REVOKED, SECOND, at_demo, ctx_for, log_rows, status_of  # noqa: E402
from inprocess_runner import Calls  # noqa: E402

from mdeis import registry  # noqa: E402
from mdeis.measure_demo import api  # noqa: E402
from mdeis.sandbox import client  # noqa: E402
from mdeis.tools.context import Approver  # noqa: E402

pytestmark = pytest.mark.integration
FLAG = "REVIEWERS_ARE_APPROVERS"
GRANTED_BY = "auto:REVIEWERS_ARE_APPROVERS"
AUTO_TOOL = "registry_auto_register"
NAMES = itertools.count(1)
PEOPLE = itertools.count(1)
FORM_1 = {"stage": "Form 1"}
FUNCTIONS = ["list_awaiting", "open_demo", "try_measure", "save_case", "confirm", "reject"]
SCREEN_TOOL = {
    "list_awaiting": "measure_demo_list", "open_demo": "measure_demo_open",
    "try_measure": "measure_demo_try",
}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    base = os.environ.get("DATABASE_URL")
    if not base:
        pytest.skip("DATABASE_URL is not set: integration tests skipped")
    name, url = demokit.setup(base, tmp_path_factory.mktemp("variants"))
    try:
        yield url
    finally:
        demokit.drop_schema(base, name)


@pytest.fixture
def runner(monkeypatch):
    spy = Calls()
    monkeypatch.setattr(client, "run", spy)
    return spy


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(FLAG, "true")


def fresh_person() -> Approver:
    n = next(PEOPLE)
    return Approver(f"u-approver-{n}", f"Approver Number {n}")


def demo_version(url, **more):
    return at_demo(url, name=f"Approver measure {next(NAMES)}", **more)


def ref(which):
    return {"measure_id": which["measure_id"], "version": which["version"]}


def entries(subject, *scores):
    return [{"subject": subject, "term": i, "score": s} for i, s in enumerate(scores, start=1)]


def call(url, who, name, school="BAO", **arguments):
    return getattr(api, name)(ctx_for(url, who, school), **arguments)


def arguments_for(name, which):
    given = ref(which)
    if name == "list_awaiting":
        return {}
    if name == "open_demo":
        return given
    if name == "try_measure":
        return {**given, **FORM_1, "entries": entries("English", "52", "58", "63")}
    if name == "save_case":
        return {**given, **FORM_1, "entries": entries("English", "52", "58", "63"),
                "expected": {"change": "11"}}
    if name == "confirm":
        return {**given, "note": None}
    return {**given, "note": "no"}


def rows_for(url, user_id):
    """(role, name, granted_by, ended) for each registry row of one person, by role."""
    with psycopg.connect(url) as conn:
        return conn.execute(
            "SELECT role, name, granted_by, revoked_at IS NOT NULL FROM registry_reviewers "
            "WHERE user_id = %s ORDER BY role",
            (user_id,),
        ).fetchall()


def auto_log(url, who):
    return [r for r in log_rows(url, AUTO_TOOL) if r[4] == who.id]


def held(world_url, **more):
    return demo_version(world_url, review="hold", reasons=("the quote does not say 10",), **more)


def hold_state(url, which):
    with psycopg.connect(url) as conn:
        return conn.execute(
            "SELECT status, review_result, hold_cleared_by_id, hold_cleared_name "
            "FROM measure_versions WHERE measure_id = %s AND version = %s",
            (which["measure_id"], which["version"]),
        ).fetchone()


# ======================================================================== the setting is off
@pytest.mark.parametrize("value", [None, "", "false", "no", "0"],
                         ids=["unset", "empty", "false", "no", "zero"])
@pytest.mark.parametrize("name", FUNCTIONS)
def test_with_the_setting_off_a_signed_in_approver_who_is_not_registered_is_refused_as_before(
    world, runner, monkeypatch, name, value
):
    if value is not None:
        monkeypatch.setenv(FLAG, value)
    who = fresh_person()
    which = demo_version(world)
    got = call(world, who, name, **arguments_for(name, which))
    assert got["status"] == "refused"
    assert "the signed-in person is not recorded as a teacher in the registry" in got["reason"]
    assert rows_for(world, who.id) == []
    assert auto_log(world, who) == []
    assert status_of(world, which)["status"] == "awaiting_demo"


def test_with_the_setting_off_clearing_a_hold_is_still_for_a_registered_developer_only(world):
    who = fresh_person()
    which = held(world)
    got = registry.clear_hold(ctx_for(world, who), **ref(which), note="read it")
    assert got["status"] == "refused" and "not recorded as a developer" in got["reason"]
    assert rows_for(world, who.id) == []
    assert hold_state(world, which)[2] is None


# ======================================================================== the setting is on
@pytest.mark.parametrize("name", ["list_awaiting", "open_demo", "try_measure"])
def test_with_it_on_listing_opening_and_trying_work_for_an_unregistered_approver_and_write_no_row(
    world, runner, on, name
):
    who = fresh_person()
    which = demo_version(world)
    got = call(world, who, name, **arguments_for(name, which))
    assert got["status"] == "ok", got
    assert rows_for(world, who.id) == []  # reading makes no record
    (line,) = [r for r in log_rows(world, SCREEN_TOOL[name]) if r[4] == who.id]
    assert line[3] == "ok" and line[5] == who.name


def test_with_it_on_the_list_shows_the_versions_waiting(world, runner, on):
    which = demo_version(world)
    got = call(world, fresh_person(), "list_awaiting")
    assert which["measure_id"] in [m["measure_id"] for m in got["measures"]]


def test_with_it_on_a_teacher_case_is_saved_and_the_person_gets_one_teacher_row_and_one_log_line(
    world, runner, on
):
    who = fresh_person()
    which = demo_version(world, independent=False)
    first = call(world, who, "save_case", **arguments_for("save_case", which))
    assert first["status"] == "saved" and first["name"] == "teacher case 1"
    assert rows_for(world, who.id) == [("teacher", who.name, GRANTED_BY, False)]
    (line,) = auto_log(world, who)
    assert line[3] == "ok" and line[5] == who.name
    assert line[2]["action"] == "auto-registered by REVIEWERS_ARE_APPROVERS"
    assert line[1]["user_id"] == who.id and line[1]["role"] == "teacher"
    assert line[1]["school"] == "BAO" and line[1]["setting"] == FLAG
    again = call(world, who, "save_case", **arguments_for("save_case", which))
    assert again["status"] == "saved" and again["name"] == "teacher case 2"
    assert rows_for(world, who.id) == [("teacher", who.name, GRANTED_BY, False)]  # idempotent
    assert len(auto_log(world, who)) == 1  # one line only


def test_with_it_on_confirming_approves_and_records_the_signed_in_approver_by_name(
    world, runner, on
):
    who = fresh_person()
    which = demo_version(world)
    done = call(world, who, "confirm", **ref(which), note="checked against my own marks")
    assert done["status"] == "confirmed", done
    assert done["teacher_name"] == who.name and done["values_confirmed_by"] == who.name
    after = status_of(world, which)
    assert after["status"] == "approved" and after["decision"] == "confirmed"
    assert after["teacher"] == who.name and after["note"] == "checked against my own marks"
    assert rows_for(world, who.id) == [("teacher", who.name, GRANTED_BY, False)]
    assert len(auto_log(world, who)) == 1
    (line,) = [r for r in log_rows(world, "registry_record_teacher_decision") if r[4] == who.id]
    assert line[3] == "ok" and line[5] == who.name
    (screen,) = [r for r in log_rows(world, "measure_demo_confirm") if r[4] == who.id]
    assert screen[3] == "ok" and screen[5] == who.name


def test_the_person_recorded_on_the_decision_is_the_one_in_the_token_not_another_person(
    world, runner, on
):
    first, second = fresh_person(), fresh_person()
    one, two = demo_version(world), demo_version(world)
    call(world, first, "confirm", **ref(one), note=None)
    call(world, second, "reject", **ref(two), note="the numbers do not match my marks")
    assert status_of(world, one)["teacher"] == first.name
    assert status_of(world, two)["teacher"] == second.name
    with psycopg.connect(world) as conn:
        ids = dict(conn.execute(
            "SELECT measure_id, teacher_id FROM measure_versions WHERE measure_id = ANY(%s)",
            ([one["measure_id"], two["measure_id"]],),
        ).fetchall())
    assert ids == {one["measure_id"]: first.id, two["measure_id"]: second.id}


@pytest.mark.parametrize("name", ["confirm", "reject", "save_case", "try_measure", "open_demo"])
def test_no_function_takes_an_argument_that_could_name_the_approver_even_with_the_setting_on(
    world, runner, on, name
):
    which = demo_version(world)
    who = fresh_person()
    for extra in ("approver", "approver_id", "approver_name", "teacher", "user_id", "school"):
        with pytest.raises(TypeError):
            getattr(api, name)(ctx_for(world, who), **arguments_for(name, which), **{extra: "x"})
    assert rows_for(world, who.id) == []


def test_with_it_on_rejecting_needs_a_note_and_a_refusal_makes_no_row(world, runner, on):
    who = fresh_person()
    which = demo_version(world)
    got = call(world, who, "reject", **ref(which), note="  ")
    assert got["status"] == "refused" and "note_needed" in got["reason"]
    assert rows_for(world, who.id) == [] and status_of(world, which)["status"] == "awaiting_demo"
    done = call(world, who, "reject", **ref(which), note="the answer is not what I expected")
    assert done["status"] == "rejected" and done["teacher_name"] == who.name
    assert status_of(world, which)["status"] == "rejected"
    assert rows_for(world, who.id) == [("teacher", who.name, GRANTED_BY, False)]


def test_a_case_that_does_not_match_is_refused_and_makes_no_row(world, runner, on):
    who = fresh_person()
    which = demo_version(world, independent=False)
    args = {**arguments_for("save_case", which), "expected": {"change": "99"}}
    got = call(world, who, "save_case", **args)
    assert got["status"] == "refused" and "case_mismatch" in got["reason"]
    assert rows_for(world, who.id) == [] and auto_log(world, who) == []


def test_a_person_the_registry_already_lists_is_used_as_listed_and_gets_no_second_row(
    world, runner, on
):
    token = Approver(SECOND.id, "A Different Spelling")
    before = rows_for(world, SECOND.id)
    which = demo_version(world)
    done = call(world, token, "confirm", **ref(which), note=None)
    assert done["status"] == "confirmed" and done["teacher_name"] == SECOND.name
    assert status_of(world, which)["teacher"] == SECOND.name  # the registry's name, as before
    assert rows_for(world, SECOND.id) == before
    assert auto_log(world, token) == []


# ======================================================================== still refused
@pytest.mark.parametrize("name", FUNCTIONS)
@pytest.mark.parametrize("who,school,words", [
    (None, "BAO", "no one is signed in"),
    ("fresh", None, "no school is attached"),
    ("fresh", "", "no school is attached"),
    ("fresh", "   ", "no school is attached"),
], ids=["no token", "no school", "empty school", "blank school"])
def test_with_it_on_no_token_and_no_school_are_still_refused(
    world, runner, on, name, who, school, words
):
    person = fresh_person() if who == "fresh" else None
    which = demo_version(world)
    got = call(world, person, name, school, **arguments_for(name, which))
    assert got["status"] == "refused" and words in got["reason"]
    if person is not None:
        assert rows_for(world, person.id) == []
    assert status_of(world, which)["status"] == "awaiting_demo"


@pytest.mark.parametrize("school", [None, ""])
def test_the_registry_itself_refuses_an_unregistered_approver_with_no_school_even_with_it_on(
    world, runner, on, school
):
    who = fresh_person()
    which = demo_version(world)
    got = registry.record_teacher_decision(
        ctx_for(world, who, school), **ref(which), decision="confirmed")
    assert got["status"] == "refused" and "no school is attached" in got["reason"]
    assert rows_for(world, who.id) == []
    waiting = held(world)
    cleared = registry.clear_hold(ctx_for(world, who, school), **ref(waiting), note="read it")
    assert cleared["status"] == "refused" and "no school is attached" in cleared["reason"]
    assert rows_for(world, who.id) == []


@pytest.mark.parametrize("name", ["list_awaiting", "confirm", "reject", "save_case"])
def test_with_it_on_a_person_whose_record_an_administrator_ended_is_still_refused(
    world, runner, on, name
):
    which = demo_version(world)
    before = rows_for(world, REVOKED.id)
    got = call(world, REVOKED, name, **arguments_for(name, which))
    assert got["status"] == "refused"
    assert "not recorded as a teacher" in got["reason"] and "ended" in got["reason"]
    assert "approver" in got["reason"] and FLAG in got["reason"]
    assert rows_for(world, REVOKED.id) == before  # nothing re-granted
    assert status_of(world, which)["status"] == "awaiting_demo"


def test_with_it_on_a_developer_record_that_was_ended_does_not_come_back_by_clearing_a_hold(
    world, on
):
    who = fresh_person()
    with psycopg.connect(world) as conn:
        conn.execute(
            "INSERT INTO registry_reviewers (user_id, role, name, granted_by, revoked_at) "
            "VALUES (%s, 'developer', %s, 'admin', now())", (who.id, who.name))
        conn.commit()
    which = held(world)
    got = registry.clear_hold(ctx_for(world, who), **ref(which), note="read it")
    assert got["status"] == "refused" and "ended that record" in got["reason"]
    assert hold_state(world, which)[2] is None
    assert rows_for(world, who.id) == [("developer", who.name, "admin", True)]


# ======================================================================== the hold
def test_the_automated_review_still_only_holds_and_a_person_cannot_pose_as_it(world, on):
    which = demo_version(world, review="hold", reasons=("a reason",))
    # the review is recorded once, by the system; a signed-in approver cannot record one
    who = fresh_person()
    spoof = registry.record_review(ctx_for(world, who), **ref(which), result="pass")
    assert spoof["status"] == "refused" and "system step" in spoof["reason"]
    again = registry.record_review(ctx_for(world, None), **ref(which), result="pass")
    assert again["status"] == "refused" and "already recorded" in again["reason"]
    assert hold_state(world, which)[:3] == ("awaiting_review", "hold", None)
    assert rows_for(world, who.id) == []


def test_a_hold_is_not_cleared_by_the_system_by_a_demo_call_or_by_time(world, runner, on):
    which = held(world)
    system = registry.clear_hold(ctx_for(world, None), **ref(which), note="auto")
    assert system["status"] == "refused" and "no one is signed in" in system["reason"]
    who = fresh_person()
    for name in ("confirm", "reject", "open_demo", "try_measure", "save_case"):
        got = call(world, who, name, **arguments_for(name, which))
        assert got["status"] == "refused" and "not_at_demo" in got["reason"], name
    moved = registry.advance_status(ctx_for(world, None), **ref(which), to="awaiting_demo")
    assert moved["status"] == "refused" and "automated review is holding" in moved["reason"]
    assert hold_state(world, which)[:3] == ("awaiting_review", "hold", None)
    assert rows_for(world, who.id) == []


def test_an_approver_clears_a_hold_only_by_an_explicit_call_with_a_note_and_is_recorded(
    world, runner, on
):
    which = held(world)
    who = fresh_person()
    blank = registry.clear_hold(ctx_for(world, who), **ref(which), note="  ")
    assert blank["status"] == "refused" and "note" in blank["reason"]
    assert rows_for(world, who.id) == [] and hold_state(world, which)[2] is None
    cleared = registry.clear_hold(ctx_for(world, who), **ref(which), note="read the section")
    assert cleared["status"] == "ok" and cleared["cleared_by"] == who.id
    status, result, by_id, by_name = hold_state(world, which)
    assert (status, result, by_id, by_name) == ("awaiting_review", "hold", who.id, who.name)
    assert rows_for(world, who.id) == [("developer", who.name, GRANTED_BY, False)]
    (line,) = auto_log(world, who)
    assert line[1]["role"] == "developer" and line[2]["role"] == "developer"
    (diary,) = [r for r in log_rows(world, "registry_clear_hold")
                if r[4] == who.id and r[3] == "ok"]
    assert diary[5] == who.name
    twice = registry.clear_hold(ctx_for(world, who), **ref(which), note="again")
    assert twice["status"] == "refused" and "already cleared" in twice["reason"]
    # clearing a hold approves nothing: the version still goes to the demo and then to a decision
    assert status_of(world, which)["decision"] is None
    moved = registry.advance_status(ctx_for(world, None), **ref(which), to="awaiting_demo")
    assert moved["status"] == "ok"
    done = call(world, who, "confirm", **ref(which), note=None)
    assert done["status"] == "confirmed"
    assert status_of(world, which)["teacher"] == who.name
    assert sorted(r[0] for r in rows_for(world, who.id)) == ["developer", "teacher"]


def test_there_is_no_hold_to_clear_on_a_version_whose_review_passed(world, on):
    which = demo_version(world)  # review passed
    who = fresh_person()
    got = registry.clear_hold(ctx_for(world, who), **ref(which), note="read it")
    assert got["status"] == "refused" and "no hold on this version" in got["reason"]
    assert rows_for(world, who.id) == []


# ====================================================================== the database still decides
def test_a_missing_approval_condition_still_blocks_with_the_setting_on_and_leaves_no_row(
    world, runner, on
):
    who = fresh_person()
    which = demo_version(world, independent=False)  # no test from outside the build
    shown = call(world, who, "confirm", **ref(which), note=None)
    assert shown["status"] == "refused" and "[approval_conditions_not_met]" in shown["reason"]
    assert [g["code"] for g in shown["gaps"]] == ["no_independent_test"]
    # past the screen's own check, straight at the registry: the database refuses the approval
    direct = registry.record_teacher_decision(
        ctx_for(world, who), **ref(which), decision="confirmed", note=None)
    assert direct["status"] == "refused" and "the registry refused it" in direct["reason"]
    after = status_of(world, which)
    assert after["status"] == "awaiting_demo" and after["decision"] is None
    # the refused approval took the person's row with it, and nothing says they were registered
    assert rows_for(world, who.id) == []
    assert auto_log(world, who) == []


def test_the_database_itself_knows_nothing_of_the_setting(world, on):
    which = demo_version(world)
    stranger = fresh_person()
    with psycopg.connect(world) as conn:
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute(
                "UPDATE measure_versions SET status = 'approved', teacher_id = %s, "
                "teacher_name = %s, teacher_decision = 'confirmed', teacher_decided_at = now(), "
                "approved_at = now() WHERE measure_id = %s AND version = %s",
                (stranger.id, stranger.name, which["measure_id"], which["version"]),
            )
    assert status_of(world, which)["status"] == "awaiting_demo"


def test_the_approval_with_everything_met_still_needs_the_teacher_decision_to_be_confirmed(
    world, runner, on
):
    who = fresh_person()
    which = demo_version(world)
    rejected = call(world, who, "reject", **ref(which), note="not what the methodology says")
    assert rejected["status"] == "rejected"
    after = status_of(world, which)
    assert after["status"] == "rejected" and after["decision"] == "rejected"
    # a rejected version is closed: it cannot then be confirmed
    late = call(world, who, "confirm", **ref(which), note=None)
    assert late["status"] == "refused" and "not_at_demo" in late["reason"]
    assert status_of(world, which)["status"] == "rejected"
