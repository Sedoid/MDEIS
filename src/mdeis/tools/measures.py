"""list_measures and compute_measure: the run-time tools for approved generated measures (T-072).

A methodology can ask for a measure the fixed tools do not compute. A function is generated for it,
checked, tested and confirmed by a named teacher before it is stored as approved (the registry
tables of migration 008). These two tools only READ what was approved and RUN it. Neither one
approves, registers, writes or names a person: the signed-in person is in the context and is only
written to the action log. The model supplies a measure, a learner and a subject. It never
supplies a number.

Fixed versus generated. This file is fixed code. It reads the learner's records through the same
path as `compute_trend`, builds the values the function reads from the ACTIVE template (the params
channel: the function is never handed a number by the model), runs the stored source in the sandbox,
and checks what came back before any of it is returned. The generated function only computes.

What a measure answers. A number measure returns an object of exact numbers under plain field
names, and optionally the records it used. A yes-or-no is 1 or 0. The result gives each number to
two places with its exact form beside it. The records cited are the ones the function used when it
names them, and otherwise every record it was given (that learner's, for that subject).

A measure is offered and run only while it is IN FORCE: its version is approved, its measure is not
retired, every clause it declares is in the active template (a measure parameter also with the same
type and unit and confirmed by a teacher), and the tools can show its kind and its wording. When it
is not, the answer is a typed refusal, never a guess. "No measure applies" is a valid answer.

Pins. A draft keeps the whole result, with the measure version and the hash of the code (the
findings stored with the draft). A report is never computed again: a released report is exactly the
text and findings it was released with. A draft whose measure version has since been replaced or
withdrawn cannot be released (`stale_pin`, see `release_report`).
"""

from __future__ import annotations

from psycopg.rows import dict_row

from ..core import measures as core
from ..core.rules import load_rules
from ..sandbox import with_template
from ..sandbox.protocol import UNAVAILABLE
from ._call import logged_tool
from ._shared import active_methodology, rules_text, situation, sources_of, with_hash
from .context import Refusal, ToolContext
from .trend import blocking_reasons

TOOL = "compute_measure"
NO_TOOL = "no measure that is approved and in force has this identifier"

# One read of every approved measure whose measure is still active. Nothing here is written.
APPROVED = (
    "SELECT m.measure_id, m.name, m.method_ref, v.version, v.description, v.input_scope, "
    "v.output_kind, v.output_schema, v.clause_ids, v.template_version, v.template_patch, "
    "v.methodology_document_id, v.section_sha256, v.code_sha256, v.source_code "
    "FROM measures m JOIN measure_versions v ON v.measure_id = m.measure_id "
    "WHERE m.status = 'active' AND v.status = 'approved'"
)


def _approved(conn, measure_id=None) -> list[dict]:
    sql, args = APPROVED + " ORDER BY m.measure_id", ()
    if measure_id is not None:
        sql, args = APPROVED + " AND m.measure_id = %s", (measure_id,)
    return conn.cursor(row_factory=dict_row).execute(sql, args).fetchall()


def _template_content(conn, version: str) -> dict:
    row = conn.execute(
        "SELECT content FROM templates WHERE version_name = %s", (version,)
    ).fetchone()
    if row is None:
        raise Refusal(f"there is no rulebook version {version}")
    return row[0]


def _recorded(conn, row: dict, cache: dict) -> dict:
    """The (type, unit) of each measure parameter the version was built against. First the patches
    stored for this version (the clauses the build proposed), then the patch kept on the version
    row, then the template the version was checked against."""
    version = row["template_version"]
    if version not in cache:
        found = conn.execute(
            "SELECT content FROM templates WHERE version_name = %s", (version,)
        ).fetchone()
        cache[version] = found[0] if found else None
    patches = [
        clauses
        for (clauses,) in conn.execute(
            "SELECT clauses FROM template_patches WHERE measure_id = %s AND version = %s "
            "ORDER BY created_at, patch_id",
            (row["measure_id"], row["version"]),
        ).fetchall()
    ]
    return with_template.recorded_types(*patches, row["template_patch"], cache[version])


def _problem(conn, row: dict, content: dict, banned: list, cache: dict):
    """None when the measure is in force and can be shown, else (reason, detail)."""
    if row["output_kind"] not in core.SUPPORTED_KINDS:
        return "kind_not_supported", "this kind of answer cannot be shown yet"
    if core.banned_hits(f"{row['name']} {row['description']}", banned):
        return "withheld_wording", "its name or description holds wording the rulebook bans"
    return with_template.force_problem(content, row["clause_ids"], _recorded(conn, row, cache))


@logged_tool("list_measures")
def list_measures(ctx: ToolContext):
    """List the measures you may compute for the active rulebook.

    Each is an approved measure that is in force now: its name, what it works out, the section of
    the methodology it comes from, the rules it reads and what it needs (a subject, or none). A
    measure that is not approved, or that the rulebook no longer supports, is not listed. When the
    list is empty, no measure applies: say so, and use the other tools. You never compute a figure
    yourself: a number reaches an answer only from `compute_measure`.
    """
    rules = load_rules(ctx.conn, None)
    content = _template_content(ctx.conn, rules.version)
    cache: dict = {}
    listed = []
    for row in _approved(ctx.conn):
        if _problem(ctx.conn, row, content, rules.banned_words, cache) is not None:
            continue
        listed.append(
            {
                "measure_id": row["measure_id"],
                "version": row["version"],
                "name": row["name"],
                "description": row["description"],
                "input_scope": row["input_scope"],
                "output_kind": row["output_kind"],
                "clause_ids": list(row["clause_ids"]),
                "method_ref": row["method_ref"],
                "methodology_document_id": row["methodology_document_id"],
                "section_sha256": row["section_sha256"],
                "code_sha256": row["code_sha256"],
            }
        )
    result = {"status": "ok", "template_version": rules.version, "measures": listed}
    with_hash(result)
    return result, "ok"


def _refused(reason: str, detail: str, **more):
    return {"status": "refused", "reason": reason, "detail": detail, **more}, "refused"


@logged_tool("compute_measure")
def compute_measure(
    ctx: ToolContext, measure_id: str, learner_id: str, skill: str | None = None
):
    """Work out one approved measure for one learner, from the stored records and the rulebook.

    Take `measure_id` from `list_measures`. Give the subject as `skill` when the measure needs one.
    The function reads only that learner's scores and the school's values from the active rulebook:
    you never pass a number, and you never work one out. The result gives exact numbers, the records
    and rules they rest on, and the measure version, so a draft can cite it. When no measure
    applies, or one is not in force, you get a refusal with its reason: say so and stop. You never
    guess a figure. A subject with an unanswered blocking problem gets no number, only the reasons.
    """
    rows = _approved(ctx.conn, measure_id) if isinstance(measure_id, str) else []
    if not rows:
        return _refused("no_tool_applies", NO_TOOL)
    row = rows[0]
    rules = load_rules(ctx.conn, None)
    content = _template_content(ctx.conn, rules.version)
    cache: dict = {}
    problem = _problem(ctx.conn, row, content, rules.banned_words, cache)
    if problem is not None:
        return _refused(*problem)
    if row["input_scope"] == "learner_skill":
        if not skill:
            return _refused("skill_needed", "this measure needs a subject as `skill`")
        if skill not in rules.subjects:
            return _refused("unknown_skill", f"{skill!r} is not a subject in {rules.version}")
        watched = [skill]
    else:
        if skill:
            return _refused("skill_not_taken", "this measure takes no subject")
        watched = list(rules.subjects)
    found = situation(ctx, learner_id, None)
    base = {
        "measure_id": row["measure_id"],
        "measure_version": row["version"],
        "name": row["name"],
        "output_kind": row["output_kind"],
        "learner_id": learner_id,
        "skill": skill,
        "template_version": found.rules.version,
    }
    if any(s in found.blocked for s in watched):
        reasons = blocking_reasons(found, watched)
        return {**base, "status": "blocked", "reasons": reasons}, "blocked"
    records = core.runner_records(found.records, found.rules, subject=skill)
    try:
        ran = with_template.run(
            row["source_code"],
            row["code_sha256"],
            records,
            content,
            row["clause_ids"],
            output_kind=row["output_kind"],
            recorded=_recorded(ctx.conn, row, cache),
        )
    except with_template.ParamsRefusal as why:
        return _refused(why.kind, why.detail)
    except with_template.RunnerRefusal as why:
        if why.kind == UNAVAILABLE:
            return _refused("runner_unavailable", why.detail)
        return _refused("measure_failed", why.detail, kind=why.kind)
    sent = [r["record_id"] for r in records]
    try:
        if ran["output_kind"] != row["output_kind"]:
            raise core.OutputRefusal("kind_changed", "the answer is of another kind")
        checked = core.check_number_output(
            ran["output"], sent, row["output_schema"], found.rules.banned_words
        )
    except core.OutputRefusal as why:
        return _refused(
            "measure_failed", f"the answer was refused ({why.code})", kind="bad_output"
        )
    result = {
        **base,
        "method_ref": row["method_ref"],
        "measure_methodology_document_id": row["methodology_document_id"],
        "section_sha256": row["section_sha256"],
        "code_sha256": row["code_sha256"],
        "clause_ids": list(row["clause_ids"]),
        "values_used": ran["params"],
    }
    used = set(checked["record_ids"] if checked["record_ids"] is not None else sent)
    result["record_ids"] = [rid for rid in sent if rid in used]
    fields = {name: core.number_view(n) for name, n in sorted(checked["numbers"].items())}
    result["status"] = "ok"
    result["result"] = {"fields": fields}
    with_hash(result)  # the hash covers the inputs, the values used and the measure version
    result["methodology_document_id"] = active_methodology(ctx.conn)
    result["sources"] = sources_of(ctx.conn, found, result["record_ids"])
    result["rules"] = rules_text(ctx.conn, found.rules.version, result["clause_ids"])
    return result, "ok"


# --- what the draft, the release and the chart need ---------------------------------------------
def results_in_run(ctx: ToolContext, hashes: list) -> list[dict]:
    """The `compute_measure` results of this run with these hashes, exactly as logged, in the order
    asked. A hash no such result of this run has is refused: a number cannot come from anywhere
    else."""
    rows = ctx.conn.execute(
        "SELECT outputs FROM action_log WHERE run_id = %s AND outcome = 'ok' AND tool = %s "
        "ORDER BY event_id",
        (ctx.run_id, TOOL),
    ).fetchall()
    found = {o["result_hash"]: o for (o,) in rows if isinstance(o, dict) and o.get("result_hash")}
    missing = [h for h in hashes if h not in found]
    if missing:
        raise Refusal("not returned by a tool in this run: " + ", ".join(missing))
    return [found[h] for h in hashes]


def measures_in(findings) -> list[dict]:
    """The measure versions a list of findings used, each once, in order:
    `[{measure_id, version, code_sha256}]`. A finding that is not a measure result adds nothing."""
    used: dict = {}
    for finding in findings if isinstance(findings, list) else []:
        if isinstance(finding, dict) and "measure_id" in finding and "measure_version" in finding:
            key = (finding["measure_id"], finding["measure_version"])
            used.setdefault(
                key,
                {
                    "measure_id": key[0],
                    "version": key[1],
                    "code_sha256": finding.get("code_sha256"),
                },
            )
    return list(used.values())


def stale_measures(conn, findings) -> list[dict]:
    """The measure versions these findings used that are no longer the approved version: replaced
    by a newer one, retired or withdrawn. Empty when every one is still in force."""
    stale = []
    for used in measures_in(findings):
        row = conn.execute(
            "SELECT status FROM measure_versions WHERE measure_id = %s AND version = %s",
            (used["measure_id"], used["version"]),
        ).fetchone()
        if row is None or row[0] != "approved":
            stale.append(used)
    return stale
