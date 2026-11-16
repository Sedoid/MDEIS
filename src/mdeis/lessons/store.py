"""The lesson store: save a checked plan, read it, reject it, and record the tutor's approval.

Each function takes the context (the connection, the run, and the signed-in person) and nothing that
names a person or a school. The author and the approver are read from `ctx.approver`, which the
server sets from the verified token; the school is read from the session. A model can write the edit
list and nothing else. It cannot name an author, an approver or a status: the edit contract refuses
any key it does not list.

What is stored, and what is not:
  * notes that name a learner are refused before anything is read or written; no row, and the diary
    holds only a hash and a length of the notes (`redact`);
  * when there is not enough class evidence, or no gap is shown, or no edit passed the checks, no
    row is stored: the result says so plainly;
  * a plan row holds only edits that passed, each with the evidence sentence code made for it. A
    dropped edit is stored as an id, its LC ids and a plain reason, never as the model's text.

Only `approve_lesson_plan` writes the approved status. The database holds the same rule as a
constraint (migration 009): the last lock. Every function writes one diary row (`logged_tool`),
including a refusal.
"""

import re
from contextlib import contextmanager

import psycopg
from psycopg import errors
from psycopg.rows import dict_row

from ..db.diary import jsonb
from ..tools._call import logged_tool
from ..tools.context import Refusal, ToolContext
from .apply import apply_edits
from .checks import PlanError, Selection, check_edits
from .edits import Edit, parse_edits
from .evidence import evidence_sentence
from .findings import FindingError, findings_from_class_result
from .guard import Roster, guard_notes
from .parse import LessonError, parse_lesson, sha256
from .rules import load_lesson_rules

# The class tools whose diary rows count as "returned in this run".
CLASS_TOOLS = ("class_baseline",)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
LIST_LIMIT = 100  # a page size for the saved-plans list, not a rule
CHECK_KEYS = ("passed", "objective_unchanged", "outside_unchanged")

PLAIN = {
    "approved_needs_approver_and_check": (
        "approval needs a passed check, at least one accepted edit that is one of the kept edits, "
        "and the author as the approver"
    ),
    "decision_whole": "the decision must be recorded whole",
    "plan_has_kept_edit": "a plan needs at least one edit that passed the checks",
    "edits_cite_stored_evidence": "every edit must cite evidence that is stored with the plan",
}
DATABASE_REFUSALS = (
    errors.CheckViolation,
    errors.RestrictViolation,
    errors.ForeignKeyViolation,
    errors.NotNullViolation,
    errors.InsufficientPrivilege,
)


# --- small helpers -----------------------------------------------------------------------------
def _signed_in(ctx: ToolContext):
    if ctx.approver is None:
        raise Refusal("no one is signed in, so this cannot be recorded")
    return ctx.approver


def _school(ctx: ToolContext) -> str:
    school = getattr(ctx, "school", None)
    if not isinstance(school, str) or not school:
        raise Refusal("no school is attached to this session")
    return school


@contextmanager
def _database(ctx: ToolContext):
    """Run database work in a savepoint. A rule of the database becomes a plain refusal. The text of
    the failing row is never part of the message (it would hold the tutor's notes)."""
    try:
        with ctx.conn.transaction():
            yield
    except psycopg.Error as error:
        named = error.diag.constraint_name
        reason = PLAIN.get(named) or error.diag.message_primary or type(error).__name__
        kind = "refused it" if isinstance(error, DATABASE_REFUSALS) else "could not do it"
        raise Refusal(f"the database {kind}: {reason}") from None


def _roster(ctx: ToolContext, school: str) -> Roster:
    rows = ctx.conn.execute(
        "SELECT display_name, school_ref FROM learners WHERE school_code = %s", (school,)
    ).fetchall()
    return Roster(
        names=tuple(r[0] for r in rows if r[0]), refs=tuple(r[1] for r in rows if r[1])
    )


def _class_findings(ctx: ToolContext, school: str) -> list:
    """The class findings a class tool returned in this run, for this school. They are read from the
    diary, so the model cannot pass in a finding of its own."""
    rows = ctx.conn.execute(
        "SELECT outputs FROM action_log WHERE run_id = %s AND outcome = 'ok' AND tool = ANY(%s) "
        "ORDER BY event_id",
        (ctx.run_id, list(CLASS_TOOLS)),
    ).fetchall()
    found = []
    for (outputs,) in rows:
        if not isinstance(outputs, dict) or outputs.get("school_id") not in (None, school):
            continue
        try:
            found += findings_from_class_result(outputs)
        except FindingError:
            continue
    return found


def _selection(rules, stage, area, term) -> Selection:
    if not isinstance(stage, str) or not isinstance(area, str):
        raise Refusal("the stage and the area must be text")
    problem = rules.selection_problem((stage, area, term))
    if problem:
        raise Refusal(problem)
    return Selection(stage, area, term)


def _guard(rules, roster, notes, objective) -> None:
    for text, limit in ((notes, rules.max_notes_chars), (objective, None)):
        result = guard_notes(text, roster, max_chars=limit)
        if not result.ok:
            raise Refusal(result.message)


def _dropped_view(checked) -> list:
    return [
        {"id": v.edit_id, "lc": list(v.lc_ids), "reason": "; ".join(v.reasons),
         "repairable": v.repairable}
        for v in checked.dropped
    ]


def _snapshot(kept, findings, rules) -> list:
    """The findings the kept edits cite, as stored: shown counts, clause ids, the sentence."""
    by_id = {f.finding_id: f for f in findings}
    cited = list(dict.fromkeys(i for e in kept for i in e.finding_ids))
    out = []
    for finding_id in cited:
        f = by_id[finding_id]
        out.append({
            "finding_id": f.finding_id, "status": f.status, "stage": f.stage, "area": f.area,
            "term": f.term, "academic_year": f.academic_year,
            "template_version": f.template_version, "group_size": f.group_size,
            "bins": [
                {"bin": b.name, "shown": b.shown, "count": b.count, "level_clause": b.level_clause}
                for b in f.bins or ()
            ],
            "clause_ids": list(f.clause_ids),
            "sentence": evidence_sentence(f, rules),
        })
    return out


def _edits_view(kept_edits, evidence) -> list:
    sentences = {e["finding_id"]: e["sentence"] for e in evidence}
    return [
        {
            "id": e["id"], "type": e["type"], "anchor_quote": e["anchor_quote"],
            "replacement": e["replacement"], "suggestion": e["suggestion"],
            "finding_ids": list(e["finding_ids"]),
            "evidence": [sentences[i] for i in e["finding_ids"] if i in sentences],
        }
        for e in kept_edits
    ]


def _diff_view(revised) -> dict:
    return {
        "lines": [
            {"op": d.op, "old": list(d.old), "new": list(d.new)} for d in revised.diff
        ],
        "changes": [
            {"edit_id": c.edit_id, "type": c.type, "start": c.start, "end": c.end,
             "before": c.before, "after": c.after, "new_start": c.new_start, "new_end": c.new_end}
            for c in revised.changes
        ],
    }


def _load(ctx: ToolContext, plan_id, school: str, lock: bool = False) -> dict:
    if not isinstance(plan_id, str) or not plan_id:
        raise Refusal("a plan id is needed")
    sql = "SELECT * FROM lesson_plans WHERE plan_id = %s" + (" FOR UPDATE" if lock else "")
    row = ctx.conn.cursor(row_factory=dict_row).execute(sql, (plan_id,)).fetchone()
    if row is None or row["school_code"] != school:
        raise Refusal(f"there is no lesson plan {plan_id} for this school")
    return row


def _checks_passed(check_result) -> bool:
    """True only when the stored check result says every check passed. Read from the row, never
    from a caller."""
    return isinstance(check_result, dict) and all(check_result.get(k) is True for k in CHECK_KEYS)


def _own(row: dict, who) -> None:
    if row["author_id"] != who.id:
        raise Refusal("this lesson plan belongs to another author")


def _kept_edits(row: dict) -> list:
    return [
        Edit(
            id=e["id"], type=e["type"], anchor_quote=e["anchor_quote"],
            replacement=e["replacement"], finding_ids=tuple(e["finding_ids"]),
            resource_ids=(), suggestion=e["suggestion"],
        )
        for e in row["edits"]["kept"]
    ]


# --- the notes guard, before any model call ------------------------------------------------------
@logged_tool("lesson_guard_notes", redact=("notes", "objective"))
def guard_lesson_notes(ctx: ToolContext, stage, area, term, objective, notes):
    """Refuse notes or an objective that name a learner, are empty, or are over the rulebook's
    limit. Notes are never cleaned. The result holds a hash and a length, never the text."""
    _signed_in(ctx)
    school = _school(ctx)
    rules = load_lesson_rules(ctx.conn)
    _selection(rules, stage, area, term)
    _guard(rules, _roster(ctx, school), notes, objective)
    return {
        "status": "ok",
        "notes_sha256": sha256(notes),
        "objective_sha256": sha256(objective),
        "length": len(notes),
        "template_version": rules.version,
    }, "ok"


# --- save ------------------------------------------------------------------------------------
@logged_tool("lesson_save_plan", redact=("notes", "objective", "model_output"))
def save_lesson_plan(
    ctx: ToolContext, stage, area, term, objective, notes, model_output, model, prompt_sha256
):
    """Check a model's edit list against the tutor's notes and the class findings of this run, and
    store the plan when at least one edit passed. The author is the signed-in user.

    Not stored, with a plain status: `insufficient_evidence` and `no_gap` (the class evidence does
    not support an edit), `no_valid_edits` (every edit was dropped, each listed with its LC ids),
    `invalid_output` (the output is not the edit contract; the graph may repair it).
    """
    author = _signed_in(ctx)
    school = _school(ctx)
    if not isinstance(model, str) or not model.strip():
        raise Refusal("the model that wrote the edits must be named")
    if not isinstance(prompt_sha256, str) or not SHA256.match(prompt_sha256):
        raise Refusal("prompt_sha256 must be a sha256 in lower-case hex")
    rules = load_lesson_rules(ctx.conn)
    selection = _selection(rules, stage, area, term)
    roster = _roster(ctx, school)
    _guard(rules, roster, notes, objective)
    try:
        lesson = parse_lesson(notes, objective)
    except LessonError as error:
        raise Refusal(str(error)) from None

    contract = parse_edits(model_output)
    if contract.error:
        return {"status": "invalid_output", "reason": contract.error, "repairable": True}, "refused"

    findings = _class_findings(ctx, school)
    try:
        checked = check_edits(lesson, contract.edits, findings, rules, selection, roster)
    except PlanError as error:
        raise Refusal(str(error)) from None
    dropped = _dropped_view(checked)
    if not checked.usable_evidence:
        return {
            "status": checked.statement_kind, "statement": checked.statement, "dropped": dropped,
        }, "ok"
    if not checked.kept:
        return {"status": "no_valid_edits", "dropped": dropped}, "ok"

    evidence = _snapshot(checked.kept, findings, rules)
    stored = checked.stored_edits()
    methodology = ctx.conn.execute(
        "SELECT document_id FROM documents WHERE kind = 'methodology' AND active"
    ).fetchone()
    pins = {
        "template_version": rules.version,
        "template_sha256": rules.sha256,
        "methodology_document_id": methodology[0] if methodology else None,
        "limits_clause": rules.limits_clause,
        "cell_clause": rules.cell_clause,
    }
    check_result = {
        "passed": checked.passed,
        "objective_unchanged": checked.objective_unchanged,
        "outside_unchanged": checked.outside_unchanged,
        "notices": [{"clause_id": n.clause_id, "where": n.where} for n in checked.notices],
    }
    diff = _diff_view(checked.revised)
    with _database(ctx):
        plan_id = ctx.conn.execute(
            "INSERT INTO lesson_plans (school_code, stage, area, term, author_id, author_name, "
            "original_text, original_sha256, objective_text, objective_sha256, template_version, "
            "evidence, pins, edits, check_result, revised_text, revised_sha256, diff, model, "
            "prompt_sha256, run_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s) RETURNING plan_id",
            (
                school, stage, area, term, author.id, author.name, notes, lesson.text_sha256,
                objective, lesson.objective_sha256, rules.version, jsonb(evidence), jsonb(pins),
                jsonb(stored), jsonb(check_result), checked.revised.text,
                checked.revised.text_sha256, jsonb(diff), model, prompt_sha256, ctx.run_id,
            ),
        ).fetchone()[0]
    return {
        "status": "stored",
        "plan_id": plan_id,
        "plan_status": "proposed",
        "stage": stage,
        "area": area,
        "term": term,
        "template_version": rules.version,
        "objective": objective,
        "original_sha256": lesson.text_sha256,
        "revised_sha256": checked.revised.text_sha256,
        "revised_text": checked.revised.text,
        "edits": _edits_view(stored["kept"], evidence),
        "dropped": dropped,
        "notices": check_result["notices"],
        "diff": diff,
    }, "ok"


# --- read -------------------------------------------------------------------------------------
@logged_tool("lesson_get_plan")
def get_lesson_plan(ctx: ToolContext, plan_id):
    """One plan, for its author only."""
    who = _signed_in(ctx)
    row = _load(ctx, plan_id, _school(ctx))
    _own(row, who)
    return {
        "status": "ok",
        "plan_id": row["plan_id"],
        "plan_status": row["status"],
        "stage": row["stage"],
        "area": row["area"],
        "term": row["term"],
        "template_version": row["template_version"],
        "objective": row["objective_text"],
        "original_text": row["original_text"],
        "revised_text": row["revised_text"],
        "final_text": row["final_text"],
        "edits": _edits_view(row["edits"]["kept"], row["evidence"]),
        "dropped": row["edits"]["dropped"],
        "accepted_edit_ids": row["accepted_edit_ids"],
        "notices": row["check_result"].get("notices", []),
        "diff": row["diff"],
        "author_name": row["author_name"],
        "approver_name": row["approver_name"],
        "approved_at": row["approved_at"].isoformat() if row["approved_at"] else None,
        "created_at": row["created_at"].isoformat(),
        "checks_passed": _checks_passed(row["check_result"]),
        "pins": row["pins"],
        "model": row["model"],
        "prompt_sha256": row["prompt_sha256"],
        "run_id": row["run_id"],
    }, "ok"


@logged_tool("lesson_list_plans")
def list_lesson_plans(ctx: ToolContext):
    """The signed-in author's own plans in this school, newest first. No argument names a person or
    a school. Each entry is what a list needs: no lesson text and no name."""
    who = _signed_in(ctx)
    school = _school(ctx)
    rows = ctx.conn.cursor(row_factory=dict_row).execute(
        "SELECT plan_id, status, stage, area, term, template_version, created_at, approved_at "
        "FROM lesson_plans WHERE author_id = %s AND school_code = %s "
        "ORDER BY created_at DESC, plan_id DESC LIMIT %s",
        (who.id, school, LIST_LIMIT),
    ).fetchall()
    return {
        "status": "ok",
        "plans": [
            {
                "plan_id": r["plan_id"], "plan_status": r["status"], "stage": r["stage"],
                "area": r["area"], "term": r["term"], "template_version": r["template_version"],
                "created_at": r["created_at"].isoformat(),
                "approved_at": r["approved_at"].isoformat() if r["approved_at"] else None,
            }
            for r in rows
        ],
    }, "ok"


# --- the tutor's decision ------------------------------------------------------------------------
def _accepted(value, kept_ids: list) -> list:
    if not isinstance(value, list) or not value:
        raise Refusal("at least one edit must be accepted")
    if not all(isinstance(i, str) for i in value):
        raise Refusal("accepted_edit_ids must be edit ids")
    if len(set(value)) != len(value):
        raise Refusal("an edit is listed twice")
    unknown = [i for i in value if i not in kept_ids]
    if unknown:
        raise Refusal(f"these are not edits of this plan: {', '.join(unknown)}")
    return value


@logged_tool("lesson_approve_plan")
def approve_lesson_plan(ctx: ToolContext, plan_id, accepted_edit_ids):
    """Approve a plan with the edits the tutor accepted. The approver is the signed-in user and must
    be the plan's author. The final text is the original with only the accepted edits applied,
    worked out here, never taken from a caller. The database refuses anything else."""
    who = _signed_in(ctx)
    row = _load(ctx, plan_id, _school(ctx), lock=True)
    _own(row, who)
    if row["status"] != "proposed":
        raise Refusal(f"the plan is {row['status']}, so it cannot be approved")
    kept = _kept_edits(row)
    accepted = _accepted(accepted_edit_ids, [e.id for e in kept])
    lesson = parse_lesson(row["original_text"], row["objective_text"])
    final = apply_edits(lesson, [e for e in kept if e.id in accepted])
    if final.objective != row["objective_text"]:
        raise Refusal("the objective would change, so the plan cannot be approved")
    with _database(ctx):
        ctx.conn.execute(
            "UPDATE lesson_plans SET status = 'approved', accepted_edit_ids = %s, final_text = %s, "
            "approver_id = %s, approver_name = %s, approved_at = now() WHERE plan_id = %s",
            (accepted, final.text, who.id, who.name, row["plan_id"]),
        )
    return {
        "status": "approved",
        "plan_id": row["plan_id"],
        "accepted_edit_ids": accepted,
        "left_out_edit_ids": [e.id for e in kept if e.id not in accepted],
        "final_sha256": final.text_sha256,
        "approver_name": who.name,
    }, "ok"


@logged_tool("lesson_reject_plan")
def reject_lesson_plan(ctx: ToolContext, plan_id):
    """The author turns a plan down. A rejected plan is closed."""
    who = _signed_in(ctx)
    row = _load(ctx, plan_id, _school(ctx), lock=True)
    _own(row, who)
    if row["status"] != "proposed":
        raise Refusal(f"the plan is {row['status']}, so it cannot be rejected")
    with _database(ctx):
        ctx.conn.execute(
            "UPDATE lesson_plans SET status = 'rejected' WHERE plan_id = %s", (row["plan_id"],)
        )
    return {"status": "rejected", "plan_id": row["plan_id"]}, "ok"
