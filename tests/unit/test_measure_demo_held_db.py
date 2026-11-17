"""The approver's screens for a hold (T-077), against Postgres on a scratch schema, through the real
registry. `list_held` shows the versions the automated review holds. `lift_hold` clears one hold,
with a note, by a named signed-in person.

What is held here:

* only a person recorded as a developer (or, with `REVIEWERS_ARE_APPROVERS` on, a signed-in approver
  with a school and no ended record) can list or clear; a teacher alone, a stranger, no token and no
  school are refused, and nothing changes;
* a note is needed; a version that is not held (a pass, a cleared hold, one at the demo) has no hold
  to clear;
* clearing removes the hold and nothing else: it records the person's id, name and the note, moves
  the version on to the demo, approves nothing and records no teacher decision; the teacher still
  decides, and the database's conditions still apply;
* the automated review can still only hold (a signed-in person cannot record one);
* every call is one action-log row with the signed-in person, and the clearing is also the
  registry's own row.

Expected values are written by hand. The sandbox is not needed (nothing here runs a function).
"""

import itertools
import os

import pytest

pytest.importorskip("psycopg")

import demokit  # noqa: E402
import psycopg  # noqa: E402
from demokit import DEVELOPER, SECOND, at_demo, ctx_for, log_rows, status_of  # noqa: E402

from mdeis import registry  # noqa: E402
from mdeis.measure_demo import api  # noqa: E402
from mdeis.tools.context import Approver  # noqa: E402

pytestmark = pytest.mark.integration
FLAG = "REVIEWERS_ARE_APPROVERS"
NAMES = itertools.count(1)
PEOPLE = itertools.count(1)
REASONS = ("the quote does not say 10", "the code reads a field the quote does not name")


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
def on(monkeypatch):
    monkeypatch.setenv(FLAG, "true")


def fresh_person() -> Approver:
    n = next(PEOPLE)
    return Approver(f"u-holder-{n}", f"Holder Number {n}")


def held(url, **more):
    return at_demo(url, name=f"Held measure {next(NAMES)}", review="hold", reasons=REASONS, **more)


def ref(which):
    return {"measure_id": which["measure_id"], "version": which["version"]}


def call(url, who, name, school="BAO", **arguments):
    return getattr(api, name)(ctx_for(url, who, school), **arguments)


def state(url, which):
    with psycopg.connect(url) as conn:
        return conn.execute(
            "SELECT status, review_result, hold_cleared_by_id, hold_cleared_name, "
            "hold_cleared_note, teacher_id, teacher_decision, approved_at IS NOT NULL "
            "FROM measure_versions WHERE measure_id = %s AND version = %s",
            (which["measure_id"], which["version"]),
        ).fetchone()


def reviewer_rows(url, user_id):
    with psycopg.connect(url) as conn:
        return conn.execute(
            "SELECT role, name, granted_by, revoked_at IS NOT NULL FROM registry_reviewers "
            "WHERE user_id = %s ORDER BY role",
            (user_id,),
        ).fetchall()


# ======================================================================== the list
def test_the_list_shows_a_held_version_with_the_reasons_and_nothing_about_a_person(world):
    which = held(world)
    got = call(world, DEVELOPER, "list_held")
    assert got["status"] == "ok" and got["held"] == len(got["measures"])
    (item,) = [m for m in got["measures"] if m["measure_id"] == which["measure_id"]]
    assert item["version"] == which["version"]
    assert item["reasons"] == list(REASONS)
    assert set(item) == {"measure_id", "version", "name", "method_ref", "description", "reasons"}
    (line,) = [r for r in log_rows(world, "measure_demo_held") if r[4] == "u-dev"][-1:]
    assert line[3] == "ok" and line[5] == "Dev One"


def test_the_list_leaves_out_a_pass_a_cleared_hold_and_a_version_at_the_demo(world):
    passed = at_demo(world, name=f"Passed measure {next(NAMES)}")
    cleared = held(world, clear_hold=True)
    waiting = held(world)
    ids = {(m["measure_id"], m["version"]) for m in call(world, DEVELOPER, "list_held")["measures"]}
    assert (waiting["measure_id"], waiting["version"]) in ids
    assert (passed["measure_id"], passed["version"]) not in ids
    assert (cleared["measure_id"], cleared["version"]) not in ids


def test_listing_changes_nothing(world):
    which = held(world)
    before = state(world, which)
    call(world, DEVELOPER, "list_held")
    assert state(world, which) == before


# ======================================================================== who may
@pytest.mark.parametrize("name", ["list_held", "lift_hold"])
@pytest.mark.parametrize("who,school,words", [
    (None, "BAO", "no one is signed in"),
    (DEVELOPER, None, "no school is attached"),
    (DEVELOPER, "", "no school is attached"),
    (SECOND, "BAO", "not recorded as a developer"),  # a teacher is not a developer
    (Approver("u-nobody-here", "Nobody"), "BAO", "not recorded as a developer"),
], ids=["no token", "no school", "empty school", "teacher only", "stranger"])
def test_only_a_registered_developer_with_a_school_may_list_or_clear_a_hold(
    world, name, who, school, words
):
    which = held(world)
    arguments = {} if name == "list_held" else {**ref(which), "note": "read it"}
    got = call(world, who, name, school, **arguments)
    assert got["status"] == "refused" and words in got["reason"]
    if who is not None and who.id == "u-nobody-here":
        assert "[not_a_developer]" in got["reason"]
    assert state(world, which)[:3] == ("awaiting_review", "hold", None)
    assert reviewer_rows(world, "u-nobody-here") == []


# ======================================================================== clearing
def test_clearing_removes_the_hold_records_the_person_and_the_note_and_approves_nothing(world):
    which = held(world)
    got = call(world, DEVELOPER, "lift_hold", **ref(which), note="  read section 6.4, it says 10  ")
    assert got["status"] == "cleared", got
    assert (got["from_status"], got["to_status"]) == ("awaiting_review", "awaiting_demo")
    assert got["cleared_by_name"] == "Dev One" and got["note"] == "read section 6.4, it says 10"
    assert "Nothing is approved" in got["next_step"] and "teacher" in got["next_step"]
    status, review, by_id, by_name, note, teacher, decision, approved = state(world, which)
    assert (status, review, by_id, by_name) == ("awaiting_demo", "hold", "u-dev", "Dev One")
    assert note == "read section 6.4, it says 10"
    assert teacher is None and decision is None and approved is False  # nothing approved
    lifts = log_rows(world, "measure_demo_lift_hold")
    ours = [r for r in lifts if r[3] == "ok" and r[4] == "u-dev"]
    assert ours and ours[-1][5] == "Dev One"
    theirs = [r for r in log_rows(world, "registry_clear_hold") if r[3] == "ok" and r[4] == "u-dev"]
    assert theirs, "the registry's own row is there too"


def test_after_clearing_the_version_is_at_the_demo_and_a_teacher_still_decides(world):
    which = held(world)
    assert call(world, DEVELOPER, "lift_hold", **ref(which), note="checked")["status"] == "cleared"
    listed = call(world, SECOND, "list_awaiting")
    assert which["measure_id"] in [m["measure_id"] for m in listed["measures"]]
    assert status_of(world, which)["decision"] is None
    done = call(world, SECOND, "confirm", **ref(which), note=None)
    assert done["status"] == "confirmed"
    assert status_of(world, which)["status"] == "approved"
    assert status_of(world, which)["teacher"] == "Mr Second"


def test_a_note_is_needed_and_a_refusal_changes_nothing(world):
    which = held(world)
    for note in (None, "", "   "):
        got = call(world, DEVELOPER, "lift_hold", **ref(which), note=note)
        assert got["status"] == "refused" and "note_needed" in got["reason"]
    got = call(world, DEVELOPER, "lift_hold", **ref(which), note=5)
    assert got["status"] == "refused" and "bad_body" in got["reason"]
    assert state(world, which)[:3] == ("awaiting_review", "hold", None)
    refused = [r for r in log_rows(world, "measure_demo_lift_hold") if r[3] == "refused"]
    assert refused and refused[-1][4] == "u-dev"


@pytest.mark.parametrize("make", [
    lambda url: at_demo(url, name=f"Pass measure {next(NAMES)}"),
    lambda url: held(url, clear_hold=True),
], ids=["a pass", "already cleared"])
def test_a_version_that_is_not_held_has_no_hold_to_clear(world, make):
    which = make(world)
    before = state(world, which)
    got = call(world, DEVELOPER, "lift_hold", **ref(which), note="again")
    assert got["status"] == "refused" and "[not_held]" in got["reason"]
    assert state(world, which) == before


def test_clearing_twice_is_refused_the_second_time(world):
    which = held(world)
    assert call(world, DEVELOPER, "lift_hold", **ref(which), note="one")["status"] == "cleared"
    again = call(world, DEVELOPER, "lift_hold", **ref(which), note="two")
    assert again["status"] == "refused" and "[not_held]" in again["reason"]
    assert state(world, which)[4] == "one"


@pytest.mark.parametrize("measure_id,version", [("MS-9999", 1), (None, 1), ("MS-0001", "1")])
def test_an_unknown_version_is_a_plain_refusal(world, measure_id, version):
    got = call(world, DEVELOPER, "lift_hold", measure_id=measure_id, version=version, note="n")
    assert got["status"] == "refused" and "not_found" in got["reason"]


def test_the_registry_alone_still_clears_without_moving_the_version(world):
    """The default of `clear_hold` is unchanged: it clears the hold and the version stays where it
    is until the system moves it."""
    which = held(world)
    got = registry.clear_hold(ctx_for(world, DEVELOPER), **ref(which), note="read it")
    assert got["status"] == "ok" and got["version_status"] == "awaiting_review"
    assert state(world, which)[0] == "awaiting_review"
    moved = registry.clear_hold(ctx_for(world, DEVELOPER), **ref(which), note="again")
    assert moved["status"] == "refused" and "already cleared" in moved["reason"]


def test_the_move_to_the_demo_is_part_of_clearing_and_needs_a_cleared_hold(world):
    which = held(world)
    stranger = Approver("u-no-row", "No Row")
    refused = registry.clear_hold(
        ctx_for(world, stranger), **ref(which), note="read it", to_demo=True)
    assert refused["status"] == "refused"
    assert state(world, which)[:3] == ("awaiting_review", "hold", None)
    system = registry.clear_hold(ctx_for(world, None), **ref(which), note="auto", to_demo=True)
    assert system["status"] == "refused" and "no one is signed in" in system["reason"]
    blocked = registry.advance_status(ctx_for(world, None), **ref(which), to="awaiting_demo")
    assert blocked["status"] == "refused" and "holding" in blocked["reason"]
    assert state(world, which)[:3] == ("awaiting_review", "hold", None)


def test_the_automated_review_still_cannot_record_a_pass_over_a_hold_or_as_a_person(world):
    which = held(world)
    spoof = registry.record_review(ctx_for(world, DEVELOPER), **ref(which), result="pass")
    assert spoof["status"] == "refused" and "system step" in spoof["reason"]
    again = registry.record_review(ctx_for(world, None), **ref(which), result="pass")
    assert again["status"] == "refused" and "already recorded" in again["reason"]
    assert state(world, which)[:3] == ("awaiting_review", "hold", None)


# ======================================================================== the setting on
def test_with_the_setting_off_an_unregistered_approver_cannot_list_or_clear(world):
    who = fresh_person()
    which = held(world)
    assert call(world, who, "list_held")["status"] == "refused"
    got = call(world, who, "lift_hold", **ref(which), note="read it")
    assert got["status"] == "refused" and "[not_a_developer]" in got["reason"]
    assert reviewer_rows(world, who.id) == []
    assert state(world, which)[:3] == ("awaiting_review", "hold", None)


def test_with_it_on_an_approver_with_a_school_lists_and_clears_and_is_recorded(world, on):
    who = fresh_person()
    which = held(world)
    got = call(world, who, "list_held")
    assert got["status"] == "ok"
    assert which["measure_id"] in [m["measure_id"] for m in got["measures"]]
    assert reviewer_rows(world, who.id) == []  # listing makes no record
    done = call(world, who, "lift_hold", **ref(which), note="read the section and the code")
    assert done["status"] == "cleared" and done["cleared_by_name"] == who.name
    status, review, by_id, by_name, note, teacher, decision, approved = state(world, which)
    assert (status, review, by_id, by_name) == ("awaiting_demo", "hold", who.id, who.name)
    assert note == "read the section and the code"
    assert teacher is None and decision is None and approved is False
    assert reviewer_rows(world, who.id) == [
        ("developer", who.name, "auto:REVIEWERS_ARE_APPROVERS", False)]
    autos = [r for r in log_rows(world, "registry_auto_register") if r[4] == who.id]
    assert len(autos) == 1 and autos[0][1]["role"] == "developer"


@pytest.mark.parametrize("school", [None, "", "  "])
def test_with_it_on_no_school_is_still_refused_and_nothing_is_made(world, on, school):
    who = fresh_person()
    which = held(world)
    for name, arguments in (("list_held", {}), ("lift_hold", {**ref(which), "note": "n"})):
        got = call(world, who, name, school, **arguments)
        assert got["status"] == "refused" and "no school is attached" in got["reason"]
    assert reviewer_rows(world, who.id) == []
    assert state(world, which)[:3] == ("awaiting_review", "hold", None)


def test_with_it_on_no_token_is_still_refused(world, on):
    which = held(world)
    got = call(world, None, "lift_hold", **ref(which), note="n")
    assert got["status"] == "refused" and "no one is signed in" in got["reason"]


def test_with_it_on_an_ended_developer_record_is_still_refused(world, on):
    who = fresh_person()
    with psycopg.connect(world) as conn:
        conn.execute(
            "INSERT INTO registry_reviewers (user_id, role, name, granted_by, revoked_at) "
            "VALUES (%s, 'developer', %s, 'admin', now())", (who.id, who.name))
        conn.commit()
    which = held(world)
    for name, arguments in (("list_held", {}), ("lift_hold", {**ref(which), "note": "n"})):
        got = call(world, who, name, **arguments)
        assert got["status"] == "refused"
        assert "ended" in got["reason"] and "approver" in got["reason"]
    assert state(world, which)[:3] == ("awaiting_review", "hold", None)
    assert reviewer_rows(world, who.id) == [("developer", who.name, "admin", True)]


def test_with_it_on_the_person_recorded_is_the_one_in_the_token_for_each_of_two_holds(world, on):
    first, second = fresh_person(), fresh_person()
    one, two = held(world), held(world)
    call(world, first, "lift_hold", **ref(one), note="first")
    call(world, second, "lift_hold", **ref(two), note="second")
    assert state(world, one)[2:5] == (first.id, first.name, "first")
    assert state(world, two)[2:5] == (second.id, second.name, "second")


@pytest.mark.parametrize("extra", ["approver", "approver_id", "user_id", "school", "status",
                                    "decision", "cleared_by", "to_demo"])
def test_lift_hold_has_no_argument_that_names_a_person_a_school_a_status_or_a_decision(
    world, on, extra
):
    which = held(world)
    with pytest.raises(TypeError):
        api.lift_hold(ctx_for(world, DEVELOPER), **ref(which), note="n", **{extra: "x"})
    assert state(world, which)[:3] == ("awaiting_review", "hold", None)
