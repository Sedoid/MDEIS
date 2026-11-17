"""The demo's functions: what a registered teacher can do with a measure that waits at the demo.
With the server setting `REVIEWERS_ARE_APPROVERS` on, a signed-in approver with a school counts as
one (T-077).

    list_awaiting   the versions waiting for a teacher
    open_demo       everything a teacher needs to decide, with the worked examples run for real
    try_measure     the teacher's own synthetic scores, run through the function
    save_case       the teacher's expected answer for such a try, stored as a test from outside the
                    build (only when the function's answer is the same)
    confirm         the teacher's decision to confirm, with a note if they wish
    reject          the teacher's decision to reject, with the reason
    list_held       the versions the automated review holds (the approver's)
    lift_hold       the approver clears one hold, with a note (the approver's)

Each takes the context first (the connection, the run and the signed-in person) and plain arguments.
None has an argument that names a person, a school, a learner or a decision: the person and the
school are read from the session the server built from the signed token, and the person must be
recorded as a teacher in the registry. Each writes one action-log row under its own name, with that
person, including when it refuses.

What these functions never do. They never approve by themselves: opening, trying and saving a case
leave the version where it was, and only `confirm` records a decision (through the registry's own
function, which the database backs with its own constraint). They never move a version along by a
call of their own, create or retire anything, and never write SQL of their own to the registry. The
one place a hold is cleared is `lift_hold`, through the registry's own function, for a named
signed-in person with a note: it removes the hold only, approves nothing, and the registry moves the
version on to the demo, where a teacher still decides. The automated review can only hold. They
call no model. Every number comes from the function, run in the sandbox through
`sandbox.with_template`, with the values the rulebook and the build's proposed patch give it.

A refusal is a `Refusal` whose first words are a code in square brackets, so the routes can type it.
"""

from __future__ import annotations

from typing import NoReturn

from psycopg.rows import dict_row

from .. import approver_setting as setting
from ..core import measures as core
from ..core.numbers import exact_text
from ..core.rules import load_rules
from ..measure_build import engine
from ..measure_build import store as build_store
from ..registry import clear_hold, record_teacher_decision, record_tests
from ..registry import transitions as t
from ..sandbox import with_template
from ..sandbox.protocol import UNAVAILABLE
from ..tools._call import logged_tool
from ..tools._shared import with_hash
from ..tools.context import Refusal, ToolContext
from . import compose

SKIPPED_FIELDS = ("section_id", "title", "change", "clause_ids", "tool", "reason")
HOLD_NEXT_STEP = (
    "The hold is cleared. Nothing is approved: the version now waits at the demo, where a teacher "
    "still tries it and decides."
)
NEXT_STEP = (
    "An administrator now applies the template patch with apply-template-patch, so the values you "
    "confirmed enter the school's rulebook. Until then this measure is approved but is not offered "
    "or run for anyone."
)
KEPT_STEP = "The function that was in force before stays in force."

VERSION_SQL = (
    "SELECT m.measure_id, m.name, m.method_ref, v.version, v.status, v.description, "
    "v.input_scope, v.output_kind, v.output_schema, v.clause_ids, v.template_version, "
    "v.template_patch, v.rules_quoted, v.methodology_document_id, v.section_sha256, "
    "v.code_sha256, v.source_code, v.static_check, v.tests, v.test_result, v.review_result, "
    "v.review_reasons, v.hold_cleared_name, v.built_by_run, v.teacher_name, "
    "registry_tests_passed(v.tests, v.test_result) AS tests_passed, "
    "registry_has_independent_test(v.tests) AS independent_test, "
    "registry_static_passed(v.static_check) AS static_passed, "
    "measure_review_cleared(v.review_result, v.hold_cleared_by_id) AS review_cleared "
    "FROM measure_versions v JOIN measures m USING (measure_id) "
    "WHERE v.measure_id = %s AND v.version = %s"
)


# --- small helpers -----------------------------------------------------------------------------
def _refuse(code: str, detail: str) -> NoReturn:
    raise Refusal(f"[{code}] {detail}")


def _cursor(ctx: ToolContext):
    return ctx.conn.cursor(row_factory=dict_row)


def _person(ctx: ToolContext, role: str, code: str) -> str:
    """The name the registry holds for the signed-in person in `role`, or a refusal. The person is
    the one in the session; the school must be attached to it. Nothing here reads either from an
    argument.

    With `REVIEWERS_ARE_APPROVERS` on, a signed-in approver with a school and no record in the
    registry is accepted under the name the token gave. Nothing is written here: the registry makes
    the row, in the same savepoint as the decision (`registry.approvers`). A record an administrator
    ended is still refused."""
    who = ctx.approver
    if who is None:
        _refuse("not_signed_in", "no one is signed in, so this cannot be done")
    if setting.school_of(ctx) is None:
        _refuse("no_school", "no school is attached to this session")
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
        return row["name"]
    if setting.enabled():
        if setting.ended(ctx.conn, who.id, role):
            _refuse(code, setting.ended_text(role))
        name = setting.name_of(who)
        if name is None:
            _refuse(code, "the signed-in approver has no name in the session")
        return name
    _refuse(code, f"the signed-in person is not recorded as a {role} in the registry")


def _teacher(ctx: ToolContext) -> str:
    """The signed-in teacher (see `_person`): the demo's functions are a teacher's."""
    return _person(ctx, t.TEACHER, "not_a_teacher")


def _developer(ctx: ToolContext) -> str:
    """The signed-in developer (see `_person`): who may see and clear a hold."""
    return _person(ctx, t.DEVELOPER, "not_a_developer")


def _load(ctx: ToolContext, measure_id, version) -> dict:
    found = None
    if isinstance(measure_id, str) and isinstance(version, int) and not isinstance(version, bool):
        found = _cursor(ctx).execute(VERSION_SQL, (measure_id, version)).fetchone()
    if found is None:
        _refuse("not_found", f"there is no version {version} of {measure_id}")
    return found


def _at_demo(ctx: ToolContext, measure_id, version) -> dict:
    row = _load(ctx, measure_id, version)
    if row["status"] != t.AWAITING_DEMO:
        _refuse("not_at_demo", f"this version is not waiting for a teacher ({row['status']})")
    return row


def _active(ctx: ToolContext) -> tuple:
    """The active rulebook's rules and its stored content."""
    rules = load_rules(ctx.conn, None)
    row = ctx.conn.execute("SELECT content FROM templates WHERE active").fetchone()
    return rules, row[0]


def _patches(ctx: ToolContext, row: dict) -> tuple[dict | None, list]:
    """The template patch stored for this version (its ID, its state and the clauses it proposes).
    With none stored, the patch kept on the version row, which has the clauses and no ID."""
    found = (
        _cursor(ctx)
        .execute(
            "SELECT patch_id, status, applied_as, clauses FROM template_patches "
            "WHERE measure_id = %s AND version = %s ORDER BY created_at, patch_id",
            (row["measure_id"], row["version"]),
        )
        .fetchall()
    )
    if found:
        first = found[0]
        info = {
            "patch_id": first["patch_id"],
            "status": first["status"],
            "applied_as": first["applied_as"],
        }
        return info, [clause for patch in found for clause in patch["clauses"]]
    kept = row["template_patch"] or {}
    return None, list(kept.get("measure_params") or [])


def _values_content(content: dict, clauses: list) -> dict:
    """The active rulebook with the build's proposed clauses added where the rulebook has none."""
    held = {r.get("id") for r in content.get("measure_params") or []}
    return engine.with_clauses(content, [c for c in clauses if c.get("id") not in held])


def _recorded(row: dict, clauses: list) -> dict:
    return with_template.recorded_types(clauses, row["template_patch"])


class _Failed(Exception):
    """A run that gave no answer. `code` is the bracket code of the refusal; `detail` is plain."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _run(row: dict, records: list, content: dict, recorded: dict, banned) -> tuple:
    """(numbers, values used): one run in the sandbox, checked. One call, never a second try.
    Raises _Failed."""
    if row["output_kind"] not in core.SUPPORTED_KINDS:
        raise _Failed("measure_failed", "this kind of answer cannot be shown yet")
    try:
        ran = with_template.run(
            row["source_code"],
            row["code_sha256"],
            records,
            content,
            row["clause_ids"],
            output_kind=row["output_kind"],
            recorded=recorded,
            unconfirmed_ok=True,
        )
    except with_template.ParamsRefusal as why:
        raise _Failed("measure_failed", f"the values could not be built ({why.kind})") from None
    except with_template.RunnerRefusal as why:
        if why.kind == UNAVAILABLE:
            raise _Failed("runner_unavailable", why.detail) from None
        raise _Failed("measure_failed", f"the function gave no answer ({why.kind})") from None
    sent = [r["record_id"] for r in records if "record_id" in r]
    try:
        if ran["output_kind"] != row["output_kind"]:
            raise core.OutputRefusal("kind_changed", "the answer is of another kind")
        checked = core.check_number_output(ran["output"], sent, row["output_schema"], banned)
    except core.OutputRefusal as why:
        raise _Failed("measure_failed", f"the answer was refused ({why.code})") from None
    return checked["numbers"], ran["params"]


def _try_records(row: dict, rules, stage, entries) -> list:
    try:
        return compose.try_records(
            {"stage": stage, "entries": entries},
            fields=compose.TRY_FIELDS,
            subjects=list(rules.subjects),
            stages=list(rules.stage_weights),
            scale_max=rules.scale_max,
            scope=row["input_scope"],
        )
    except compose.TryRefused as why:
        _refuse(why.code, why.detail)
    return []  # (not reached: _refuse always raises)


def _quotes(ctx: ToolContext, row: dict) -> list:
    """Each rule the build quoted, with where it was quoted from: the section, its title and page in
    the methodology document, and the hash of that section's text."""
    out = []
    for quoted in row["rules_quoted"] or []:
        section = quoted.get("quote_section")
        chunk = (
            _cursor(ctx)
            .execute(
                "SELECT title, page, text_sha256 FROM method_chunks "
                "WHERE document_id = %s AND section_id = %s ORDER BY ordinal LIMIT 1",
                (row["methodology_document_id"], section),
            )
            .fetchone()
        ) or {}
        out.append(
            {
                "key": quoted.get("key"),
                "name": quoted.get("name"),
                "source": quoted.get("source"),
                "quote": quoted.get("quote"),
                "section_id": section,
                "title": chunk.get("title"),
                "page": chunk.get("page"),
                "section_sha256": chunk.get("text_sha256"),
            }
        )
    return out


def _example_answers(row: dict, content: dict, recorded: dict, banned) -> dict:
    """Run each worked example from the methodology, as stored, and keep what came back. An example
    that cannot be run is kept as a problem, not skipped."""
    answers = {}
    for test in row["tests"]:
        if test.get("source") != "document_example":
            continue
        try:
            numbers, _ = _run(row, test["records"], content, recorded, banned)
            answers[test["name"]] = {"numbers": numbers}
        except _Failed as failed:
            answers[test["name"]] = {"problem": failed.code}
    return answers


def _facts(row: dict) -> dict:
    return {
        "status": row["status"],
        "tests_passed": row["tests_passed"],
        "independent_test": row["independent_test"],
        "static_passed": row["static_passed"],
        "review_cleared": row["review_cleared"],
        "review_result": row["review_result"],
    }


def _wording(row: dict, content: dict) -> list:
    text = " ".join([row["name"], row["description"], *(row["review_reasons"] or [])])
    return engine.banned_in(text, content)


def _form(row: dict, rules) -> dict:
    return {
        "scope": row["input_scope"],
        "stages": list(rules.stage_weights),
        "subjects": list(rules.subjects),
        "scale_max": exact_text(rules.scale_max),
    }


# --- the list ----------------------------------------------------------------------------------
@logged_tool("measure_demo_list")
def list_awaiting(ctx: ToolContext):
    """The versions waiting for a teacher, oldest first, with what is needed to recognise each."""
    _teacher(ctx)
    rows = (
        _cursor(ctx)
        .execute(
            "SELECT m.measure_id, v.version, m.name, m.method_ref, v.description "
            "FROM measure_versions v JOIN measures m USING (measure_id) "
            "WHERE v.status = %s AND m.status = 'active' ORDER BY m.measure_id, v.version",
            (t.AWAITING_DEMO,),
        )
        .fetchall()
    )
    listed = [dict(r) for r in rows]
    return {"status": "ok", "waiting": len(listed), "measures": listed}, "ok"


# --- the demo ----------------------------------------------------------------------------------
@logged_tool("measure_demo_open")
def open_demo(ctx: ToolContext, measure_id, version):
    """Everything a teacher needs to decide about one version: where it came from, the rules and
    values it reads, what the checks found, the worked examples run for real, the teacher's own
    cases so far, and what is still missing before it can be confirmed. Nothing is changed."""
    _teacher(ctx)
    row = _at_demo(ctx, measure_id, version)
    rules, content = _active(ctx)
    info, clauses = _patches(ctx, row)
    shown = _values_content(content, clauses)
    recorded = _recorded(row, clauses)
    answers = _example_answers(row, shown, recorded, rules.banned_words)
    plan = build_store.latest_plan(ctx.conn, row["methodology_document_id"])
    view = compose.demo_view(
        version=row,
        quotes=_quotes(ctx, row),
        clauses=compose.clause_rows(
            row["clause_ids"], content, clauses, _values_read(row, shown, recorded)
        ),
        patch=info,
        skipped=[{k: s.get(k) for k in SKIPPED_FIELDS} for s in plan["skipped"]],
        build_anyway=list(plan["build_anyway"]),
        examples=compose.example_rows(row["tests"], answers),
        cases=compose.case_rows(row["tests"], row["test_result"]),
        facts=_facts(row),
        wording=_wording(row, content),
        form=_form(row, rules),
    )
    return view, "ok"


def _values_read(row: dict, shown: dict, recorded: dict) -> dict:
    """The values the function is given, by key, built from the rulebook and the proposed patch by
    the same code a run uses. Used to show what each clause contributes."""
    try:
        return with_template.values_for(
            row["source_code"], shown, row["clause_ids"], recorded=recorded, unconfirmed_ok=True
        )
    except with_template.ParamsRefusal:
        return {}  # the demo still opens; the clause then shows no value read


# --- try your own ------------------------------------------------------------------------------
def _answer_for(ctx: ToolContext, measure_id, version, stage, entries) -> dict:
    """Run the function on the teacher's synthetic scores. Returns what both `try_measure` and
    `save_case` need. Raises Refusal."""
    row = _at_demo(ctx, measure_id, version)
    rules, content = _active(ctx)
    records = _try_records(row, rules, stage, entries)
    _, clauses = _patches(ctx, row)
    shown = _values_content(content, clauses)
    try:
        numbers, used = _run(row, records, shown, _recorded(row, clauses), rules.banned_words)
    except _Failed as failed:
        _refuse(failed.code, failed.detail)
    return {
        "row": row,
        "records": records,
        "numbers": numbers,
        "used": used,
        "clauses": compose.clause_rows(row["clause_ids"], content, clauses, used),
    }


@logged_tool("measure_demo_try")
def try_measure(ctx: ToolContext, measure_id, version, stage, entries):
    """Run the function on a few synthetic scores the teacher typed, and show its exact answer and
    the rules and values it used. The same scores always give the same answer. Nothing is stored."""
    _teacher(ctx)
    made = _answer_for(ctx, measure_id, version, stage, entries)
    result = {
        "status": "ok",
        "measure_id": measure_id,
        "version": version,
        "stage": stage,
        "scores": [
            {"subject": r["subject"], "term": r["term"], "score": r["score"]}
            for r in made["records"]
        ],
        "answer": compose.answer_view(made["numbers"]),
        "values_used": made["used"],
        "clauses": made["clauses"],
        "notice": compose.SYNTHETIC_NOTICE,
    }
    with_hash(result)
    return result, "ok"


@logged_tool("measure_demo_case")
def save_case(ctx: ToolContext, measure_id, version, stage, entries, expected, agreed=False):
    """Store the teacher's expected answer for a set of scores as a test from outside the build.

    The function is run again on the scores. The case is stored only when its answer is the same as
    the teacher's expected answer. When it differs nothing is stored and both answers are shown, so
    the teacher can decide whether to reject the version instead. `agreed` records that the teacher
    chose to take the answer the function showed as their own."""
    _teacher(ctx)
    if not isinstance(agreed, bool):
        _refuse("bad_body", compose.REFUSAL_TEXT["bad_body"])
    made = _answer_for(ctx, measure_id, version, stage, entries)
    row = made["row"]
    declared = (row["output_schema"] or {}).get("fields") or []
    try:
        wanted = compose.expected_numbers(expected, declared)
    except compose.TryRefused as why:
        _refuse(why.code, why.detail)
    checked = compose.compare(wanted, made["numbers"])
    if not checked["match"]:
        return {
            "status": "refused",
            "reason": (
                "[case_mismatch] the function's answer is not the answer you expected, so no case "
                "was stored"
            ),
            "answer": compose.answer_view(made["numbers"]),
            "compare": checked,
            "values_used": made["used"],
        }, "refused"
    taken = sum(1 for test in row["tests"] if test.get("source") == "teacher")
    name = f"teacher case {taken + 1}"
    case = {
        "name": name,
        "source": "teacher",
        "records": made["records"],
        "expected": wanted,
        "basis": "agreed_with_answer" if agreed else "typed",
    }
    earlier = [
        {"name": r["name"], "passed": r["passed"]}
        for r in (row["test_result"] or {}).get("results") or []
    ]
    stored = record_tests(
        ctx,
        measure_id=measure_id,
        version=version,
        results=[*earlier, {"name": name, "passed": True}],
        new_tests=[case],
    )
    if stored.get("status") != "ok":
        return {"status": "refused", "reason": stored.get("reason") or "refused"}, "refused"
    return {
        "status": "saved",
        "measure_id": measure_id,
        "version": version,
        "name": name,
        "expected": wanted,
        "answer": compose.answer_view(made["numbers"]),
        "tests_passed": stored["tests_passed"],
        "by_source": stored["by_source"],
    }, "ok"


# --- the decision ------------------------------------------------------------------------------
def _clean_note(note) -> str | None:
    if note is None:
        return None
    if not isinstance(note, str):
        _refuse("bad_body", compose.REFUSAL_TEXT["bad_body"])
    return note.strip() or None


@logged_tool("measure_demo_confirm")
def confirm(ctx: ToolContext, measure_id, version, note=None):
    """The teacher confirms a version. The registry's conditions are read from the database's own
    functions first, and when one is not met the answer says plainly what is missing and nothing is
    recorded. When they are met the decision is recorded by the registry, which approves the
    version, and the database checks the same conditions again. The values the version reads are
    confirmed with it: the teacher's name is the one the registry holds, never one from here. The
    template patch itself is applied later by an administrator."""
    _teacher(ctx)
    note = _clean_note(note)
    row = _load(ctx, measure_id, version)
    if row["status"] != t.AWAITING_DEMO:
        _refuse("not_at_demo", f"this version is not waiting for a teacher ({row['status']})")
    _, content = _active(ctx)
    gaps = compose.approval_gaps(_facts(row), _wording(row, content))
    if gaps:
        words = " ".join(g["text"] for g in gaps)
        return {
            "status": "refused",
            "reason": f"[approval_conditions_not_met] the registry would refuse this: {words}",
            "gaps": gaps,
        }, "refused"
    decided = record_teacher_decision(
        ctx, measure_id=measure_id, version=version, decision=t.CONFIRMED, note=note
    )
    if decided.get("status") != "ok":
        return {"status": "refused", "reason": decided.get("reason") or "refused"}, "refused"
    after = _load(ctx, measure_id, version)
    info, clauses = _patches(ctx, after)
    values = [
        {k: c[k] for k in ("clause_id", "name", "value", "unit", "method_ref")}
        for c in compose.clause_rows(after["clause_ids"], content, clauses, {})
        if c["kind"] == compose.MEASURE_PARAMETER and c["state"] != compose.MISSING
    ]
    return {
        "status": "confirmed",
        "measure_id": measure_id,
        "version": version,
        "from_status": decided["from_status"],
        "to_status": decided["to_status"],
        "decision": t.CONFIRMED,
        "teacher_name": after["teacher_name"],
        "values_confirmed_by": after["teacher_name"],
        "values": values,
        "patch": info,
        "next_step": NEXT_STEP,
    }, "ok"


@logged_tool("measure_demo_reject")
def reject(ctx: ToolContext, measure_id, version, note):
    """The teacher rejects a version, with the reason. The version is closed. The function that was
    in force before it stays in force."""
    _teacher(ctx)
    note = _clean_note(note)
    if note is None:
        _refuse("note_needed", "a rejection needs a note saying why")
    row = _load(ctx, measure_id, version)
    if row["status"] != t.AWAITING_DEMO:
        _refuse("not_at_demo", f"this version is not waiting for a teacher ({row['status']})")
    decided = record_teacher_decision(
        ctx, measure_id=measure_id, version=version, decision=t.REJECTED, note=note
    )
    if decided.get("status") != "ok":
        return {"status": "refused", "reason": decided.get("reason") or "refused"}, "refused"
    after = _load(ctx, measure_id, version)
    return {
        "status": "rejected",
        "measure_id": measure_id,
        "version": version,
        "from_status": decided["from_status"],
        "to_status": decided["to_status"],
        "decision": t.REJECTED,
        "teacher_name": after["teacher_name"],
        "note": note,
        "next_step": KEPT_STEP,
    }, "ok"


# --- the approver's: a hold of the automated review ----------------------------------------------
@logged_tool("measure_demo_held")
def list_held(ctx: ToolContext):
    """The versions the automated review holds and nobody has cleared, oldest first, with the
    reasons the review gave. Nothing is changed."""
    _developer(ctx)
    rows = (
        _cursor(ctx)
        .execute(
            "SELECT m.measure_id, v.version, m.name, m.method_ref, v.description, "
            "v.review_reasons FROM measure_versions v JOIN measures m USING (measure_id) "
            "WHERE v.status = %s AND v.review_result = %s AND v.hold_cleared_by_id IS NULL "
            "AND m.status = 'active' ORDER BY m.measure_id, v.version",
            (t.AWAITING_REVIEW, t.HOLD),
        )
        .fetchall()
    )
    listed = [compose.held_item(r) for r in rows]
    return {"status": "ok", "held": len(listed), "measures": listed}, "ok"


@logged_tool("measure_demo_lift_hold")
def lift_hold(ctx: ToolContext, measure_id, version, note):
    """The signed-in approver clears the hold on one version, with a note saying what they checked.

    This removes the hold and nothing else. It approves nothing and makes no decision: the registry
    moves the version on to the demo, and a teacher still tries it and decides. The person and the
    note are recorded by the registry's own function (`clear_hold`), which the database backs with
    its own guard. The automated review can only hold: it never reaches this."""
    _developer(ctx)
    note = _clean_note(note)
    if note is None:
        _refuse("note_needed", "a note saying what you checked is needed")
    row = _load(ctx, measure_id, version)
    if (
        row["status"] != t.AWAITING_REVIEW
        or row["review_result"] != t.HOLD
        or row["hold_cleared_name"] is not None
    ):
        _refuse("not_held", "there is no hold on this version to clear")
    cleared = clear_hold(ctx, measure_id=measure_id, version=version, note=note, to_demo=True)
    if cleared.get("status") != "ok":
        return {"status": "refused", "reason": cleared.get("reason") or "refused"}, "refused"
    after = _load(ctx, measure_id, version)
    return {
        "status": "cleared",
        "measure_id": measure_id,
        "version": version,
        "from_status": row["status"],
        "to_status": after["status"],
        "cleared_by_name": after["hold_cleared_name"],
        "note": note,
        "next_step": HOLD_NEXT_STEP,
    }, "ok"
