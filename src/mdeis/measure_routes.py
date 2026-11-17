"""The teacher's human-only routes for a built measure (T-073).

Plain HTTP routes on the tool server, next to the lesson routes (`human_routes.py`). They are not
MCP tools, so no tool list offers them and no model can call them. Each route needs BOTH of these,
checked by the lesson routes' own gate (this file does not copy it):

  1. `X-Approver-Token`   the signed pass that names the signed-in person and their school.
  2. `X-Human-Route-Key`  the shared secret that only the gateway and this server hold.

The person and the school are read from the token. The body is closed: a field that is not listed is
refused, so no field can name a person, a school, a learner, a status or a decision.

    GET  /human/measures                              the versions waiting for a teacher
    GET  /human/measures/{measure_id}/{version}       one demo
    POST /human/measures/{measure_id}/{version}/try      {"stage", "entries"}
    POST /human/measures/{measure_id}/{version}/case     {"stage", "entries", "expected", "agreed"}
    POST /human/measures/{measure_id}/{version}/confirm  {"note"} (the note is optional)
    POST /human/measures/{measure_id}/{version}/reject   {"note"} (the note is needed)
    GET  /human/measures/held                         the versions the automated review holds
    POST /human/measures/{measure_id}/{version}/clear-hold  {"note"} (the note is needed)

The last two are the approver's: a hold of the automated review is cleared by a named signed-in
person with a note (T-077). Clearing removes the hold only: it approves nothing, and the version
goes on to the demo, where a teacher still decides. The automated review can only hold.

Each route calls one function of the demo's store, which reads the signed-in person from the context
made here, checks that the person is a registered teacher, and writes its own action-log row,
including when it refuses. A refusal here (a key or token problem, a body problem, a number that is
not one of ours) writes a row too. Nothing here calls a model or a network, and nothing here
decides: only the registry's own teacher decision can approve a version, and the routes only
carry it.
"""

import os
import re
import uuid

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import human_routes
from .db import diary
from .measure_demo import api as demo_api
from .measure_demo import compose
from .tools.context import Approver, ToolContext

KEY_HEADER = human_routes.KEY_HEADER
TOKEN_HEADER = human_routes.TOKEN_HEADER
REFUSED_TOOL = human_routes.REFUSED_TOOL
TRY_FIELDS = compose.TRY_FIELDS
CASE_FIELDS = compose.CASE_FIELDS
CONFIRM_FIELDS = ("note",)
REJECT_FIELDS = ("note",)
CLEAR_HOLD_FIELDS = ("note",)
MEASURE_ID = re.compile(r"^MS-[0-9]{4,}$")  # the form the database gives a measure number
VERSION = re.compile(r"^[1-9][0-9]*$")  # a version is a whole number from one

PATHS = {
    "list": "/human/measures",
    "open": "/human/measures/{measure_id}/{version}",
    "try": "/human/measures/{measure_id}/{version}/try",
    "case": "/human/measures/{measure_id}/{version}/case",
    "confirm": "/human/measures/{measure_id}/{version}/confirm",
    "reject": "/human/measures/{measure_id}/{version}/reject",
    "held": "/human/measures/held",
    "clear_hold": "/human/measures/{measure_id}/{version}/clear-hold",
}
STORE_FUNCTIONS = {
    "list": "list_awaiting", "open": "open_demo", "try": "try_measure", "case": "save_case",
    "confirm": "confirm", "reject": "reject", "held": "list_held", "clear_hold": "lift_hold",
}
GOOD_STATUS = {
    "list": "ok", "open": "ok", "try": "ok", "case": "saved", "confirm": "confirmed",
    "reject": "rejected", "held": "ok", "clear_hold": "cleared",
}
# What a refusal may carry besides its words: what the page needs to say what is missing or to show
# both answers. Nothing else of the store's answer is handed on with a refusal.
EXTRA_KEYS = ("gaps", "answer", "compare", "values_used")

BRACKET = re.compile(r"^\[([a-z_]+)\]\s*")
# A code in square brackets at the start of a refusal, and the typed reason and HTTP status for it.
CODES = {
    "not_signed_in": ("not_signed_in", 401),
    "no_school": ("no_school", 403),
    "not_a_teacher": ("not_a_teacher", 403),
    "not_a_developer": ("not_a_developer", 403),
    "not_held": ("not_held", 409),
    "not_found": ("measure_not_found", 404),
    "not_at_demo": ("not_at_demo", 409),
    "approval_conditions_not_met": ("approval_conditions_not_met", 409),
    "case_mismatch": ("case_mismatch", 409),
    "note_needed": ("note_needed", 422),
    "runner_unavailable": ("runner_unavailable", 503),
    "measure_failed": ("measure_failed", 502),
    **{code: (code, 422) for code in compose.REFUSAL_TEXT},
}
# The registry's own refusals, found by their words. The first match wins.
REFUSAL_TYPES = (
    ("no one is signed in", "not_signed_in", 401),
    ("no school is attached", "no_school", 403),
    ("not recorded as a teacher", "not_a_teacher", 403),
    ("not recorded as a developer", "not_a_developer", 403),
    ("no hold on this version", "not_held", 409),
    ("the hold was already cleared", "not_held", 409),
    ("there is no version", "measure_not_found", 404),
    ("a decision is taken at the demo", "not_at_demo", 409),
    ("the registry refused it", "approval_conditions_not_met", 409),
    ("a rejection needs a note", "note_needed", 422),
)
OTHER_REFUSAL = ("refused", 409)

PLAIN = {
    **human_routes.PLAIN,
    "measure_not_found": "There is no such measure version.",
    "note_needed": "A rejection needs a note saying why.",
    "unexpected_answer": "The demo gave an answer this route does not know.",
}


def type_refusal(reason) -> tuple:
    """The typed reason and the HTTP status for a refusal the demo or the registry gave in words."""
    text = reason if isinstance(reason, str) else ""
    found = BRACKET.match(text)
    if found:
        return CODES.get(found.group(1), OTHER_REFUSAL)
    for marker, typed, status in REFUSAL_TYPES:
        if marker in text:
            return typed, status
    return OTHER_REFUSAL


def route_name(path: str) -> str:
    """The route a concrete path belongs to, as written in PATHS (what the diary records)."""
    parts = path.split("?")[0].split("/")
    for template in PATHS.values():
        shape = template.split("/")
        if len(shape) == len(parts) and all(
            s == p or (s.startswith("{") and p) for s, p in zip(shape, parts, strict=True)
        ):
            return template
    return path


class _MeasureRoutes(human_routes._Routes):
    """The same gate as the lesson routes, and a flow of its own."""

    def __init__(self, store, connect, env, commit):
        super().__init__(store, connect, env, commit, None)

    def handle(self, name: str, key, token, measure_id, version, raw: bytes) -> tuple:
        run_id = f"RUN-{uuid.uuid4()}"
        url = self.env.get("DATABASE_URL")
        shown = measure_id if isinstance(measure_id, str) and MEASURE_ID.match(measure_id) else None
        number = version if isinstance(version, str) and VERSION.match(version) else None

        def refuse(status, reason, detail=None, who=None, outcome="refused") -> tuple:
            body = human_routes._refusal_body(reason, detail or PLAIN[reason])
            try:
                diary.write(
                    REFUSED_TOOL, run_id=run_id,
                    inputs={"route": PATHS[name], "measure_id": shown, "version": number},
                    outputs=body, outcome=outcome,
                    approver_id=who.id if who else None,
                    approver_name=who.name if who else None, url=url,
                )
            except diary.DiaryUnavailable:
                return 503, human_routes._refusal_body("log_unavailable", PLAIN["log_unavailable"])
            return status, body

        who, problem = self._gate(key, token)
        if problem:
            status, reason = problem
            return refuse(status, reason)
        if name not in ("list", "held") and (shown is None or number is None):
            return refuse(404, "measure_not_found", who=who)
        try:
            return self._serve(name, who, run_id, url, shown, number, raw, refuse)
        except Exception:  # (nothing from the failure is shown: it could hold a note)
            return refuse(500, "error", who=who, outcome="error")

    def _serve(self, name, who, run_id, url, measure_id, number, raw, refuse) -> tuple:
        arguments: dict = {}
        if name not in ("list", "held"):
            arguments = {"measure_id": measure_id, "version": int(number)}
        if name in ("try", "case", "confirm", "reject", "clear_hold"):
            fields = {"try": TRY_FIELDS, "case": CASE_FIELDS, "confirm": CONFIRM_FIELDS,
                      "reject": REJECT_FIELDS, "clear_hold": CLEAR_HOLD_FIELDS}[name]  # fmt: skip
            if name == "confirm" and not raw.strip():
                body, problem = {}, None  # a confirmation may carry no body at all
            else:
                body, problem = human_routes._closed_body(raw, fields)
            if problem:
                reason, status = problem
                detail = PLAIN[reason].format(allowed=", ".join(fields))
                return refuse(status, reason, detail, who=who)
            shaped = self._shape(name, body)
            if isinstance(shaped, tuple):
                status, reason = shaped
                return refuse(status, reason, who=who)
            arguments.update(shaped)
        call = getattr(self.store, STORE_FUNCTIONS[name])
        with self.connect() as conn:
            ctx = ToolContext(
                conn=conn, run_id=run_id, approver=Approver(id=who.id, name=who.name),
                database_url=url, commit=self.commit, school=who.school,
            )
            result = call(ctx, **arguments)
        return self._reply(name, result, who, refuse)

    @staticmethod
    def _shape(name: str, body: dict):
        """The arguments a body gives the store, or (status, reason) when it has the wrong shape."""
        bad = (400, "bad_body")
        if name in ("try", "case"):
            stage, entries = body.get("stage"), body.get("entries")
            if not isinstance(stage, str) or not isinstance(entries, list):
                return bad
            shaped = {"stage": stage, "entries": entries}
            if name == "case":
                expected, agreed = body.get("expected"), body.get("agreed", False)
                if not isinstance(expected, dict) or not isinstance(agreed, bool):
                    return bad
                shaped.update(expected=expected, agreed=agreed)
            return shaped
        note = body.get("note")
        if name == "confirm":
            return bad if note is not None and not isinstance(note, str) else {"note": note}
        if not isinstance(note, str) or not note.strip():
            return (422, "note_needed")  # a rejection and a cleared hold both need their note
        return {"note": note}

    def _reply(self, name, result, who, refuse) -> tuple:
        status = result.get("status") if isinstance(result, dict) else None
        if status == "refused":
            typed, http = type_refusal(result.get("reason"))
            words = BRACKET.sub("", str(result.get("reason") or "")) or PLAIN.get(typed, typed)
            body = human_routes._refusal_body(typed, words)
            body.update({k: result[k] for k in EXTRA_KEYS if k in result})
            return http, body  # the store already wrote its own diary row for this refusal
        if status == GOOD_STATUS[name]:
            return 200, result
        return refuse(502, "unexpected_answer", who=who, outcome="error")


def install(app, *, store=demo_api, connect=None, env=None, commit=True) -> None:
    """Add the eight routes to `app` (the FastMCP server, or anything with `custom_route`).

    `env` is read at each request, so the key and the secret are never copied. `connect`, `commit`
    and `store` are there so a test can put a fake in their place. Unlike the lesson routes these do
    not wait for the class view: a built measure is part of the school's own methodology.
    """
    routes = _MeasureRoutes(store, connect, os.environ if env is None else env, commit)

    def serve(name):
        async def endpoint(request: Request) -> JSONResponse:
            raw = await request.body() if request.method == "POST" else b""
            status, body = await run_in_threadpool(
                routes.handle, name, request.headers.get(KEY_HEADER),
                request.headers.get(TOKEN_HEADER), request.path_params.get("measure_id"),
                request.path_params.get("version"), raw,
            )
            return JSONResponse(body, status_code=status)

        return endpoint

    app.custom_route(PATHS["list"], methods=["GET"])(serve("list"))
    app.custom_route(PATHS["open"], methods=["GET"])(serve("open"))
    app.custom_route(PATHS["try"], methods=["POST"])(serve("try"))
    app.custom_route(PATHS["case"], methods=["POST"])(serve("case"))
    app.custom_route(PATHS["confirm"], methods=["POST"])(serve("confirm"))
    app.custom_route(PATHS["reject"], methods=["POST"])(serve("reject"))
    app.custom_route(PATHS["held"], methods=["GET"])(serve("held"))
    app.custom_route(PATHS["clear_hold"], methods=["POST"])(serve("clear_hold"))
