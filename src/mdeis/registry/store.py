"""The registry functions: create a version, move it along its path, record what was checked and
who decided. Each takes the context (the connection, the run, and the signed-in person) and nothing
that names a person.

Who may do what, in code and in the database:
  * the system (no one signed in) creates a version, moves it to testing, review and the demo,
    records test results, records the automated review, rejects and retires;
  * a signed-in developer who is in `registry_reviewers` clears a hold;
  * a signed-in teacher who is in `registry_reviewers` decides at the demo, and only that decision
    can make a version `approved`. `advance_status` refuses to.
The signed-in person is read from `ctx.approver`, which the server sets from the verified token. No
function has an argument that could name one.

Each function writes one action-log row (`logged_tool`), so each status move is on record with the
person who made it, or with none for a system step. A refusal leaves the version as it was: the
database work runs in a savepoint, and the database's own constraints are the last lock.
"""

from __future__ import annotations

import hashlib
import re
from contextlib import contextmanager

from psycopg import errors
from psycopg.rows import dict_row

from .. import approver_setting as setting
from ..db.diary import jsonb
from ..sandbox import static_check
from ..sandbox.config import load_rules
from ..tools._call import logged_tool
from ..tools.context import Refusal, ToolContext
from . import approvers
from . import transitions as t
from .values import template_values

SHA256 = re.compile(r"^[0-9a-f]{64}$")

# What the database refuses with, turned into a plain refusal instead of an error.
DATABASE_REFUSALS = (
    errors.CheckViolation,
    errors.RestrictViolation,
    errors.UniqueViolation,
    errors.ForeignKeyViolation,
    errors.NotNullViolation,
    errors.InsufficientPrivilege,
)

# The constraint names that a person is likely to meet, in plain words.
PLAIN = {
    "testing_needs_static_check": "the static check did not pass",
    "review_stage_needs_passed_tests": "the tests have not all passed",
    "demo_needs_review_cleared": (
        "the automated review is holding it and no developer has cleared the hold"
    ),
    "approved_needs_tests_review_and_teacher": (
        "that needs passed tests, at least one test that did not come from the model, a passed "
        "static check, a review that is not holding, and the confirmation of a registered teacher"
    ),
    "one_approved_per_measure": "another version of this measure is already in force",
}


# --- small helpers -----------------------------------------------------------------------------
def _cursor(ctx: ToolContext):
    return ctx.conn.cursor(row_factory=dict_row)


def _text(value, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Refusal(f"{what} is needed")
    return value


def _why(error) -> str:
    named = error.diag.constraint_name
    return PLAIN.get(named) or (error.diag.message_primary or str(error)).splitlines()[0]


@contextmanager
def _database(ctx: ToolContext):
    """Run the database work in a savepoint. A rule of the database becomes a refusal, and the
    work inside is undone. Call it after a first statement, so that it is a savepoint and not a
    commit."""
    try:
        with ctx.conn.transaction():
            yield
    except DATABASE_REFUSALS as error:
        raise Refusal(f"the registry refused it: {_why(error)}") from None


def _load(ctx: ToolContext, measure_id, version) -> dict:
    if not isinstance(measure_id, str) or not isinstance(version, int) or isinstance(version, bool):
        raise Refusal("a measure ID and a version number are needed")
    row = (
        _cursor(ctx)
        .execute(
            "SELECT status, review_result, hold_cleared_by_id, tests, test_result "
            "FROM measure_versions WHERE measure_id = %s AND version = %s FOR UPDATE",
            (measure_id, version),
        )
        .fetchone()
    )
    if row is None:
        raise Refusal(f"there is no version {version} of {measure_id}")
    return row


def _signed_in(ctx: ToolContext):
    if ctx.approver is None:
        raise Refusal("no one is signed in, so this cannot be recorded")
    return ctx.approver


def _registered(ctx: ToolContext, role: str):
    """The signed-in person, the name for them, and whether the row for them is still to be made.

    A person with a live row in the registry is as before: their registry name, nothing to make.
    With `REVIEWERS_ARE_APPROVERS` on, a signed-in person with a school in the token and no record
    at all is accepted too, under the name the token gave; the third value is then True and the
    caller makes the row inside its own savepoint (`approvers.add_row`). A record an administrator
    ended is refused, with the setting on or off."""
    who = _signed_in(ctx)
    row = (
        _cursor(ctx)
        .execute(
            "SELECT name FROM registry_reviewers "
            "WHERE user_id = %s AND role = %s AND revoked_at IS NULL",
            (who.id, role),
        )
        .fetchone()
    )
    if row is not None:
        return who, row["name"], False
    if not setting.enabled():
        raise Refusal(f"the signed-in person is not recorded as a {role} in the registry")
    if setting.ended(ctx.conn, who.id, role):
        raise Refusal(setting.ended_text(role))
    if setting.school_of(ctx) is None:
        raise Refusal("no school is attached to this session")
    name = setting.name_of(who)
    if name is None:
        raise Refusal("the signed-in approver has no name in the session, so none can be recorded")
    return who, name, True


def _moved(measure_id, version, before, after, **more) -> dict:
    return {
        "status": "ok",
        "measure_id": measure_id,
        "version": version,
        "from_status": before,
        "to_status": after,
        **more,
    }


def _clean_tests(tests, allowed: tuple[str, ...]) -> list[dict]:
    """Tests to add. Each has a unique name and a known source; a worked-example test has its
    quote."""
    if tests is None:
        return []
    if not isinstance(tests, list):
        raise Refusal("tests must be a list")
    names: set[str] = set()
    clean = []
    for test in tests:
        if not isinstance(test, dict):
            raise Refusal("each test must be an object")
        name = _text(test.get("name"), "each test needs a name")
        source = test.get("source")
        if source not in t.TEST_SOURCES:
            known = ", ".join(t.TEST_SOURCES)
            raise Refusal(f"test {name!r} has a source that is not one of {known}")
        if source not in allowed:
            raise Refusal(f"a test from {source} cannot be added here")
        if source == "document_example":
            _text(test.get("quote"), f"test {name!r} is from a worked example, so its quote")
        if name in names:
            raise Refusal(f"two tests are called {name!r}")
        names.add(name)
        clean.append(test)
    return clean


def _summarise(tests: list[dict], results) -> dict:
    """The test result, worked out here from the recorded results. A test with no result counts as
    not passed. Nobody passes a verdict in: only a name and passed true or false for each test."""
    if not isinstance(results, list):
        raise Refusal("results must be a list of {name, passed}")
    names = [test["name"] for test in tests]
    seen: dict[str, bool] = {}
    for result in results:
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("name"), str)
            or not isinstance(result.get("passed"), bool)
        ):
            raise Refusal("each result needs a test name and passed true or false")
        if result["name"] not in names:
            raise Refusal(f"there is no test called {result['name']!r}")
        if result["name"] in seen:
            raise Refusal(f"there are two results for {result['name']!r}")
        seen[result["name"]] = result["passed"]
    failed = [n for n in names if seen.get(n) is not True]
    return {
        "passed": not failed,
        "ran": len(seen),
        "failed": failed,
        "results": [{"name": n, "passed": seen[n]} for n in names if n in seen],
    }


def _by_source(tests: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for test in tests:
        counts[test["source"]] = counts.get(test["source"], 0) + 1
    return counts


# --- create ------------------------------------------------------------------------------------
@logged_tool("registry_create_version")
def create_version(
    ctx: ToolContext,
    *,
    measure_name,
    method_ref,
    description,
    input_scope,
    source_code,
    output_kind,
    output_schema,
    methodology_document_id,
    section_sha256,
    clause_ids,
    template_version,
    tests=None,
    template_patch=None,
    rules_quoted=None,
    measure_id=None,
    declared_params=False,
):
    """Store a generated function as a new `proposed` version, with the result of the static check.

    The static check runs here, against the rules file and the numbers the named template holds, and
    its report is stored with the version. Code that fails it is kept (so the findings can be read)
    but can only be rejected. A version of a measure that already holds the same code built from the
    same section is not made twice. Nothing here runs the code.

    `declared_params` adds the two checks on how the function reads the school's values: every key
    it reads names a declared clause (`param_key_not_declared`) and every declared clause is read
    (`param_unused`). The measure build sets it. It is off by default, so a version registered
    without it is checked as before.
    """
    _text(measure_name, "a measure name")
    _text(method_ref, "the methodology section it comes from")
    _text(description, "a description")
    _text(source_code, "the source code")
    _text(template_version, "a template version")
    _text(methodology_document_id, "the methodology document")
    if input_scope not in t.INPUT_SCOPES:
        raise Refusal(f"input_scope must be one of {', '.join(t.INPUT_SCOPES)}")
    rules = load_rules()
    if output_kind not in rules.output_kinds:
        raise Refusal(f"output_kind must be one of {', '.join(rules.output_kinds)}")
    if not isinstance(output_schema, dict) or output_schema.get("kind") != output_kind:
        raise Refusal("output_schema must be an object whose kind is the output kind")
    if not isinstance(section_sha256, str) or not SHA256.match(section_sha256):
        raise Refusal("section_sha256 must be a sha256 in lower-case hex")
    if (
        not isinstance(clause_ids, list)
        or not clause_ids
        or not all(isinstance(c, str) and c.strip() for c in clause_ids)
    ):
        raise Refusal("clause_ids must be a list of the clauses the function reads")
    cleaned = _clean_tests(tests, t.BUILD_SOURCES)

    template = _cursor(ctx).execute(
        "SELECT content FROM templates WHERE version_name = %s", (template_version,)
    ).fetchone()
    if template is None:
        raise Refusal(f"there is no template version {template_version!r}")
    report = static_check.check_source(
        source_code,
        output_kind=output_kind,
        rules=rules,
        template_values=template_values(template["content"]),
        declared_clause_ids=clause_ids if declared_params else None,
    )
    digest = hashlib.sha256(source_code.encode("utf-8")).hexdigest()

    with _database(ctx):
        cursor = _cursor(ctx)
        if measure_id is None:
            measure_id = cursor.execute(
                "INSERT INTO measures (name, method_ref) VALUES (%s, %s) "
                "ON CONFLICT (method_ref, name) DO UPDATE SET name = EXCLUDED.name "
                "RETURNING measure_id",
                (measure_name, method_ref),
            ).fetchone()["measure_id"]
        elif not cursor.execute(
            "SELECT 1 FROM measures WHERE measure_id = %s", (measure_id,)
        ).fetchone():
            raise Refusal(f"there is no measure {measure_id}")
        held = cursor.execute(
            "SELECT version FROM measure_versions "
            "WHERE measure_id = %s AND code_sha256 = %s AND section_sha256 = %s",
            (measure_id, digest, section_sha256),
        ).fetchone()
        if held is not None:
            created, version = False, held["version"]
        else:
            version = cursor.execute(
                "SELECT coalesce(max(version), 0) + 1 AS next FROM measure_versions "
                "WHERE measure_id = %s",
                (measure_id,),
            ).fetchone()["next"]
            cursor.execute(
                "INSERT INTO measure_versions (measure_id, version, description, input_scope, "
                "source_code, code_sha256, methodology_document_id, section_sha256, clause_ids, "
                "template_version, template_patch, rules_quoted, output_kind, output_schema, "
                "static_check, tests, built_by_run) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    measure_id, version, description, input_scope, source_code, digest,
                    methodology_document_id, section_sha256, clause_ids, template_version,
                    jsonb(template_patch) if template_patch is not None else None,
                    jsonb(rules_quoted if rules_quoted is not None else []),
                    output_kind, jsonb(output_schema), jsonb(report.to_dict()), jsonb(cleaned),
                    ctx.run_id,
                ),
            )
            created = True
    return {
        "status": "ok",
        "measure_id": measure_id,
        "version": version,
        "version_status": t.PROPOSED,
        "created": created,
        "static_check": report.to_dict(),
    }, "ok"


# --- moving along the path ---------------------------------------------------------------------
@logged_tool("registry_advance_status")
def advance_status(ctx: ToolContext, *, measure_id, version, to):
    """Move a version one step along its path, or reject it. Only legal moves are made.

    It cannot approve a version (only the teacher's decision does) and it cannot retire one (that
    has its own function). The database refuses a move whose conditions are not met: no testing
    for code that failed the static check, no review before every test has passed, no demo while
    the automated review is holding it.
    """
    row = _load(ctx, measure_id, version)
    before = row["status"]
    if to == t.APPROVED:
        raise Refusal("only the teacher's decision can approve a version")
    if to not in t.ADVANCE_TARGETS:
        raise Refusal(f"a version cannot move to {to!r} with this function")
    if not t.is_legal(before, to):
        raise Refusal(f"a version cannot move from {before} to {to}")
    with _database(ctx):
        ctx.conn.execute(
            "UPDATE measure_versions SET status = %s WHERE measure_id = %s AND version = %s",
            (to, measure_id, version),
        )
    return _moved(measure_id, version, before, to), "ok"


@logged_tool("registry_record_tests")
def record_tests(ctx: ToolContext, *, measure_id, version, results, new_tests=None):
    """Record which tests passed, and add tests. The verdict is worked out here from the results.

    While the version is testing, the system may add `build`, `break_it` and `document_example`
    tests. At the demo only a signed-in teacher in the registry may add a case (source `teacher`).
    `results` is one {name, passed} for each test that ran; a test with no result counts as not
    passed. At the demo a result that does not pass is refused: a failing case is a reason for the
    teacher to reject the version, not a result to store beside a version that is waiting.
    """
    row = _load(ctx, measure_id, version)
    here = row["status"]
    if here not in t.RESULT_STATUSES:
        raise Refusal("test results are taken while a version is testing or at the demo")
    adding = _clean_tests(new_tests, t.DEMO_SOURCES if here == t.AWAITING_DEMO else t.BUILD_SOURCES)
    joining = None  # (person, name) when the setting accepts a teacher who has no row yet
    if adding:
        if here == t.AWAITING_DEMO:
            who, name, pending = _registered(ctx, t.TEACHER)
            joining = (who, name) if pending else None
        taken = {test["name"] for test in row["tests"]}
        clash = [test["name"] for test in adding if test["name"] in taken]
        if clash:
            raise Refusal(f"there is already a test called {clash[0]!r}")
    tests = [*row["tests"], *adding]
    outcome = _summarise(tests, results)
    if here == t.AWAITING_DEMO and not outcome["passed"]:
        raise Refusal(
            "a test fails at the demo: record that by rejecting the version, with a note, "
            "not as a result"
        )
    made = False
    with _database(ctx):
        if joining is not None:
            made = approvers.add_row(ctx, joining[0], t.TEACHER, joining[1])
        ctx.conn.execute(
            "UPDATE measure_versions SET tests = %s, test_result = %s "
            "WHERE measure_id = %s AND version = %s",
            (jsonb(tests), jsonb(outcome), measure_id, version),
        )
    if made:
        approvers.log(ctx, joining[0], t.TEACHER, joining[1])
    return {
        "status": "ok",
        "measure_id": measure_id,
        "version": version,
        "tests_passed": outcome["passed"],
        "ran": outcome["ran"],
        "failed": outcome["failed"],
        "by_source": _by_source(tests),
    }, "ok"


@logged_tool("registry_record_review")
def record_review(ctx: ToolContext, *, measure_id, version, result, reasons=None):
    """Record the automated review: `pass` or `hold`, with its reasons. Written once, never changed.

    This is a system step. A signed-in person cannot record it, so a person's judgement is never
    stored as if the review had made it. The review can only hold: it does not move the status, it
    never approves, and it cannot clear a hold (a hold is cleared by a registered developer).
    """
    if ctx.approver is not None:
        raise Refusal(
            "the automated review is a system step and cannot be recorded by a signed-in person"
        )
    if result not in t.REVIEW_RESULTS:
        raise Refusal(f"a review is {' or '.join(t.REVIEW_RESULTS)}")
    reasons = [] if reasons is None else reasons
    if not isinstance(reasons, list) or not all(isinstance(r, str) and r.strip() for r in reasons):
        raise Refusal("reasons must be a list of plain sentences")
    if result == t.HOLD and not reasons:
        raise Refusal("a hold must give its reasons")
    row = _load(ctx, measure_id, version)
    if row["status"] != t.AWAITING_REVIEW:
        raise Refusal("a review is recorded while the version waits for it")
    if row["review_result"] is not None:
        raise Refusal("the review is already recorded and cannot change")
    with _database(ctx):
        ctx.conn.execute(
            "UPDATE measure_versions SET review_result = %s, review_reasons = %s, "
            "reviewed_at = now() WHERE measure_id = %s AND version = %s",
            (result, jsonb(reasons), measure_id, version),
        )
    return {
        "status": "ok",
        "measure_id": measure_id,
        "version": version,
        "review_result": result,
        "reasons": reasons,
    }, "ok"


@logged_tool("registry_clear_hold")
def clear_hold(ctx: ToolContext, *, measure_id, version, note, to_demo=False):
    """A registered developer clears a hold, with a note saying what they checked.

    The person is the signed-in user. Nobody signed in, or someone not recorded as a developer in
    the registry, is refused. Clearing a hold does not approve anything: the version still goes to
    the teacher. With `REVIEWERS_ARE_APPROVERS` on, a signed-in approver with a school counts as a
    developer too (see `approvers`). The automated review never reaches this: it is a system step
    with no signed-in person, and a hold is only ever cleared by this explicit call.

    `to_demo` (default False) moves the version on to the demo in the same step, as the system's
    `advance_status` would, so that the approver's clearing does not need a second call. It changes
    nothing else: the database still refuses the move unless the review is cleared, and nothing is
    approved.
    """
    who, name, pending = _registered(ctx, t.DEVELOPER)
    _text(note, "a note saying what was checked")
    row = _load(ctx, measure_id, version)
    if row["status"] != t.AWAITING_REVIEW or row["review_result"] != t.HOLD:
        raise Refusal("there is no hold on this version to clear")
    if row["hold_cleared_by_id"] is not None:
        raise Refusal("the hold was already cleared")
    made = False
    with _database(ctx):
        if pending:
            made = approvers.add_row(ctx, who, t.DEVELOPER, name)
        ctx.conn.execute(
            "UPDATE measure_versions SET hold_cleared_by_id = %s, hold_cleared_name = %s, "
            "hold_cleared_at = now(), hold_cleared_note = %s "
            "WHERE measure_id = %s AND version = %s",
            (who.id, name, note, measure_id, version),
        )
        if to_demo is True:
            ctx.conn.execute(
                "UPDATE measure_versions SET status = %s WHERE measure_id = %s AND version = %s",
                (t.AWAITING_DEMO, measure_id, version),
            )
    if made:
        approvers.log(ctx, who, t.DEVELOPER, name)
    cleared = {
        "status": "ok",
        "measure_id": measure_id,
        "version": version,
        "cleared_by": who.id,
        "version_status": t.AWAITING_DEMO if to_demo is True else t.AWAITING_REVIEW,
    }
    return cleared, "ok"


@logged_tool("registry_record_teacher_decision")
def record_teacher_decision(ctx: ToolContext, *, measure_id, version, decision, note=None):
    """The teacher confirms or rejects a version at the demo. Confirming approves it.

    This is the only function that can make a version `approved`. The teacher is the signed-in
    user, and must be recorded as a teacher in the registry. The database refuses the approval
    unless the tests passed, at least one test is not the model's, the static check passed, the
    automated review is not holding, and this decision is `confirmed`. A rejection needs a note.
    With `REVIEWERS_ARE_APPROVERS` on, a signed-in approver with a school counts as a registered
    teacher: the registry row is made in the same savepoint, so a refused approval leaves none.
    Confirming a newer version retires the one in force in the same step; if the database refuses,
    the old one stays in force.
    """
    who, name, pending = _registered(ctx, t.TEACHER)
    if decision not in t.DECISIONS:
        raise Refusal(f"a decision is {' or '.join(t.DECISIONS)}")
    if decision == t.REJECTED:
        _text(note, "a rejection needs a note")
    row = _load(ctx, measure_id, version)
    if row["status"] != t.AWAITING_DEMO:
        raise Refusal("a decision is taken at the demo, when the version waits for the teacher")
    confirming = decision == t.CONFIRMED
    made = False
    with _database(ctx):
        if pending:
            made = approvers.add_row(ctx, who, t.TEACHER, name)
        if confirming:
            ctx.conn.execute(
                "UPDATE measure_versions SET status = 'retired', retired_reason = 'superseded' "
                "WHERE measure_id = %s AND status = 'approved' AND version <> %s",
                (measure_id, version),
            )
        ctx.conn.execute(
            "UPDATE measure_versions SET status = %s, teacher_id = %s, teacher_name = %s, "
            "teacher_decision = %s, teacher_note = %s, teacher_decided_at = now(), "
            "approved_at = CASE WHEN %s THEN now() END "
            "WHERE measure_id = %s AND version = %s",
            (
                "approved" if confirming else t.REJECTED,
                who.id, name, decision, note, confirming, measure_id, version,
            ),
        )
    if made:
        approvers.log(ctx, who, t.TEACHER, name)
    after = "approved" if confirming else t.REJECTED
    return _moved(measure_id, version, row["status"], after, decision=decision), "ok"


@logged_tool("registry_retire")
def retire(ctx: ToolContext, *, measure_id, version, reason):
    """Retire a version for a reason on the list: superseded, section_removed or withdrawn.

    A retired version is kept, never deleted, so a released report can still be reproduced.
    """
    if reason not in t.RETIRE_REASONS:
        raise Refusal(f"a reason is one of {', '.join(t.RETIRE_REASONS)}")
    row = _load(ctx, measure_id, version)
    before = row["status"]
    if not t.is_legal(before, t.RETIRED):
        raise Refusal(f"a version cannot move from {before} to {t.RETIRED}")
    with _database(ctx):
        ctx.conn.execute(
            "UPDATE measure_versions SET status = %s, retired_reason = %s "
            "WHERE measure_id = %s AND version = %s",
            (t.RETIRED, reason, measure_id, version),
        )
    return _moved(measure_id, version, before, t.RETIRED, reason=reason), "ok"
