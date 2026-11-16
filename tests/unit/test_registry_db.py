"""The registry constraints in a real Postgres (integration: needs DATABASE_URL).

Migration 008 runs into a throwaway schema, so nothing live is touched. Two layers are tested, and
the second is tested alone:

  * the guard (a trigger) and the constraints together, by walking a version along its path with
    plain UPDATE statements, the way a bug in the code might;
  * the `approved` constraint on its own. For that, the guard is switched off inside the test, so
    a row can be written straight at `approved` and the constraint is the only thing that can refuse
    it. (The role that owns the table can do this too, which is the honest limit the migration
    states.)
"""

import hashlib
import itertools
from datetime import UTC, datetime

import psycopg
import pytest
from dbtools import make_document, one, refused, run_migrate
from psycopg import errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

pytestmark = pytest.mark.integration

CHECK = errors.CheckViolation
GUARD = errors.RestrictViolation
APPROVED_RULE = "approved_needs_tests_review_and_teacher"
JSON_COLUMNS = {
    "tests", "test_result", "review_reasons", "static_check", "output_schema", "rules_quoted",
    "template_patch",
}
_n = itertools.count(1)


@pytest.fixture
def db(scratch_schema):
    schema, url = scratch_schema
    built = run_migrate(url)
    assert built.returncode == 0, built.stderr
    with psycopg.connect(url) as conn:
        conn.schema = schema
        yield conn
        conn.rollback()


@pytest.fixture
def loose(db):
    """The guard is off: only the table's own constraints stand between a row and `approved`."""
    db.execute("ALTER TABLE measure_versions DISABLE TRIGGER measure_versions_guard")
    return db


def now():
    return datetime.now(UTC)


def wrap(cols):
    # None stays an SQL null. A JSON null would be a value, and the constraints must see "nothing".
    return {
        k: Jsonb(v) if k in JSON_COLUMNS and v is not None and not isinstance(v, Jsonb) else v
        for k, v in cols.items()
    }


# --------------------------------------------------------------------------- building blocks
SOURCE = (
    'OUTPUT_KIND = "number"\n\n\ndef measure(records, params):\n'
    '    return {{"value": "v{}"}}\n'
)


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def insert_sql_text(cols):
    names = ", ".join(cols)
    return f"INSERT INTO measure_versions ({names}) VALUES ({', '.join(['%s'] * len(cols))})"


def methodology(db, n):
    return make_document(db, kind="methodology", status="done", sha256=f"{n:064x}")


def may(db, role, relation, privilege):
    """has_table_privilege for a named role, or for the connected role when `role` is None."""
    if role is None:
        return one(db, "SELECT has_table_privilege(%s, %s)", (relation, privilege))
    return one(db, "SELECT has_table_privilege(%s, %s, %s)", (role, relation, privilege))


def add_measure(db):
    n = next(_n)
    return one(
        db,
        "INSERT INTO measures (name, method_ref) VALUES (%s, %s) RETURNING measure_id",
        (f"measure {n}", f"5.{n}"),
    )


def tests_of(*sources):
    out = []
    for i, source in enumerate(sources):
        test = {"name": f"case {i}", "source": source, "records": [], "expected": {"value": "0"}}
        if source == "document_example":
            test["quote"] = "a worked example in the methodology"
        out.append(test)
    return out


def result_of(tests, failed=()):
    return {"passed": not failed, "ran": len(tests), "failed": list(failed)}


def add_version(db, measure=None, version=1, **over):
    n = next(_n)
    source = over.pop("source_code", SOURCE.format(n))
    cols = {
        "measure_id": measure or add_measure(db),
        "version": version,
        "description": "what it shows",
        "input_scope": "learner",
        "source_code": source,
        "code_sha256": sha(source),
        "methodology_document_id": make_document(
            db, kind="methodology", status="done", sha256=f"{n:064x}"
        ),
        "section_sha256": "a" * 64,
        "clause_ids": ["SC-3"],
        "template_version": "t-test",
        "output_kind": "number",
        "output_schema": {"kind": "number"},
        "static_check": {"passed": True, "findings": []},
        "tests": [],
        **over,
    }
    cols = wrap(cols)
    db.execute(
        insert_sql_text(cols),
        list(cols.values()),
    )
    return cols["measure_id"], cols["version"]


def person(db, user_id, role, name="A Person"):
    db.execute(
        "INSERT INTO registry_reviewers (user_id, role, name, granted_by) "
        "VALUES (%s, %s, %s, 'admin')",
        (user_id, role, name),
    )


def set_cols(db, v, **cols):
    cols = wrap(cols)
    sets = ", ".join(f"{k} = %s" for k in cols)
    db.execute(
        f"UPDATE measure_versions SET {sets} WHERE measure_id = %s AND version = %s",
        [*cols.values(), *v],
    )


def refuse_set(db, error, v, **cols):
    cols = wrap(cols)
    sets = ", ".join(f"{k} = %s" for k in cols)
    return refused(
        db, error,
        f"UPDATE measure_versions SET {sets} WHERE measure_id = %s AND version = %s",
        [*cols.values(), *v],
    )


def row(db, v):
    cur = db.cursor(row_factory=dict_row)
    return cur.execute(
        "SELECT * FROM measure_versions WHERE measure_id = %s AND version = %s", v
    ).fetchone()


def status(db, v):
    return row(db, v)["status"]


def at_testing(db, **over):
    v = add_version(db, **over)
    set_cols(db, v, status="testing")
    return v


def at_review(db, tests=None, **over):
    tests = tests if tests is not None else tests_of("build", "document_example")
    v = at_testing(db, **over)
    set_cols(db, v, tests=tests, test_result=result_of(tests))
    set_cols(db, v, status="awaiting_review")
    return v


def at_demo(db, review="pass", tests=None, **over):
    v = at_review(db, tests=tests, **over)
    set_cols(db, v, review_result=review, review_reasons=[] if review == "pass" else ["reason"],
             reviewed_at=now())
    if review == "hold":
        return v  # a held version stays at awaiting_review
    set_cols(db, v, status="awaiting_demo")
    return v


def approve(db, v, teacher):
    set_cols(db, v, status="approved", teacher_id=teacher, teacher_name="typed by a bug",
             teacher_decision="confirmed", teacher_decided_at=now(), approved_at=now())


# --------------------------------------------------------------------------- the whole path
def test_a_version_with_everything_in_place_is_approved_and_the_name_is_the_recorded_one(db):
    person(db, "u-teacher", "teacher", "Ms Teacher")
    v = at_demo(db)
    approve(db, v, "u-teacher")
    got = row(db, v)
    assert got["status"] == "approved"
    assert (got["teacher_id"], got["teacher_name"]) == ("u-teacher", "Ms Teacher")
    assert got["teacher_decision"] == "confirmed" and got["approved_at"] is not None


def test_the_measure_id_follows_the_id_scheme(db):
    made = add_measure(db)
    got = one(db, "SELECT measure_id FROM measures WHERE measure_id = %s", (made,))
    assert got.startswith("MS-")


# --------------------------------------------------------------------------- approved, tested alone
def approved_row(db, **over):
    tests = over.pop("tests", tests_of("build", "document_example"))
    cols = {
        "status": "approved",
        "tests": tests,
        "test_result": result_of(tests),
        "review_result": "pass",
        "review_reasons": [],
        "reviewed_at": now(),
        "teacher_id": "u-teacher",
        "teacher_name": "Ms Teacher",
        "teacher_decision": "confirmed",
        "teacher_decided_at": now(),
        "approved_at": now(),
        **over,
    }
    return add_version(db, **cols)


def test_the_approved_row_that_meets_every_rule_is_accepted(loose):
    approved_row(loose)


@pytest.mark.parametrize("sources", [("document_example",), ("teacher",), ("build", "teacher"),
                                     ("break_it", "document_example")])
def test_one_test_that_is_not_the_models_is_enough(loose, sources):
    approved_row(loose, tests=tests_of(*sources))


def cases():
    both = tests_of("build", "document_example")
    return {
        "one test failed": dict(test_result=result_of(both, failed=["case 0"])),
        "the result says passed but a test has no result": dict(
            test_result={"passed": True, "ran": 1, "failed": []}
        ),
        "the result says passed but lists a failure": dict(
            test_result={"passed": True, "ran": 2, "failed": ["case 1"]}
        ),
        "no result recorded": dict(test_result=None),
        "no tests at all": dict(tests=[], test_result={"passed": True, "ran": 0, "failed": []}),
        "only tests the model wrote": dict(
            tests=tests_of("build", "break_it"),
            test_result=result_of(tests_of("build", "break_it")),
        ),
        "the static check did not pass": dict(static_check={"passed": False, "findings": []}),
        "no static check": dict(static_check=None),
        "the automated review held and nobody cleared it": dict(
            review_result="hold", review_reasons=["the quote does not match the code"]
        ),
        "no automated review": dict(review_result=None, review_reasons=None, reviewed_at=None),
        "no teacher": dict(teacher_id=None, teacher_name=None, teacher_decision=None,
                           teacher_decided_at=None),
        "the teacher did not confirm": dict(teacher_decision="rejected"),
        "no approval time": dict(approved_at=None),
    }


@pytest.mark.parametrize("case", list(cases()))
def test_approved_is_refused_when_a_rule_is_not_met(loose, case):
    err = refused(loose, CHECK, *insert_sql(loose, case))
    assert err.diag.constraint_name is not None


def insert_sql(db, case):
    """The INSERT for one broken approved row, as (sql, params), so `refused` can run it."""
    over = dict(cases()[case])
    tests = over.pop("tests", tests_of("build", "document_example"))
    base = {
        "status": "approved", "tests": tests, "test_result": result_of(tests),
        "review_result": "pass", "review_reasons": [], "reviewed_at": now(),
        "teacher_id": "u-teacher", "teacher_name": "Ms Teacher", "teacher_decision": "confirmed",
        "teacher_decided_at": now(), "approved_at": now(), **over,
    }
    n = next(_n)
    source = SOURCE.format(n)
    cols = wrap({
        "measure_id": add_measure(db), "version": 1, "description": "d", "input_scope": "learner",
        "source_code": source, "code_sha256": sha(source),
        "methodology_document_id": methodology(db, n),
        "section_sha256": "a" * 64, "clause_ids": ["SC-3"], "template_version": "t",
        "output_kind": "number", "output_schema": {"kind": "number"},
        "static_check": {"passed": True, "findings": []}, **base,
    })
    return (
        insert_sql_text(cols),
        list(cols.values()),
    )


def test_the_approved_rule_itself_is_what_refuses_a_row_with_only_model_tests_or_a_hold(loose):
    # Exactly the approved constraint, not a neighbour, for the two rules the owner named.
    for case in ("only tests the model wrote", "the automated review held and nobody cleared it",
                 "the teacher did not confirm", "no teacher"):
        err = refused(loose, CHECK, *insert_sql(loose, case))
        assert err.diag.constraint_name in {APPROVED_RULE, "demo_needs_review_cleared"}, case


def test_a_hold_that_a_person_cleared_lets_the_version_be_approved(loose):
    approved_row(
        loose,
        review_result="hold", review_reasons=["a reason"],
        hold_cleared_by_id="u-dev", hold_cleared_name="Dev One", hold_cleared_at=now(),
        hold_cleared_note="read the code, it matches the quote",
    )


def test_a_cleared_hold_without_a_note_or_a_time_is_refused(loose):
    for broken in (dict(hold_cleared_note=None), dict(hold_cleared_at=None)):
        over = dict(review_result="hold", review_reasons=["r"], hold_cleared_by_id="u-dev",
                    hold_cleared_name="Dev One", hold_cleared_at=now(), hold_cleared_note="ok")
        over.update(broken)
        with pytest.raises(CHECK):
            with loose.transaction():
                approved_row(loose, **over)


def test_a_cleared_hold_on_a_review_that_passed_is_refused(loose):
    with pytest.raises(CHECK):
        with loose.transaction():
            approved_row(loose, hold_cleared_by_id="u-dev", hold_cleared_name="Dev One",
                         hold_cleared_at=now(), hold_cleared_note="no hold to clear")


def test_only_one_version_of_a_measure_can_be_approved_at_a_time(loose):
    measure = add_measure(loose)
    approved_row(loose, measure=measure, version=1)
    err = refused(loose, errors.UniqueViolation, *insert_sql_for(loose, measure))
    assert err.diag.constraint_name == "one_approved_per_measure"


def insert_sql_for(db, measure):
    n = next(_n)
    source = SOURCE.format(n)
    tests = tests_of("document_example")
    cols = wrap({
        "measure_id": measure, "version": 2, "status": "approved", "description": "d",
        "input_scope": "learner", "source_code": source, "code_sha256": sha(source),
        "methodology_document_id": methodology(db, n),
        "section_sha256": "a" * 64, "clause_ids": ["SC-3"], "template_version": "t",
        "output_kind": "number", "output_schema": {"kind": "number"},
        "static_check": {"passed": True}, "tests": tests, "test_result": result_of(tests),
        "review_result": "pass", "review_reasons": [], "reviewed_at": now(),
        "teacher_id": "u-teacher", "teacher_name": "T", "teacher_decision": "confirmed",
        "teacher_decided_at": now(), "approved_at": now(),
    })
    return (
        insert_sql_text(cols),
        list(cols.values()),
    )


# ----------------------------------------------------------------- the same rules, guard on
def test_through_the_path_a_version_cannot_leave_testing_with_a_failed_test(db):
    v = at_testing(db)
    tests = tests_of("build", "document_example")
    set_cols(db, v, tests=tests, test_result=result_of(tests, failed=["case 0"]))
    err = refuse_set(db, CHECK, v, status="awaiting_review")
    assert err.diag.constraint_name == "review_stage_needs_passed_tests"


def test_through_the_path_a_version_cannot_leave_testing_when_a_test_has_no_result(db):
    v = at_testing(db)
    tests = tests_of("build", "document_example")
    set_cols(db, v, tests=tests, test_result={"passed": True, "ran": 1, "failed": []})
    refuse_set(db, CHECK, v, status="awaiting_review")


def test_through_the_path_a_version_with_no_tests_cannot_reach_review(db):
    v = at_testing(db)
    refuse_set(db, CHECK, v, status="awaiting_review")


def test_a_version_whose_static_check_failed_cannot_even_be_tested(db):
    v = add_version(db, static_check={"passed": False, "findings": [{"code": "hardcoded_value"}]})
    err = refuse_set(db, CHECK, v, status="testing")
    assert err.diag.constraint_name == "testing_needs_static_check"
    assert status(db, v) == "proposed"


def test_a_held_version_cannot_go_to_the_teacher_until_a_registered_developer_clears_it(db):
    v = at_demo(db, review="hold")
    assert status(db, v) == "awaiting_review"
    err = refuse_set(db, CHECK, v, status="awaiting_demo")
    assert err.diag.constraint_name == "demo_needs_review_cleared"


def test_the_automated_review_cannot_change_its_own_result_so_it_cannot_clear_a_hold(db):
    v = at_demo(db, review="hold")
    refuse_set(db, GUARD, v, review_result="pass", review_reasons=[])
    refuse_set(db, GUARD, v, review_reasons=["changed"])
    assert row(db, v)["review_result"] == "hold"


def test_a_pass_cannot_be_turned_into_a_hold_either(db):
    v = at_demo(db)
    refuse_set(db, GUARD, v, review_result="hold", review_reasons=["late"])


def test_a_review_that_holds_must_give_its_reasons(db):
    v = at_review(db)
    refuse_set(db, CHECK, v, review_result="hold", review_reasons=[], reviewed_at=now())
    refuse_set(db, CHECK, v, review_result="pass", review_reasons=None, reviewed_at=now())
    refuse_set(db, CHECK, v, review_result="maybe", review_reasons=[], reviewed_at=now())


def clear(db, v, by, note="read it"):
    set_cols(db, v, hold_cleared_by_id=by, hold_cleared_at=now(), hold_cleared_note=note)


def test_a_hold_is_cleared_only_by_a_developer_who_is_in_the_registry(db):
    v = at_demo(db, review="hold")
    person(db, "u-teacher", "teacher", "Ms Teacher")
    for stranger in ("nobody", "u-teacher"):
        refuse_set(db, GUARD, v, hold_cleared_by_id=stranger, hold_cleared_at=now(),
                   hold_cleared_note="x")
    person(db, "u-dev", "developer", "Dev One")
    clear(db, v, "u-dev")
    got = row(db, v)
    assert (got["hold_cleared_by_id"], got["hold_cleared_name"]) == ("u-dev", "Dev One")
    set_cols(db, v, status="awaiting_demo")
    assert status(db, v) == "awaiting_demo"


def test_a_developer_whose_access_was_withdrawn_cannot_clear_a_hold(db):
    v = at_demo(db, review="hold")
    person(db, "u-dev", "developer", "Dev One")
    db.execute("UPDATE registry_reviewers SET revoked_at = now() WHERE user_id = 'u-dev'")
    refuse_set(
        db, GUARD, v, hold_cleared_by_id="u-dev", hold_cleared_at=now(), hold_cleared_note="x"
    )


def test_a_cleared_hold_cannot_be_cleared_again_by_someone_else(db):
    v = at_demo(db, review="hold")
    person(db, "u-dev", "developer", "Dev One")
    person(db, "u-dev2", "developer", "Dev Two")
    clear(db, v, "u-dev")
    refuse_set(db, GUARD, v, hold_cleared_by_id="u-dev2")


def test_the_teacher_must_be_in_the_registry_as_a_teacher(db):
    v = at_demo(db)
    person(db, "u-dev", "developer", "Dev One")
    for stranger in ("nobody", "u-dev"):
        err = refuse_set(db, GUARD, v, status="approved", teacher_id=stranger, teacher_name="x",
                         teacher_decision="confirmed", teacher_decided_at=now(), approved_at=now())
        assert "teacher" in str(err)
    assert status(db, v) == "awaiting_demo"


def test_a_teacher_who_did_not_confirm_cannot_approve(db):
    person(db, "u-teacher", "teacher", "Ms Teacher")
    v = at_demo(db)
    refuse_set(db, CHECK, v, status="approved", teacher_id="u-teacher", teacher_name="x",
               teacher_decision="rejected", teacher_decided_at=now(), approved_at=now())
    refuse_set(db, CHECK, v, status="approved", approved_at=now())  # no teacher at all


def test_only_model_tests_cannot_be_approved_even_with_everything_else_in_place(db):
    person(db, "u-teacher", "teacher", "Ms Teacher")
    v = at_demo(db, tests=tests_of("build", "break_it"))
    err = refuse_set(db, CHECK, v, status="approved", teacher_id="u-teacher", teacher_name="x",
                     teacher_decision="confirmed", teacher_decided_at=now(), approved_at=now())
    assert err.diag.constraint_name == APPROVED_RULE
    assert status(db, v) == "awaiting_demo"


# --------------------------------------------------------------------------- the code never changes
@pytest.mark.parametrize("state", ["proposed", "testing", "awaiting_review", "awaiting_demo"])
def test_reviewed_code_and_what_it_was_built_from_cannot_change(db, state):
    v = {"proposed": lambda: add_version(db), "testing": lambda: at_testing(db),
         "awaiting_review": lambda: at_review(db), "awaiting_demo": lambda: at_demo(db)}[state]()
    changed = SOURCE.format(next(_n))
    refuse_set(db, GUARD, v, source_code=changed, code_sha256=sha(changed))
    refuse_set(db, GUARD, v, section_sha256="b" * 64)
    refuse_set(db, GUARD, v, clause_ids=["SC-9"])
    refuse_set(db, GUARD, v, output_kind="table", output_schema={"kind": "table"})
    refuse_set(db, GUARD, v, description="something else")
    refuse_set(db, GUARD, v, static_check={"passed": True, "findings": [], "edited": True})
    refuse_set(db, GUARD, v, template_version="other")
    assert row(db, v)["source_code"].startswith("OUTPUT_KIND")
    set_cols(db, v, demo_session_id="demo-1")  # what may change still can


def test_the_hash_must_be_the_hash_of_the_code(db):
    source = SOURCE.format(next(_n))
    err = refused_insert(db, source_code=source, code_sha256=sha("something else"))
    assert err.diag.constraint_name == "code_matches_hash"


def refused_insert(db, error=CHECK, **over):
    with pytest.raises(error) as info:
        with db.transaction():
            add_version(db, **over)
    return info.value


# --------------------------------------------------------------------------- status moves
ILLEGAL_FROM = {
    "proposed": ["awaiting_review", "awaiting_demo", "approved"],
    "testing": ["proposed", "awaiting_demo", "approved"],
    "awaiting_review": ["proposed", "testing", "approved"],
    "awaiting_demo": ["proposed", "testing", "awaiting_review"],
}


@pytest.mark.parametrize("state", list(ILLEGAL_FROM))
def test_a_status_cannot_skip_or_go_back(db, state):
    v = {"proposed": lambda: add_version(db), "testing": lambda: at_testing(db),
         "awaiting_review": lambda: at_review(db), "awaiting_demo": lambda: at_demo(db)}[state]()
    for target in ILLEGAL_FROM[state]:
        err = refuse_set(db, GUARD, v, status=target)
        assert "not a legal move" in str(err), (state, target)
    assert status(db, v) == state


def test_a_status_that_is_not_one_of_the_seven_is_refused(db):
    v = add_version(db)
    refuse_set(db, (GUARD, CHECK), v, status="awaiting_dev")


@pytest.mark.parametrize("end", ["rejected", "retired"])
def test_a_rejected_or_retired_version_is_closed(db, end):
    v = add_version(db)
    extra = {"retired_reason": "withdrawn"} if end == "retired" else {}
    set_cols(db, v, status=end, **extra)
    refuse_set(db, GUARD, v, status="proposed")
    refuse_set(db, GUARD, v, status="testing")
    refuse_set(db, GUARD, v, demo_session_id="late")
    assert status(db, v) == end


def test_every_state_before_approved_can_be_rejected_or_retired(db):
    for build in (lambda: add_version(db), lambda: at_testing(db), lambda: at_review(db),
                  lambda: at_demo(db)):
        a, b = build(), build()
        set_cols(db, a, status="rejected")
        set_cols(db, b, status="retired", retired_reason="superseded")
        assert (status(db, a), status(db, b)) == ("rejected", "retired")


def test_an_approved_version_can_only_be_retired_and_only_with_a_reason(db):
    person(db, "u-teacher", "teacher", "Ms Teacher")
    v = at_demo(db)
    approve(db, v, "u-teacher")
    refuse_set(db, GUARD, v, status="rejected")
    refuse_set(db, GUARD, v, status="awaiting_demo")
    refuse_set(db, GUARD, v, demo_session_id="x")  # approved is closed to every other change
    refuse_set(db, CHECK, v, status="retired")  # a reason is needed
    set_cols(db, v, status="retired", retired_reason="section_removed")
    assert status(db, v) == "retired"


def test_approving_a_new_version_works_once_the_old_one_is_retired(db):
    person(db, "u-teacher", "teacher", "Ms Teacher")
    measure = add_measure(db)
    first = at_demo(db, measure=measure, version=1)
    approve(db, first, "u-teacher")
    second = at_demo(db, measure=measure, version=2)
    with pytest.raises(errors.UniqueViolation):
        with db.transaction():
            approve(db, second, "u-teacher")
    set_cols(db, first, status="retired", retired_reason="superseded")
    approve(db, second, "u-teacher")
    assert (status(db, first), status(db, second)) == ("retired", "approved")


# --------------------------------------------------------------------------- tests only grow
def test_tests_can_be_added_while_testing_but_a_recorded_test_never_changes(db):
    v = at_testing(db)
    first = tests_of("build")
    set_cols(db, v, tests=first)
    refuse_set(db, GUARD, v, tests=[{**first[0], "expected": {"value": "9"}}])
    refuse_set(db, GUARD, v, tests=[])
    second = [{**t, "name": "second"} for t in tests_of("document_example")]
    set_cols(db, v, tests=[*first, *second])
    assert len(row(db, v)["tests"]) == 2


def test_tests_cannot_be_added_once_the_version_is_under_review(db):
    v = at_review(db)
    added = [*row(db, v)["tests"], {"name": "late", "source": "build"}]
    err = refuse_set(db, GUARD, v, tests=added)
    assert "only be added while testing or at the demo" in str(err)


def test_a_test_added_at_the_demo_needs_a_result_that_covers_it(db):
    v = at_demo(db)
    tests = [*row(db, v)["tests"], {"name": "teacher case", "source": "teacher"}]
    stale = refuse_set(db, CHECK, v, tests=tests)  # the old result covers two tests, not three
    assert stale.diag.constraint_name == "review_stage_needs_passed_tests"
    set_cols(db, v, tests=tests, test_result=result_of(tests))
    assert len(row(db, v)["tests"]) == 3
    failing = refuse_set(db, CHECK, v, test_result=result_of(tests, failed=["teacher case"]))
    assert failing.diag.constraint_name == "review_stage_needs_passed_tests"


def test_a_test_result_cannot_be_recorded_before_testing_or_while_under_review(db):
    v = add_version(db)
    refuse_set(db, GUARD, v, test_result=result_of([]))
    w = at_review(db)
    refuse_set(db, GUARD, w, test_result={**result_of(row(db, w)["tests"]), "again": True})


# --------------------------------------------------------------------------- shapes
@pytest.mark.parametrize(
    "over",
    [
        dict(output_kind="chart", output_schema={"kind": "chart"}),
        dict(output_schema={"kind": "table"}),  # differs from output_kind number
        dict(output_schema=[1]),
        dict(clause_ids=[]),
        dict(description=""),
        dict(input_scope="class"),
        dict(section_sha256="short"),
        dict(source_code=""),
        dict(template_version=""),
        dict(tests=[{"name": "a", "source": "model"}]),
        dict(tests=[{"name": "a", "source": "document_example"}]),  # no quote
        dict(tests=[{"name": "a", "source": "build"}, {"name": "a", "source": "break_it"}]),
        dict(tests=[{"source": "build"}]),
        dict(tests=[{"name": "", "source": "build"}]),
        dict(tests={"name": "a"}),
        dict(version=0),
    ],
)
def test_a_version_with_a_bad_shape_is_refused(db, over):
    refused_insert(db, **over)


def test_a_version_starts_as_proposed_with_nothing_decided(db):
    for over in (
        dict(status="approved"), dict(status="testing"),
        dict(test_result={"passed": True, "ran": 0, "failed": []}),
        dict(review_result="pass", review_reasons=[], reviewed_at=now()),
        dict(teacher_id="u-teacher"), dict(approved_at=now()), dict(retired_reason="withdrawn"),
        dict(hold_cleared_by_id="u-dev"), dict(dev_reviewer_id="u-dev"),
    ):
        refused_insert(db, GUARD, **over)


def test_the_optional_developer_review_columns_are_not_required_for_approval(db):
    person(db, "u-teacher", "teacher", "Ms Teacher")
    v = at_demo(db)
    approve(db, v, "u-teacher")
    assert row(db, v)["dev_reviewer_id"] is None


def test_a_developer_review_when_given_must_be_a_registered_developer_and_whole(db):
    v = at_demo(db)
    refuse_set(db, GUARD, v, dev_reviewer_id="nobody", dev_reviewer_name="x", dev_reviewed_at=now())
    person(db, "u-dev", "developer", "Dev One")
    refuse_set(db, CHECK, v, dev_reviewer_id="u-dev")  # no time recorded
    set_cols(db, v, dev_reviewer_id="u-dev", dev_reviewer_name="x", dev_reviewed_at=now())
    assert row(db, v)["dev_reviewer_name"] == "Dev One"


def test_a_reviewer_role_is_developer_or_teacher_and_nothing_else(db):
    with pytest.raises(CHECK):
        with db.transaction():
            person(db, "u-1", "admin")
    person(db, "u-1", "developer")
    person(db, "u-1", "teacher")  # the same person may hold both roles; it is two rows


# --------------------------------------------------------------------------- nothing is deleted
def test_a_version_is_never_deleted_or_truncated(db):
    v = add_version(db)
    # Two locks: the role has no DELETE right, and the guard refuses it even for a role that has.
    refused(
        db, (GUARD, errors.InsufficientPrivilege),
        "DELETE FROM measure_versions WHERE measure_id = %s", (v[0],),
    )
    refused(db, (GUARD, errors.InsufficientPrivilege), "TRUNCATE measure_versions")
    assert one(db, "SELECT count(*) FROM measure_versions") == 1


def test_the_app_role_holds_no_delete_or_truncate_right_on_the_registry(db):
    for table in ("measures", "measure_versions", "registry_reviewers"):
        for privilege in ("DELETE", "TRUNCATE"):
            assert not may(db, None, f'"{db.schema}".{table}', privilege), (table, privilege)
        assert may(db, None, f'"{db.schema}".{table}', "INSERT")


# --------------------------------------------------------------------------- who can read what
SAFE = ["measure_id", "version", "status", "description", "input_scope", "output_kind",
        "output_schema", "clause_ids", "template_version", "methodology_document_id",
        "section_sha256", "code_sha256", "approved_at"]


def role_exists(db, name):
    return db.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone()


def column_right(db, role, relation, column):
    return one(db, "SELECT has_column_privilege(%s, %s, %s, 'SELECT')", (role, relation, column))


def test_the_read_only_role_cannot_read_source_code_and_can_read_what_a_measure_is(db, roles):
    if not role_exists(db, roles["ro"]):
        pytest.skip("the database roles are made by the init script; this database has none")
    relation = f'"{db.schema}".measure_versions'
    every = [r[0] for r in db.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = 'measure_versions'", (db.schema,))]
    for column in every:
        assert column_right(db, roles["ro"], relation, column) == (column in SAFE), column
    assert not column_right(db, roles["ro"], relation, "source_code")
    for column in ("teacher_name", "teacher_note", "hold_cleared_name", "review_reasons", "tests"):
        assert not column_right(db, roles["ro"], relation, column), column


def test_the_read_only_role_cannot_write_and_cannot_read_the_reviewers(db, roles):
    if not role_exists(db, roles["ro"]):
        pytest.skip("the database roles are made by the init script; this database has none")
    for table in ("measures", "measure_versions", "registry_reviewers", "measure_status_moves"):
        relation = f'"{db.schema}".{table}'
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            assert not may(db, roles["ro"], relation, privilege), (table, privilege)
    reviewers = f'"{db.schema}".registry_reviewers'
    assert not one(db, "SELECT has_table_privilege(%s, %s, 'SELECT')", (roles["ro"], reviewers))
    for column in ("user_id", "name", "role"):
        assert not column_right(db, roles["ro"], reviewers, column)


def test_no_other_role_can_touch_the_registry(db, roles):
    for key in ("portal", "auto", "orchestrator", "n8n"):
        if not role_exists(db, roles[key]):
            continue
        for table in ("measures", "measure_versions", "registry_reviewers", "measure_status_moves"):
            relation = f'"{db.schema}".{table}'
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert not one(
                    db, "SELECT has_table_privilege(%s, %s, %s)", (roles[key], relation, privilege)
                ), (key, table, privilege)


def test_in_the_live_schema_the_read_only_role_has_lost_the_default_read(conn, roles):
    """The default privileges give the read-only role SELECT on every new table in `public`. The
    migration takes it away for the two tables that hold code and names. Skipped until 008 is
    applied to the live database."""
    if not one(conn, "SELECT to_regclass('public.measure_versions') IS NOT NULL"):
        pytest.skip("migration 008 is not applied to this database yet")
    if not role_exists(conn, roles["ro"]):
        pytest.skip("no read-only role in this database")
    assert not column_right(conn, roles["ro"], "public.measure_versions", "source_code")
    assert not may(conn, roles["ro"], "public.measure_versions", "SELECT")
    assert not may(conn, roles["ro"], "public.registry_reviewers", "SELECT")
    for column in SAFE:
        assert column_right(conn, roles["ro"], "public.measure_versions", column), column
