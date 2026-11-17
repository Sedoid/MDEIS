"""The build tools, as the server runs them. Each is one step of the build graph, takes the context
(the connection and the run) and plain arguments, and writes one action-log row (`logged_tool`).

    build_plan            which sections are built, which are skipped (and why), which were removed
    build_settle_rules    which extracted rules are kept, and where each value lives
    build_check_function  every deterministic check on a proposed function, nothing stored
    build_store_version   re-runs every check, then writes the version, tests and patch
    build_record_review   records the automated review (pass or hold); a pass moves the version on
    build_retire_removed  retires the measures built from a section the new document dropped

The build is by the system. A tool here refuses when anyone is signed in, takes no argument that
names a person, and never approves, confirms or rejects a measure: only a registered teacher does
that, at the demo, through the registry. Nothing here runs generated code except through the
sandbox client, and the model never calls a function in this file: the orchestrator's graph does,
by name, over the MCP server, and the server lists these tools to no chat assistant (the chat
allow-list in the orchestrator does not name them).

`register` adds the tools to an MCP server only when MEASURE_BUILD_ENABLED is set, so a stack that
does not build exposes none of them.
"""

from __future__ import annotations

import os

from ..registry import transitions as t
from ..registry.store import (
    advance_status,
    create_version,
    record_review,
    record_tests,
    retire,
)
from ..sandbox import client
from ..sandbox.config import load_rules as load_sandbox_rules
from ..tools._call import logged_tool
from ..tools.context import Refusal, ToolContext
from . import engine, store
from .config import load_build_rules

try:  # the MCP library is on the server image; the tools themselves do not need it
    from mcp.server.fastmcp import Context
except ImportError:  # pragma: no cover
    Context = None

ENABLE_ENV = "MEASURE_BUILD_ENABLED"
REASON_BANNED = (
    "The automated review used a word the rulebook bans, so the version is held for a person."
)


def enabled() -> bool:
    return os.environ.get(ENABLE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _system_only(ctx: ToolContext) -> None:
    if ctx.approver is not None:
        raise Refusal("a build is a system step and cannot be run by a signed-in person")


def _text(value, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Refusal(f"{what} is needed")
    return value


def _inputs(ctx: ToolContext, document_id):
    return store.load_inputs(ctx.conn, document_id, load_build_rules())


@logged_tool("build_plan")
def build_plan(ctx: ToolContext, *, document_id, build_anyway=None):
    """Plan a build for the methodology in force: the sections to build, the sections a template
    clause already covers (skipped, with the clause and the fixed tool, and a 'build it anyway'
    choice that is recorded here), the sections that are not measures, and the sections the new
    document removed."""
    _system_only(ctx)
    if build_anyway is not None and not (
        isinstance(build_anyway, list) and all(isinstance(s, str) for s in build_anyway)
    ):
        raise Refusal("build_anyway is a list of section numbers")
    inputs = _inputs(ctx, document_id)
    return engine.plan(inputs, load_build_rules(), build_anyway or [], load_sandbox_rules()), "ok"


@logged_tool("build_settle_rules")
def build_settle_rules(ctx: ToolContext, *, document_id, section_id, rules):
    """Keep the rules whose quote is literally in the section (or a section it points at), and
    settle each value: read the template's own field when it has one, stop with setting_conflict on
    a different value, or propose a numbered measure_params clause when there is no home."""
    _system_only(ctx)
    _text(section_id, "a section number")
    if not isinstance(rules, list):
        raise Refusal("rules is a list")
    inputs = _inputs(ctx, document_id)
    settled = engine.settle_rules(
        inputs, load_build_rules(), section_id, rules, store.allocator(ctx.conn, inputs.content)
    )
    if settled["status"] == "refused":
        raise Refusal(settled["reason"])
    return settled, "ok" if settled["status"] == "ok" else "blocked"


def _checked(ctx: ToolContext, document_id, section_id, settled, proposal):
    _text(section_id, "a section number")
    inputs = _inputs(ctx, document_id)
    report = engine.check_function(
        inputs, load_build_rules(), load_sandbox_rules(), section_id, settled, proposal, client.run
    )
    return inputs, report


@logged_tool("build_check_function")
def build_check_function(ctx: ToolContext, *, document_id, section_id, settled, proposal):
    """Run every deterministic check on a proposed function and report what is wrong, in this
    order: the proposal and the settlement, the wording, the static check, the tests in the
    sandbox, the independent tests and the sensitivity check. Nothing is stored."""
    _system_only(ctx)
    _, report = _checked(ctx, document_id, section_id, settled, proposal)
    return report, "ok" if report["status"] == "ok" else "error"


@logged_tool("build_store_version")
def build_store_version(ctx: ToolContext, *, document_id, section_id, settled, proposal):
    """Write a function the checks passed to the registry as a version waiting for the automated
    review. The server runs every check again itself: it takes no one's word that they passed. The
    version, its tests and its template patch are written, and the tests' results are recorded."""
    _system_only(ctx)
    inputs, report = _checked(ctx, document_id, section_id, settled, proposal)
    if report["status"] != "ok":
        down = {"status": "error", "kind": report.get("kind"), "reason": "the sandbox is down"}
        return down, "error"
    if not report["passed"]:
        refused = {"status": "refused", "reason": "the checks did not pass",
                   "problems": report["problems"]}  # fmt: skip
        return refused, "refused"
    section = next(s for s in inputs.sections if s.id == section_id)
    made = create_version(
        ctx,
        measure_name=proposal["measure_name"],
        method_ref=section_id,
        description=proposal["description"],
        input_scope="learner",
        source_code=proposal["source"],
        output_kind=proposal["output_kind"],
        output_schema=report["output_schema"],
        methodology_document_id=document_id,
        section_sha256=section.sha256,
        clause_ids=report["clause_ids"],
        template_version=inputs.template_version,
        tests=report["tests"],
        template_patch=report["patch"],
        rules_quoted=settled["rules_quoted"],
        declared_params=True,
    )
    if made.get("status") != "ok":
        return made, "refused"
    measure_id, version = made["measure_id"], made["version"]
    if not made["created"]:
        again = {
            "status": "refused", "measure_id": measure_id, "version": version,
            "reason": f"this function was already built as version {version} of {measure_id}",
        }  # fmt: skip
        return again, "refused"

    def give_up(reason: str, detail=None):
        advance_status(ctx, measure_id=measure_id, version=version, to=t.REJECTED)
        return {"status": "refused", "measure_id": measure_id, "version": version,
                "reason": reason, "detail": detail}, "refused"  # fmt: skip

    patch_id = None
    if report["patch"]:
        hashes = store.section_hashes(inputs, section_id, settled["rules_quoted"])
        try:
            patch_id = store.store_patch(
                ctx.conn, measure_id=measure_id, version=version, inputs=inputs,
                body=report["patch"], section_hashes=hashes,
            )  # fmt: skip
        except Exception as error:  # the patch could not be stored: the version is not left behind
            ctx.conn.rollback()
            return give_up("the template patch could not be stored", type(error).__name__)
    for step in (
        lambda: advance_status(ctx, measure_id=measure_id, version=version, to=t.TESTING),
        lambda: record_tests(
            ctx, measure_id=measure_id, version=version, results=report["test_results"]
        ),
        lambda: advance_status(ctx, measure_id=measure_id, version=version, to=t.AWAITING_REVIEW),
    ):
        done = step()
        if done.get("status") not in ("ok",):
            return give_up("the registry refused a step", done.get("reason"))
    stored = {
        "status": "stored", "measure_id": measure_id, "version": version,
        "version_status": t.AWAITING_REVIEW, "patch_id": patch_id,
        "tests": len(report["tests"]), "clause_ids": report["clause_ids"],
    }  # fmt: skip
    return stored, "ok"


@logged_tool("build_record_review")
def build_record_review(ctx: ToolContext, *, measure_id, version, result, reasons=None):
    """Record the automated review of a version: `pass` or `hold`, with reasons. A pass lets the
    version go on to the demo; a hold keeps it where it is until a registered developer clears it.
    The review never approves anything and cannot clear a failed check: the version is only here
    because every check passed. Reasons that use a word the rulebook bans are replaced by a hold."""
    _system_only(ctx)
    if result not in t.REVIEW_RESULTS:
        raise Refusal("a review is pass or hold")
    listed = reasons if isinstance(reasons, list) else []
    banned = engine.banned_in(" ".join(str(r) for r in listed), _active_content(ctx))
    if banned:
        result, listed = t.HOLD, [REASON_BANNED]
    recorded = record_review(ctx, measure_id=measure_id, version=version, result=result,
                             reasons=listed)  # fmt: skip
    if recorded.get("status") != "ok":
        return recorded, "refused"
    status = t.AWAITING_REVIEW
    if result == t.PASS:
        moved = advance_status(ctx, measure_id=measure_id, version=version, to=t.AWAITING_DEMO)
        if moved.get("status") != "ok":
            return moved, "refused"
        status = t.AWAITING_DEMO
    done = {
        "status": "ok", "measure_id": measure_id, "version": version, "review_result": result,
        "reasons": listed, "version_status": status,
    }  # fmt: skip
    return done, "ok"


def _active_content(ctx: ToolContext) -> dict:
    row = ctx.conn.execute("SELECT content FROM templates WHERE active").fetchone()
    return row[0] if row else {}


@logged_tool("build_retire_removed")
def build_retire_removed(ctx: ToolContext, *, document_id, section_ids):
    """Retire the measure versions that were built from a section the new document removed. They
    are kept, never deleted, so a released report can still be reproduced."""
    _system_only(ctx)
    if not (isinstance(section_ids, list) and all(isinstance(s, str) for s in section_ids)):
        raise Refusal("section_ids is a list of section numbers")
    previous = store.previous_document(ctx.conn, document_id)
    found = []
    if previous and section_ids:
        found = ctx.conn.execute(
            "SELECT v.measure_id, v.version FROM measure_versions v "
            "JOIN measures m USING (measure_id) "
            "WHERE v.methodology_document_id = %s AND m.method_ref = ANY(%s) "
            "AND v.status NOT IN ('rejected', 'retired') ORDER BY v.measure_id, v.version",
            (previous, section_ids),
        ).fetchall()
    retired = []
    for measure_id, version in found:
        done = retire(ctx, measure_id=measure_id, version=version, reason="section_removed")
        if done.get("status") == "ok":
            retired.append({"measure_id": measure_id, "version": version})
    return {"status": "ok", "retired": retired}, "ok"


# The tools by the name the graph calls them. A test compares this with the orchestrator's list.
TOOLS = {
    "build_plan": build_plan,
    "build_settle_rules": build_settle_rules,
    "build_check_function": build_check_function,
    "build_store_version": build_store_version,
    "build_record_review": build_record_review,
    "build_retire_removed": build_retire_removed,
}


def register(mcp, run) -> list[str]:
    """Add the build tools to an MCP server, if MEASURE_BUILD_ENABLED is set. `run(request, tool,
    **arguments)` is the server's own way of opening a context from the request headers. Returns
    the names added (none when the switch is off)."""
    if not enabled():
        return []

    def about(tool) -> str:
        return " ".join((tool.__doc__ or "").split())

    @mcp.tool(name="build_plan", description=about(build_plan))
    def build_plan_tool(request: Context, document_id: str, build_anyway: list[str] | None = None):
        return run(request, build_plan, document_id=document_id, build_anyway=build_anyway)

    @mcp.tool(name="build_settle_rules", description=about(build_settle_rules))
    def build_settle_rules_tool(request: Context, document_id: str, section_id: str, rules: list):
        return run(request, build_settle_rules, document_id=document_id, section_id=section_id,
                   rules=rules)  # fmt: skip

    @mcp.tool(name="build_check_function", description=about(build_check_function))
    def build_check_function_tool(
        request: Context, document_id: str, section_id: str, settled: dict, proposal: dict
    ):
        return run(request, build_check_function, document_id=document_id, section_id=section_id,
                   settled=settled, proposal=proposal)  # fmt: skip

    @mcp.tool(name="build_store_version", description=about(build_store_version))
    def build_store_version_tool(
        request: Context, document_id: str, section_id: str, settled: dict, proposal: dict
    ):
        return run(request, build_store_version, document_id=document_id, section_id=section_id,
                   settled=settled, proposal=proposal)  # fmt: skip

    @mcp.tool(name="build_record_review", description=about(build_record_review))
    def build_record_review_tool(
        request: Context, measure_id: str, version: int, result: str,
        reasons: list[str] | None = None,
    ):
        return run(request, build_record_review, measure_id=measure_id, version=version,
                   result=result, reasons=reasons)  # fmt: skip

    @mcp.tool(name="build_retire_removed", description=about(build_retire_removed))
    def build_retire_removed_tool(request: Context, document_id: str, section_ids: list[str]):
        return run(request, build_retire_removed, document_id=document_id,
                   section_ids=section_ids)  # fmt: skip

    return sorted(TOOLS)
