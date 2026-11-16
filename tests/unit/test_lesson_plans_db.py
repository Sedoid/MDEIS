"""The lesson_plans constraints in a real Postgres (integration: needs DATABASE_URL), T-060 slice 3.

Migration 009 runs into a throwaway schema; each test has its own connection and rolls back. Two
layers are tested, as for the registry: the guard (a trigger) and the constraints together, by plain
UPDATE statements the way a bug might write them; and the `approved` constraint alone, with the
guard switched off inside the test so that the constraint is the only thing that can refuse the row.
(The role that owns the table can do this too: that is the honest limit the migration states.)
"""

import hashlib

import psycopg
import pytest
from dbtools import one, refused, run_migrate
from psycopg import errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

pytestmark = pytest.mark.integration

CHECK = errors.CheckViolation
GUARD = errors.RestrictViolation
APPROVED_RULE = "approved_needs_approver_and_check"

ORIGINAL = "## Starter\nRead the passage aloud. Ask why.\n"
OBJECTIVE = "Learners explain the main idea."
REVISED = "## Starter\nRead the passage aloud. Ask what and why.\n"
FINDING = "CF-a1b2c3d4"
KEPT = {
    "id": "e1", "type": "reword", "anchor_quote": "Ask why.", "replacement": "Ask what and why.",
    "finding_ids": [FINDING], "resource_ids": [], "suggestion": "Ask for a reason.",
}
KEPT_TWO = {**KEPT, "id": "e2", "anchor_quote": "Read the passage aloud."}
PASSED = {"passed": True, "objective_unchanged": True, "outside_unchanged": True}


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def db(scratch_schema):
    schema, url = scratch_schema
    built = run_migrate(url)
    assert built.returncode == 0, built.stderr
    with psycopg.connect(url) as conn:
        conn.schema, conn.url = schema, url
        conn.execute(
            "INSERT INTO templates (version_name, file_name, sha256, template_id, content, active) "
            "VALUES ('t-lessons', 't.yaml', repeat('a', 64), 'tid', '{}'::jsonb, false)"
        )
        yield conn
        conn.rollback()


@pytest.fixture
def loose(db):
    """The guard is off: only the table's own constraints stand between a row and `approved`."""
    db.execute("ALTER TABLE lesson_plans DISABLE TRIGGER lesson_plans_guard")
    return db


def columns(**over):
    base = {
        "school_code": "TST", "stage": "Form 3", "area": "literacy", "term": 2,
        "author_id": "u-1", "author_name": "Ms Tutor",
        "original_text": ORIGINAL, "original_sha256": sha(ORIGINAL),
        "objective_text": OBJECTIVE, "objective_sha256": sha(OBJECTIVE),
        "template_version": "t-lessons",
        "evidence": [{"finding_id": FINDING, "sentence": "Evidence ..."}],
        "pins": {"template_version": "t-lessons", "template_sha256": "a" * 64},
        "edits": {"kept": [KEPT], "dropped": []},
        "check_result": PASSED,
        "revised_text": REVISED, "revised_sha256": sha(REVISED),
        "diff": {"lines": [], "changes": []},
        "model": "model-x", "prompt_sha256": "b" * 64, "run_id": "RUN-1",
    }
    base.update(over)
    json_columns = {"evidence", "pins", "edits", "check_result", "diff"}
    return {
        k: Jsonb(v) if k in json_columns and v is not None and not isinstance(v, Jsonb) else v
        for k, v in base.items()
    }


def insert_sql(cols):
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    return f"INSERT INTO lesson_plans ({names}) VALUES ({marks}) RETURNING plan_id"


def add(db, **over):
    cols = columns(**over)
    return one(db, insert_sql(cols), list(cols.values()))


def refuse_add(db, error=CHECK, **over):
    cols = columns(**over)
    return refused(db, error, insert_sql(cols), list(cols.values()))


def row(db, plan_id):
    return db.cursor(row_factory=dict_row).execute(
        "SELECT * FROM lesson_plans WHERE plan_id = %s", (plan_id,)
    ).fetchone()


def approve_sql(plan_id, **over):
    cols = {
        "status": "approved", "accepted_edit_ids": ["e1"], "final_text": REVISED,
        "approver_id": "u-1", "approver_name": "Ms Tutor", "approved_at": "now()", **over,
    }
    sets = ", ".join(f"{k} = now()" if v == "now()" else f"{k} = %s" for k, v in cols.items())
    params = [v for v in cols.values() if v != "now()"]
    return f"UPDATE lesson_plans SET {sets} WHERE plan_id = %s", [*params, plan_id]


def refuse_update(db, error, plan_id, **cols):
    sets = ", ".join(f"{k} = %s" for k in cols)
    values = [Jsonb(v) if isinstance(v, (dict, list)) and k in {"evidence", "pins", "edits",
              "check_result", "diff"} else v for k, v in cols.items()]
    return refused(db, error, f"UPDATE lesson_plans SET {sets} WHERE plan_id = %s",
                   [*values, plan_id])


# --------------------------------------------------------------------------- the path
def test_a_plan_with_everything_in_place_starts_as_proposed_with_an_id_in_the_scheme(db):
    plan = add(db)
    assert plan.startswith("LP-") and len(plan) == 9
    got = row(db, plan)
    assert got["status"] == "proposed"
    assert got["approver_id"] is None and got["final_text"] is None
    assert got["accepted_edit_ids"] is None


def test_the_author_approves_their_own_plan_through_the_guard(db):
    plan = add(db)
    sql, params = approve_sql(plan)
    db.execute(sql, params)
    got = row(db, plan)
    assert got["status"] == "approved"
    assert (got["approver_id"], got["approver_name"]) == ("u-1", "Ms Tutor")
    assert got["accepted_edit_ids"] == ["e1"] and got["final_text"] == REVISED
    assert got["approved_at"] is not None


def test_a_plan_can_be_rejected_and_is_then_closed(db):
    plan = add(db)
    db.execute("UPDATE lesson_plans SET status = 'rejected' WHERE plan_id = %s", (plan,))
    assert row(db, plan)["status"] == "rejected"
    refuse_update(db, GUARD, plan, status="proposed")
    refuse_update(db, GUARD, plan, status="approved")
    sql, params = approve_sql(plan)
    refused(db, GUARD, sql, params)
    assert row(db, plan)["status"] == "rejected"


def test_an_approved_plan_can_only_become_superseded_and_then_nothing(db):
    plan = add(db)
    db.execute(*approve_sql(plan))
    refuse_update(db, GUARD, plan, status="proposed")
    refuse_update(db, GUARD, plan, status="rejected")
    refuse_update(db, GUARD, plan, final_text="changed text")
    refuse_update(db, GUARD, plan, accepted_edit_ids=["e1", "e2"])
    db.execute("UPDATE lesson_plans SET status = 'superseded' WHERE plan_id = %s", (plan,))
    assert row(db, plan)["status"] == "superseded"
    refuse_update(db, GUARD, plan, status="approved")
    refuse_update(db, GUARD, plan, final_text="again")


@pytest.mark.parametrize("target", ["superseded", "approved", "weird"])
def test_a_proposed_plan_cannot_skip_to_a_state_that_is_not_a_legal_move(db, target):
    plan = add(db)
    refuse_update(db, (GUARD, CHECK), plan, status=target)
    assert row(db, plan)["status"] == "proposed"


# --------------------------------------------------------------------------- approved, alone
def approved_row(db, **over):
    cols = {
        "status": "approved", "accepted_edit_ids": ["e1"], "final_text": REVISED,
        "approver_id": "u-1", "approver_name": "Ms Tutor", **over,
    }
    columns_ = columns(**{k: v for k, v in cols.items()})
    names = ", ".join([*columns_, "approved_at"])
    marks = ", ".join(["%s"] * len(columns_)) + ", now()"
    return one(db, f"INSERT INTO lesson_plans ({names}) VALUES ({marks}) RETURNING plan_id",
               list(columns_.values()))


def test_the_approved_row_that_meets_every_rule_is_accepted_when_the_guard_is_off(loose):
    assert approved_row(loose).startswith("LP-")


BROKEN = {
    "the approver is not the author": dict(approver_id="u-2", approver_name="Someone Else"),
    "the check did not pass": dict(check_result={**PASSED, "passed": False}),
    "the objective changed": dict(check_result={**PASSED, "objective_unchanged": False}),
    "the text outside the edits changed": dict(check_result={**PASSED, "outside_unchanged": False}),
    "no check result keys": dict(check_result={}),
    "no accepted edit": dict(accepted_edit_ids=[]),
    "an accepted edit that is not a kept edit": dict(accepted_edit_ids=["e9"]),
    "the same accepted edit twice": dict(accepted_edit_ids=["e1", "e1"]),
}


@pytest.mark.parametrize("case", list(BROKEN))
def test_approved_is_refused_by_the_constraint_when_a_rule_is_not_met(loose, case):
    cols = columns(
        **{"status": "approved", "accepted_edit_ids": ["e1"], "final_text": REVISED,
           "approver_id": "u-1", "approver_name": "Ms Tutor", **BROKEN[case]}
    )
    names = ", ".join([*cols, "approved_at"])
    marks = ", ".join(["%s"] * len(cols)) + ", now()"
    err = refused(loose, CHECK, f"INSERT INTO lesson_plans ({names}) VALUES ({marks})",
                  list(cols.values()))
    assert err.diag.constraint_name == APPROVED_RULE, case


@pytest.mark.parametrize(
    "missing",
    [dict(approver_id=None), dict(approver_name=None), dict(final_text=None),
     dict(accepted_edit_ids=None), dict(approver_name=""), dict(final_text="")],
)
def test_approved_with_a_missing_part_of_the_decision_is_refused(loose, missing):
    cols = columns(
        **{"status": "approved", "accepted_edit_ids": ["e1"], "final_text": REVISED,
           "approver_id": "u-1", "approver_name": "Ms Tutor", **missing}
    )
    names = ", ".join([*cols, "approved_at"])
    marks = ", ".join(["%s"] * len(cols)) + ", now()"
    err = refused(loose, CHECK, f"INSERT INTO lesson_plans ({names}) VALUES ({marks})",
                  list(cols.values()))
    assert err.diag.constraint_name in {APPROVED_RULE, "decision_whole"}


def test_approved_with_no_approval_time_is_refused(loose):
    cols = columns(status="approved", accepted_edit_ids=["e1"], final_text=REVISED,
                   approver_id="u-1", approver_name="Ms Tutor")
    err = refused(loose, CHECK, insert_sql(cols), list(cols.values()))
    assert err.diag.constraint_name in {APPROVED_RULE, "decision_whole"}


def test_a_proposed_row_that_carries_a_decision_is_refused(loose):
    cols = columns(approver_id="u-1")
    err = refused(loose, CHECK, insert_sql(cols), list(cols.values()))
    assert err.diag.constraint_name == "decision_whole"


# --------------------------------------------------------------------------- what a row must hold
def test_a_new_plan_starts_as_proposed_with_nothing_decided(db):
    for over in (
        dict(status="approved"), dict(status="rejected"), dict(approver_id="u-1"),
        dict(final_text=REVISED), dict(accepted_edit_ids=["e1"]),
    ):
        cols = columns(**over)
        refused(db, (GUARD, CHECK), insert_sql(cols), list(cols.values()))


def test_a_kept_edit_that_cites_a_finding_not_in_the_stored_evidence_is_refused(db):
    err = refuse_add(db, edits={"kept": [{**KEPT, "finding_ids": ["CF-0f0f0f0f"]}], "dropped": []})
    assert err.diag.constraint_name == "edits_cite_stored_evidence"


def test_a_kept_edit_that_cites_no_finding_is_refused(db):
    err = refuse_add(db, edits={"kept": [{**KEPT, "finding_ids": []}], "dropped": []})
    assert err.diag.constraint_name == "edits_cite_stored_evidence"


def test_a_plan_with_no_kept_edit_is_refused(db):
    for edits in ({"kept": [], "dropped": []}, {"dropped": []}, {"kept": "x", "dropped": []}):
        err = refuse_add(db, edits=edits)
        assert err.diag.constraint_name in {"plan_has_kept_edit", "edits_cite_stored_evidence"}


@pytest.mark.parametrize(
    ("over", "names"),
    [
        (dict(original_sha256="c" * 64), {"original_matches_hash"}),
        (dict(objective_sha256="c" * 64), {"objective_matches_hash"}),
        (dict(revised_sha256="c" * 64), {"revised_matches_hash"}),
        (dict(pins={"template_version": "other", "template_sha256": "a" * 64}),
         {"pins_match_column"}),
        (dict(pins={"template_version": "t-lessons"}), {"pins_match_column"}),
        (dict(evidence={"finding_id": FINDING}),
         {"shapes_are_right", "edits_cite_stored_evidence"}),
        (dict(pins=[1]), {"shapes_are_right", "pins_match_column"}),
        (dict(check_result=[1]), {"shapes_are_right"}),
    ],
)
def test_a_row_whose_hash_pins_or_shape_is_wrong_is_refused_by_name(db, over, names):
    err = refuse_add(db, **over)
    assert err.diag.constraint_name in names


@pytest.mark.parametrize(
    "over",
    [dict(original_text=""), dict(objective_text=""), dict(school_code=""), dict(stage=""),
     dict(area=""), dict(term=0), dict(author_id=""), dict(author_name=""), dict(run_id=""),
     dict(model=""), dict(prompt_sha256="short"), dict(status="weird")],
)
def test_empty_or_malformed_columns_are_refused(db, over):
    refuse_add(db, (CHECK, GUARD), **over)


def test_a_plan_needs_the_model_and_the_prompt_that_wrote_its_edits(db):
    for over in (dict(model=None), dict(prompt_sha256=None)):
        refuse_add(db, errors.NotNullViolation, **over)


def test_a_plan_must_name_a_template_version_that_exists(db):
    refuse_add(db, errors.ForeignKeyViolation, template_version="nope",
               pins={"template_version": "nope", "template_sha256": "a" * 64})


# --------------------------------------------------------------------------- what never changes
@pytest.mark.parametrize(
    "change",
    [
        dict(original_text="other", original_sha256=sha("other")),
        dict(objective_text="other", objective_sha256=sha("other")),
        dict(revised_text="other", revised_sha256=sha("other")),
        dict(evidence=[{"finding_id": FINDING, "sentence": "changed"}]),
        dict(pins={"template_version": "t-lessons", "template_sha256": "d" * 64}),
        dict(edits={"kept": [{**KEPT, "replacement": "changed"}], "dropped": []}),
        dict(check_result={**PASSED, "extra": True}),
        dict(diff={"lines": [1], "changes": []}),
        dict(model="other-model"),
        dict(prompt_sha256="e" * 64),
        dict(author_id="u-2"),
        dict(author_name="Someone"),
        dict(school_code="OTHER"),
        dict(stage="Form 1"),
        dict(area="numeracy"),
        dict(term=3),
        dict(run_id="RUN-2"),
    ],
    ids=lambda c: next(iter(c)),
)
def test_what_the_tutor_wrote_and_what_the_edits_rest_on_never_change(db, change):
    plan = add(db)
    err = refuse_update(db, GUARD, plan, **change)
    assert "cannot change" in str(err)


def test_a_plan_is_never_deleted_or_truncated(db):
    plan = add(db)
    refused(db, (GUARD, errors.InsufficientPrivilege),
            "DELETE FROM lesson_plans WHERE plan_id = %s", (plan,))
    refused(db, (GUARD, errors.InsufficientPrivilege), "TRUNCATE lesson_plans")
    assert one(db, "SELECT count(*) FROM lesson_plans") == 1


def test_a_plan_that_was_approved_is_still_there_unchanged_after_a_refused_delete(db):
    plan = add(db)
    db.execute(*approve_sql(plan))
    refused(db, (GUARD, errors.InsufficientPrivilege), "DELETE FROM lesson_plans")
    assert row(db, plan)["final_text"] == REVISED


# --------------------------------------------------------------------------- who can do what
def may(db, role, relation, privilege):
    if role is None:
        return one(db, "SELECT has_table_privilege(%s, %s)", (relation, privilege))
    return one(db, "SELECT has_table_privilege(%s, %s, %s)", (role, relation, privilege))


def role_exists(db, name):
    return db.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone()


def test_the_app_role_can_write_plans_but_holds_no_delete_or_truncate_right(db):
    relation = f'"{db.schema}".lesson_plans'
    for privilege in ("DELETE", "TRUNCATE"):
        assert not may(db, None, relation, privilege), privilege
    for privilege in ("INSERT", "UPDATE", "SELECT"):
        assert may(db, None, relation, privilege), privilege
    moves = f'"{db.schema}".lesson_plan_moves'
    for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
        assert not may(db, None, moves, privilege), privilege


def test_no_other_role_can_touch_the_plans_and_the_read_only_role_reads_only_the_moves(db, roles):
    relation = f'"{db.schema}".lesson_plans'
    moves = f'"{db.schema}".lesson_plan_moves'
    for key in ("ro", "portal", "auto", "orchestrator", "n8n"):
        if not role_exists(db, roles[key]):
            continue
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            assert not may(db, roles[key], relation, privilege), (key, privilege)
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            assert not may(db, roles[key], moves, privilege), (key, privilege)


def test_in_the_live_schema_the_read_only_role_has_lost_the_default_read_of_plans(conn, roles):
    """The default privileges give the read-only role SELECT on every new table in `public`. The
    migration takes it away for lesson_plans. Skipped until 009 is applied to the live database."""
    if not one(conn, "SELECT to_regclass('public.lesson_plans') IS NOT NULL"):
        pytest.skip("migration 009 is not applied to this database yet")
    if not role_exists(conn, roles["ro"]):
        pytest.skip("no read-only role in this database")
    assert not may(conn, roles["ro"], "public.lesson_plans", "SELECT")
