"""The human-only routes: how a signed-in tutor approves or rejects a lesson plan.

These are plain HTTP routes on the tool server, next to `/healthz`. They are not MCP tools, so no
tool list ever offers them and no model can call them. Only the gateway can reach them, because
each route needs BOTH of these:

  1. `X-Approver-Token`   the signed pass that names the signed-in person and their school
                          (`mdeis.core.approver_token`). The person and the school are read from it.
  2. `X-Human-Route-Key`  a shared secret, `HUMAN_ROUTE_KEY`, held by the gateway and this server
                          and by nothing else. The orchestrator, the model and the portal never
                          see it.

With the key unset every route refuses. The routes read nothing about a person or a school from the
body, the query or any other header; the body is closed, and a field it does not list is refused.

    GET  /human/lessons/options              the form's lists, from the active rulebook
    GET  /human/lessons                      the signed-in person's own plans, newest first
    GET  /human/lessons/{plan_id}            one plan (its author only)
    POST /human/lessons/{plan_id}/approve    {"accepted_edit_ids": [...]} and nothing else
    POST /human/lessons/{plan_id}/reject     {} and nothing else

Approve and reject are the store's own functions. They read the approver from the context made here
from the token, refuse anyone who is not the plan's author, and write their own diary row, including
on a refusal. A refusal here (a key or token problem, a body problem) writes a row too. Approving a
plan twice is REFUSED, not repeated: the store answers that the plan is no longer open.

Nothing here calls a model or a network, and nothing here names a person or a school.
"""

import hmac
import json
import os
import re
import uuid
from contextlib import contextmanager

import psycopg
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from .core import approver_token
from .core.rules import RulebookProblem
from .db import diary
from .lessons import store as lesson_store
from .lessons.rules import load_lesson_rules
from .tools.context import Approver, ToolContext

KEY_ENV = "HUMAN_ROUTE_KEY"
KEY_HEADER = "x-human-route-key"
TOKEN_HEADER = "x-approver-token"
REFUSED_TOOL = "human_route_refused"
OPTIONS_TOOL = "lesson_options"
APPROVE_FIELDS = ("accepted_edit_ids",)
REJECT_FIELDS = ()
MAX_BODY_BYTES = 8192  # a transport guard: an approval holds a few edit ids
PLAN_ID = re.compile(r"^LP-[0-9]{6,}$")  # the form the database gives a plan number

PATHS = {
    "options": "/human/lessons/options",
    "list": "/human/lessons",
    "get": "/human/lessons/{plan_id}",
    "approve": "/human/lessons/{plan_id}/approve",
    "reject": "/human/lessons/{plan_id}/reject",
}

# What the store says when it refuses, found by its own words, and the typed reason for it. The
# first match wins. The integration test drives the real store and checks each still matches.
REFUSAL_TYPES = (
    ("no one is signed in", "not_signed_in", 401),
    ("no school is attached", "no_school", 403),
    ("there is no lesson plan", "plan_not_found", 404),
    ("belongs to another author", "not_the_author", 403),
    ("so it cannot be approved", "plan_not_open", 409),
    ("so it cannot be rejected", "plan_not_open", 409),
    ("the objective would change", "approval_conditions_not_met", 409),
    ("the database refused it: approval needs", "approval_conditions_not_met", 409),
    ("at least one edit must be accepted", "accepted_edits_invalid", 422),
    ("are not edits of this plan", "accepted_edits_invalid", 422),
    ("an edit is listed twice", "accepted_edits_invalid", 422),
    ("must be edit ids", "accepted_edits_invalid", 422),
)
OTHER_REFUSAL = ("refused", 409)
STORE_FUNCTIONS = {
    "approve": "approve_lesson_plan", "reject": "reject_lesson_plan", "get": "get_lesson_plan",
    "list": "list_lesson_plans",
}

PLAIN = {
    "routes_off": "The human routes are switched off on this server.",
    "no_key": "This route needs the gateway's key.",
    "wrong_key": "This route needs the gateway's key.",
    "no_token": "No one is signed in.",
    "bad_token": "The sign-in is not valid.",
    "expired_token": "The sign-in has expired. Sign in again.",
    "no_school": "This sign-in carries no school.",
    "bad_body": "The body must be one small JSON object.",
    "unknown_field": "The body may hold only these fields: {allowed}.",
    "accepted_edits_invalid": "accepted_edit_ids must be a list of edit ids (text).",
    "plan_not_found": "There is no such plan.",
    "list_unavailable": "The plan list is not available yet.",
    "unexpected_answer": "The lesson store gave an answer this route does not know.",
    "log_unavailable": "The action log could not be written, so nothing was done.",
    "error": "The server could not do this. Nothing was changed.",
}


def type_refusal(reason) -> tuple:
    """The typed reason and the HTTP status for a refusal the store gave in words."""
    text = reason if isinstance(reason, str) else ""
    for marker, typed, status in REFUSAL_TYPES:
        if marker in text:
            return typed, status
    return OTHER_REFUSAL


def lesson_options(rules, school) -> dict:
    """The form's lists, from the rulebook: stages in the rulebook's order, and for each the areas
    it has, the foundation areas first (in the order the rulebook lists them), then the others in
    the rulebook's order. Terms are 1 to the rulebook's terms per year."""
    areas_of: dict = {}
    for stage, area in rules.range_clause:
        areas_of.setdefault(stage, []).append(area)
    foundation = list(rules.foundation_areas)
    stages = []
    for stage, areas in areas_of.items():
        first = [a for a in foundation if a in areas]
        rest = [a for a in areas if a not in foundation]
        stages.append({
            "stage": stage,
            "areas": [{"area": a, "foundation": a in foundation} for a in first + rest],
        })
    return {
        "status": "ok",
        "school": school,
        "template_version": rules.version,
        "terms": list(range(1, rules.terms_per_year + 1)),
        "max_notes_chars": rules.max_notes_chars,
        "stages": stages,
    }


@contextmanager
def _database(env):
    with psycopg.connect(env["DATABASE_URL"]) as conn:
        yield conn


def _refusal_body(reason: str, detail: str) -> dict:
    return {"status": "refused", "reason": reason, "detail": detail}


class _Routes:
    def __init__(self, store, connect, env, commit, load_rules):
        self.store, self.env, self.commit, self.load_rules = store, env, commit, load_rules
        self.connect = connect or (lambda: _database(self.env))

    # -- one request, start to finish. It runs in a worker thread (psycopg is synchronous here). --
    def handle(self, name: str, key, token, plan_id, raw: bytes) -> tuple:
        run_id = f"RUN-{uuid.uuid4()}"
        url = self.env.get("DATABASE_URL")
        shown_plan = plan_id if isinstance(plan_id, str) and PLAN_ID.match(plan_id) else None

        def refuse(status, reason, detail=None, who=None, outcome="refused") -> tuple:
            body = _refusal_body(reason, detail or PLAIN[reason])
            try:
                diary.write(
                    REFUSED_TOOL, run_id=run_id,
                    inputs={"route": PATHS[name], "plan_id": shown_plan}, outputs=body,
                    outcome=outcome, approver_id=who.id if who else None,
                    approver_name=who.name if who else None, url=url,
                )
            except diary.DiaryUnavailable:
                return 503, _refusal_body("log_unavailable", PLAIN["log_unavailable"])
            return status, body

        who, problem = self._gate(key, token)
        if problem:
            status, reason = problem
            return refuse(status, reason)
        if plan_id is not None and shown_plan is None:
            return refuse(404, "plan_not_found", who=who)
        try:
            return self._serve(name, who, run_id, url, shown_plan, raw, refuse)
        except Exception:  # (nothing from the failure is shown: it could hold a note)
            return refuse(500, "error", who=who, outcome="error")

    # -- the two things every route needs ---------------------------------------------------------
    def _gate(self, key, token) -> tuple:
        """(the person, None) or (None, (status, typed reason))."""
        expected = (self.env.get(KEY_ENV) or "").strip()
        if not expected:
            return None, (503, "routes_off")
        if not key:
            return None, (401, "no_key")
        if not hmac.compare_digest(key.encode("utf-8"), expected.encode("utf-8")):
            return None, (401, "wrong_key")
        if not token:
            return None, (401, "no_token")
        try:
            who = approver_token.verify(self.env.get("APPROVER_TOKEN_SECRET", ""), token)
        except approver_token.TokenError as problem:
            return None, (401, "expired_token" if str(problem) == "expired" else "bad_token")
        if not who.school:
            return None, (403, "no_school")
        return who, None

    # -- what each route does ---------------------------------------------------------------------
    def _serve(self, name, who, run_id, url, plan_id, raw, refuse) -> tuple:
        arguments = {}
        if name in ("approve", "reject"):
            allowed = APPROVE_FIELDS if name == "approve" else REJECT_FIELDS
            body, problem = _closed_body(raw, allowed)
            if problem:
                reason, status = problem
                detail = PLAIN[reason].format(allowed=", ".join(allowed) or "none")
                return refuse(status, reason, detail, who=who)
            if name == "approve":
                ids = body.get("accepted_edit_ids")
                if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                    return refuse(422, "accepted_edits_invalid", who=who)
                arguments = {"plan_id": plan_id, "accepted_edit_ids": ids}
            else:
                arguments = {"plan_id": plan_id}
        elif name == "get":
            arguments = {"plan_id": plan_id}

        call = None
        if name != "options":
            call = getattr(self.store, STORE_FUNCTIONS[name], None)
            if call is None:  # only the list can be missing: the store has no list function yet
                return refuse(501, "list_unavailable", who=who)
        with self.connect() as conn:
            ctx = ToolContext(
                conn=conn, run_id=run_id, approver=Approver(id=who.id, name=who.name),
                database_url=url, commit=self.commit, school=who.school,
            )
            if name == "options":
                return self._options(ctx, who, refuse)
            result = call(ctx, **arguments)
        return self._answer(name, result, who, refuse)

    def _options(self, ctx, who, refuse) -> tuple:
        try:
            rules = self.load_rules(ctx.conn)
        except RulebookProblem as problem:
            return refuse(409, "rulebook_unavailable", str(problem), who=who)
        body = lesson_options(rules, who.school)
        try:
            diary.write(
                OPTIONS_TOOL, run_id=ctx.run_id, inputs={}, outputs={
                    "status": "ok", "template_version": rules.version,
                }, outcome="ok", approver_id=who.id, approver_name=who.name,
                url=ctx.database_url,
            )
        except diary.DiaryUnavailable:
            return 503, _refusal_body("log_unavailable", PLAIN["log_unavailable"])
        return 200, body

    def _answer(self, name, result, who, refuse) -> tuple:
        status = result.get("status") if isinstance(result, dict) else None
        if status == "refused":
            typed, http = type_refusal(result.get("reason"))
            # The store already wrote its own diary row for this refusal.
            return http, _refusal_body(typed, str(result.get("reason") or typed))
        if name == "approve" and status == "approved":
            return 200, result
        if name == "reject" and status == "rejected":
            return 200, result
        if name == "get" and status == "ok":
            return 200, {"status": "ok", "plan": result}
        if name == "list" and status == "ok" and isinstance(result.get("plans"), list):
            return 200, {"status": "ok", "plans": result["plans"]}
        return refuse(502, "unexpected_answer", who=who, outcome="error")


def _closed_body(raw: bytes, allowed: tuple) -> tuple:
    """(the body, None) or (None, (typed reason, status)). The body is one small JSON object that
    holds only the fields listed. An empty body counts as `{}` only for a route that lists none."""
    if len(raw) > MAX_BODY_BYTES:
        return None, ("bad_body", 400)
    if not raw.strip():
        return ({}, None) if not allowed else (None, ("bad_body", 400))
    try:
        body = json.loads(raw)
    except ValueError:
        return None, ("bad_body", 400)
    if not isinstance(body, dict):
        return None, ("bad_body", 400)
    if set(body) - set(allowed):
        return None, ("unknown_field", 422)
    return body, None


def install(app, *, store=lesson_store, connect=None, env=None, commit=True,
            load_rules=load_lesson_rules) -> None:
    """Add the five routes to `app` (the FastMCP server, or anything with `custom_route`).

    `env` is read at each request, so the key and the secret are never copied. `connect`, `commit`,
    `store` and `load_rules` are there so a test can put a fake in their place.
    """
    routes = _Routes(store, connect, os.environ if env is None else env, commit, load_rules)

    def serve(name):
        async def endpoint(request: Request) -> JSONResponse:
            raw = await request.body() if request.method == "POST" else b""
            status, body = await run_in_threadpool(
                routes.handle, name, request.headers.get(KEY_HEADER),
                request.headers.get(TOKEN_HEADER), request.path_params.get("plan_id"), raw,
            )
            return JSONResponse(body, status_code=status)

        return endpoint

    app.custom_route(PATHS["options"], methods=["GET"])(serve("options"))
    app.custom_route(PATHS["list"], methods=["GET"])(serve("list"))
    app.custom_route(PATHS["get"], methods=["GET"])(serve("get"))
    app.custom_route(PATHS["approve"], methods=["POST"])(serve("approve"))
    app.custom_route(PATHS["reject"], methods=["POST"])(serve("reject"))
