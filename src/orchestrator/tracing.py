"""Optional tracing to Langfuse: tokens, time, tool calls and the cost in USD, for people to watch.

The Postgres action log is the audit log and is not touched here. This is a window on a run, and it
is off unless LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_HOST are all set. Off means a
no-op: no client, no network, no change in any answer.

Langfuse Cloud shut its legacy ingestion API on 2026-11-16. The route now is OpenTelemetry over
HTTP with a JSON body (OTLP/JSON) to `{host}/api/public/otel/v1/traces`. There is no OpenTelemetry
or Langfuse package here: the JSON is built by hand and sent with httpx.

How it works, in four parts:
* A `Trace` is one run. Its model calls and tool calls are collected as child spans while the run
  goes on, under one root span (`mdeis-chat` or `mdeis-lesson`). When the run finishes, all its
  spans go to Langfuse in ONE request. `TracedModel` and `TracedServer` wrap the run's model and
  tool servers, so the loop and the graphs are not changed: they are handed the wrapped objects.
* The trace id is made from the run id (the uuid in `RUN-<uuid>`), and the full run id is also an
  attribute, so a trace lines up with the action log rows of that run.
* Metadata only by default: tokens, cost, time, model, provider, tool names, outcomes, counts.
  Never a prompt, a reply, a tool argument or a tool result. With LANGFUSE_CAPTURE_CONTENT on, the
  text of a chat's model calls is added (tool results are still withheld), for debugging with
  SYNTHETIC data. A lesson's notes and objective are never sent in any mode, and nor is a learner
  name that a tool returned.
* A second, separate switch, LANGFUSE_CAPTURE_TOOL_IO (also SYNTHETIC data only), adds each tool's
  arguments and result to its span, cut to TOOL_IO_MAX_CHARS. Names found in results, the approver
  token and the keys are masked. The lesson tools and the whole lesson graph are never captured, in
  any mode. A name a user typed into a question and that no result repeated is NOT masked.
* Fire and forget: a background task sends the queued runs with a short timeout. Any failure is
  swallowed and counted in `Tracer.stats`; a run is never slowed, changed or broken by the tracer.

The cost is what OpenRouter reported (`usage.cost`, in USD). It is never worked out here.

Span types (`langfuse.observation.type`): the root of a run is `agent`, a model call is
`generation`, a tool call is `tool`, and a call to a verification gate (GUARDRAIL_TOOLS) is
`guardrail`. The root also carries the run's totals (tokens, the cost the service reported, calls,
time) as metadata, so one question's cost is on one row. Usage and cost details stay on the model
calls only: putting them on the root as well would be counted twice in Langfuse's dashboards.

The same collector feeds the usage log (`usage.py`), which works with tracing off.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import secrets
import time
import weakref
from collections import deque

import httpx

from . import usage as usage_log
from .lessons import GUARD_TOOL, READ_TOOL, SAVE_TOOL
from .model import ModelError, usage_of

TRACES_PATH = "/api/public/otel/v1/traces"
INGESTION_VERSION = "4"  # makes the data appear in real time (header x-langfuse-ingestion-version)
SERVICE_NAME = "mdeis-orchestrator"
SCOPE_NAME = "mdeis"
SPAN_KIND_INTERNAL = 1  # OTLP span kind
STATUS_OK, STATUS_ERROR = 1, 2  # OTLP status codes
SEND_TIMEOUT_SECONDS = 3.0  # one run to Langfuse: give up quickly, the run does not wait for it
FLUSH_TIMEOUT_SECONDS = 5.0  # the most a shutdown flush waits in all
QUEUE_MAX = 100  # runs waiting to be sent; beyond this the oldest run is dropped (spans counted)
MASK_MIN_CHARS = 3  # a learner name, or a part of one, shorter than this is not masked
MAX_DEPTH = 6  # how deep a tool result is searched for names
MAX_ID_CHARS = 200
NS_PER_MS = 1_000_000
WITHHELD = "[withheld]"
TOOL_IO_MAX_CHARS = 4000  # a tool argument or result longer than this is cut (and marked)
TRUNCATED_SUFFIX = "...[truncated]"
# Tools whose arguments and results are never captured: they hold a tutor's notes and objective.
NEVER_TOOL_IO = frozenset({GUARD_TOOL, SAVE_TOOL, READ_TOOL})
_MISSING = object()  # "this tool call has no result" (it raised)
LEVELS = {"refused": "WARNING", "error": "ERROR"}  # a Langfuse level for a tool outcome
# Tools that are verification gates, shown as `guardrail` spans. Every other tool is a `tool`.
GUARDRAIL_TOOLS = frozenset({"check_citations"})
TYPE_ROOT, TYPE_MODEL, TYPE_TOOL, TYPE_GUARDRAIL = "agent", "generation", "tool", "guardrail"
REASONING_MAX_CHARS = 2000  # a model's reasoning text shown with content capture is cut to this
RUN_UUID = re.compile(r"^RUN-([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})$")

log = logging.getLogger("orchestrator.tracing")
OFF_SUMMARY = {
    "on": False, "runs_sent": 0, "runs_failed": 0, "runs_dropped": 0, "spans_rejected": 0,
    "last_status": None,
}
_LIVE: weakref.WeakSet = weakref.WeakSet()  # every tracer, so shutdown can flush them all
_CACHE: dict = {}


def _show_info_lines() -> None:
    """The service sets up no logging, so an INFO line would be lost. Only a real tracer (tracing
    on) calls this: it lets this one logger print its lines, and nothing else changes."""
    if not log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        log.addHandler(handler)
    log.setLevel(logging.INFO)


def trace_id_for(run_id: str) -> str:
    """32 lowercase hex characters: the run's uuid when the run id is `RUN-<uuid>`, otherwise the
    first 32 hex characters of the sha256 of the run id."""
    found = RUN_UUID.match(run_id)
    if found:
        return found.group(1).replace("-", "").lower()
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:32]


def new_span_id() -> str:
    return secrets.token_hex(8)  # 16 lowercase hex characters


def attr(key: str, value) -> dict:
    """One OTLP/JSON attribute. A 64-bit integer is a JSON string, as the encoding says."""
    if isinstance(value, bool):
        typed = {"boolValue": value}
    elif isinstance(value, int):
        typed = {"intValue": str(value)}
    elif isinstance(value, float):
        typed = {"doubleValue": value}
    elif isinstance(value, list | tuple):
        typed = {"arrayValue": {"values": [{"stringValue": str(v)} for v in value]}}
    else:
        typed = {"stringValue": str(value)}
    return {"key": key, "value": typed}


def metadata_attrs(prefix: str, values: dict) -> list:
    """`langfuse.observation.metadata.<key>` style attributes. A list is joined into one string."""
    return [
        attr(f"{prefix}.{key}", ", ".join(map(str, v)) if isinstance(v, list | tuple) else v)
        for key, v in values.items()
    ]


def user_id_from(token: str | None) -> str | None:
    """The signed-in user's id, read from the token's payload for tagging only. The orchestrator
    holds no signing secret, so this is not a check: the toolbox checks the token. The name in the
    token is never read."""
    try:
        payload = token.split(".")[0] if token and token.count(".") == 1 else ""
        body = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        uid = body["uid"]
    except Exception:  # (anything unreadable means no user tag)
        return None
    return uid if isinstance(uid, str) and 0 < len(uid) <= MAX_ID_CHARS else None


def outcome_of(result) -> str:
    status = result.get("status") if isinstance(result, dict) else None
    return status if status in ("refused", "error") else "ok"


def tool_names(definitions) -> list:
    """The names of the tools offered to the model, from the definitions it was given. Names only:
    never a description or a schema."""
    found = []
    for item in definitions or []:
        function = item.get("function") if isinstance(item, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if isinstance(name, str):
            found.append(name)
    return found


def conversation_of(conversation_id) -> str | None:
    return str(conversation_id)[:MAX_ID_CHARS] if conversation_id else None


def safe_usage_log(settings):
    """The usage log for these settings, or None if even that fails: it must not break a run."""
    try:
        return usage_log.log_for(settings)
    except Exception:
        return None


def chat_outcome(item: dict, current: str) -> str:
    """The outcome of a chat run, read from the events the loop yields."""
    if item["event"] == "error":
        return "error"
    if item["event"] != "final":
        return current
    data = item["data"]
    flags = (("refused", "refused"), ("incomplete", "incomplete"), ("needs_answer", "ask"))
    for flag, name in flags:
        if data.get(flag):
            return name
    return "ok"


class Trace:
    """One run, seen from outside. Every method is safe: a failure is counted, never raised."""

    def __init__(
        self, tracer: Tracer, run_id: str, kind: str, masks: list,
        user_id: str | None = None, session_id: str | None = None,
    ):
        self.tracer, self.run_id, self.kind = tracer, run_id, kind
        self.capture = tracer.settings.langfuse_capture_content
        self.tool_io = tracer.settings.langfuse_capture_tool_io
        self.masks = masks
        self.user_id, self.session_id = user_id, session_id
        self.trace_id, self.root_id = trace_id_for(run_id), new_span_id()
        self.started = self.begin()
        self.usage = usage_log.RunUsage(
            tracer.usage_log, run_id=run_id, kind=kind, conversation_id=session_id,
            user_id=user_id, model=tracer.settings.model,
        )
        self.children: list = []
        self.calls = 0
        self.done = False

    # -- the wrappers the app hands to the graphs
    def wrap_model(self, model):
        return TracedModel(model, self)

    def wrap_servers(self, servers: dict) -> dict:
        return {name: TracedServer(name, server, self) for name, server in servers.items()}

    # -- spans
    def _safe(self, build) -> None:
        try:
            build()
        except Exception:
            self.tracer.stats["errors"] += 1

    @staticmethod
    def begin() -> tuple:
        return time.time_ns(), time.monotonic_ns()

    @staticmethod
    def _times(started: tuple) -> tuple:
        wall_ns, mono_ns = started
        elapsed = time.monotonic_ns() - mono_ns
        return wall_ns, wall_ns + elapsed, elapsed // NS_PER_MS

    def _span(self, parent: str | None, name: str, times: tuple, attributes: list, error=False):
        span = {
            "traceId": self.trace_id,
            "spanId": new_span_id() if parent else self.root_id,
            "name": name,
            "kind": SPAN_KIND_INTERNAL,
            "startTimeUnixNano": str(times[0]),
            "endTimeUnixNano": str(times[1]),
            "attributes": attributes,
            "status": {"code": STATUS_ERROR if error else STATUS_OK},
        }
        if parent:
            span["parentSpanId"] = parent
        return span

    def _trace_attributes(self, outcome_meta: dict) -> list:
        """The trace-level attributes, put on EVERY span so that filters work."""
        found = [
            attr("langfuse.trace.name", f"mdeis-{self.kind}"),
            attr("langfuse.trace.tags", [self.kind]),
            attr("mdeis.run_id", self.run_id),
        ]
        if self.user_id:
            found.append(attr("user.id", self.user_id))
        if self.session_id:
            found.append(attr("session.id", self.session_id))
        meta = {"run_id": self.run_id, "graph_kind": self.kind, **outcome_meta}
        meta["model"] = self.tracer.settings.model
        return found + metadata_attrs("langfuse.trace.metadata", meta)

    def finish(self, outcome: str, *, question=None, answer=None, **numbers) -> None:
        """End the run: build the root span and send every span of the run in one request.
        `question` and `answer` are shown on the root only with content capture on, never in a
        lesson run."""

        def build():
            if self.done:
                return
            self.done = True
            times = self._times(self.started)
            meta = {"outcome": outcome}
            meta.update({k: v for k, v in numbers.items() if isinstance(v, int | str)})
            common = self._trace_attributes(meta)
            # The run's totals go on the root only, as metadata, and never as usage_details or
            # cost_details: those on a parent as well as its model calls would be counted twice.
            totals = {**self.usage.totals(), "latency_ms": times[2]}
            failed = outcome == "error"
            root = [
                attr("langfuse.observation.type", TYPE_ROOT),
                attr("langfuse.observation.level", "ERROR" if failed else "DEFAULT"),
                *metadata_attrs(
                    "langfuse.observation.metadata",
                    {"graph_kind": self.kind, "outcome": outcome, **totals},
                ),
                *self._root_io(question, answer),
            ]
            top = self._trace_attributes({**meta, **totals})
            spans = [self._span(None, f"mdeis-{self.kind}", times, top + root, failed)]
            spans += [{**c, "attributes": common + c["attributes"]} for c in self.children]
            self.children = []
            self.tracer.submit(spans)

        self._safe(build)
        self._safe(lambda: self.usage.finish(outcome, numbers.get("repair_rounds")))

    def _root_io(self, question, answer) -> list:
        """The question and the answer as the root's input and output, masked like any content.
        Only with content capture on, and never for a lesson (its notes must not leave)."""
        if not self.capture or self.kind == "lesson":
            return []
        found = []
        for key, value in (("input", question), ("output", answer)):
            if isinstance(value, str) and value:
                found.append(attr(f"langfuse.observation.{key}", self.clean(value)))
        return found

    def generation(self, started: tuple, messages: list, tools: list, reply, error=None) -> None:
        self._safe(lambda: self.usage.call(self._times(started)[2], reply, error))

        def build():
            self.calls += 1
            times = self._times(started)
            asked = [c.name for c in getattr(reply, "calls", ())]
            meta = {
                "graph_kind": self.kind, "call": self.calls, "latency_ms": times[2],
                "tools_offered": len(tools), "tool_calls_requested": len(asked),
                "tools_offered_names": json.dumps(tool_names(tools)),  # names only, no schemas
            }
            if asked:
                meta["tool_names_requested"] = asked
            provider = getattr(reply, "provider", None)
            if provider:
                meta["provider"] = provider
            model = self.tracer.settings.model
            attributes = [
                attr("langfuse.observation.type", TYPE_MODEL),
                attr("langfuse.observation.model.name", model),
                attr("gen_ai.request.model", model),
                attr("langfuse.observation.level", "ERROR" if error is not None else "DEFAULT"),
            ]
            usage = usage_of({"usage": getattr(reply, "usage", None)}) or {}  # numbers only
            counts = {
                label: usage[key]
                for label, key in (
                    ("input", "prompt_tokens"), ("output", "completion_tokens"),
                    ("total", "total_tokens"),
                )
                if key in usage
            }
            if counts:
                attributes.append(attr("langfuse.observation.usage_details", json.dumps(counts)))
            if "reasoning_tokens" in usage:  # metadata only: not a usage_details key
                meta["reasoning_tokens"] = usage["reasoning_tokens"]
            if "cost" in usage:  # USD, as the service reported it. Never worked out here.
                cost = json.dumps({"total": usage["cost"]})
                attributes.append(attr("langfuse.observation.cost_details", cost))
            if error is not None:
                # Only our own model-client errors have a message that is safe to send.
                said = str(error) if isinstance(error, ModelError) else type(error).__name__
                attributes.append(attr("langfuse.observation.status_message", said))
            if self.capture:
                shown_in = json.dumps(self._shown_input(messages))
                shown_out = json.dumps(self._shown_output(reply))
                attributes.append(attr("langfuse.observation.input", shown_in))
                attributes.append(attr("langfuse.observation.output", shown_out))
            attributes += metadata_attrs("langfuse.observation.metadata", meta)
            name = f"{self.kind}-model-call"
            span = self._span(self.root_id, name, times, attributes, error is not None)
            self.children.append(span)

        self._safe(build)

    def tool(
        self, started: tuple, server: str, name: str, outcome: str,
        arguments=_MISSING, result=_MISSING,
    ) -> None:
        self._safe(lambda: self.usage.tool(name, self._times(started)[2], outcome))

        def build():
            times = self._times(started)
            kind = TYPE_GUARDRAIL if name in GUARDRAIL_TOOLS else TYPE_TOOL
            attributes = [
                attr("langfuse.observation.type", kind),
                attr("langfuse.observation.level", LEVELS.get(outcome, "DEFAULT")),
                *self._tool_io_attributes(name, arguments, result),
                *metadata_attrs(
                    "langfuse.observation.metadata",
                    {
                        "graph_kind": self.kind, "server": server, "outcome": outcome,
                        "latency_ms": times[2],
                    },
                ),
            ]
            self.children.append(
                self._span(self.root_id, name, times, attributes, outcome == "error")
            )

        self._safe(build)

    # -- what may be shown, with content capture or tool capture on
    def _tool_io_attributes(self, name: str, arguments, result) -> list:
        """The tool's arguments and result as JSON strings, masked and cut. Nothing for the lesson
        tools or in a lesson run, and nothing when the setting is off."""
        if not self.tool_io or self.kind == "lesson" or name in NEVER_TOOL_IO:
            return []
        found, cut = [], False
        for key, value in (("input", arguments), ("output", result)):
            if value is _MISSING:
                continue
            try:
                text = json.dumps(value, default=str, ensure_ascii=False)
            except Exception:
                text = "[unserializable]"
            text = self.clean(text)
            if len(text) > TOOL_IO_MAX_CHARS:
                text, cut = text[:TOOL_IO_MAX_CHARS] + TRUNCATED_SUFFIX, True
            found.append(attr(f"langfuse.observation.{key}", text))
        if cut:
            found.append(attr("mdeis.truncated", True))
        return found

    def learn_names(self, result, depth: int = 0) -> None:
        """Remember any value under a key that ends in `name` (a learner's display name), so it can
        be masked wherever it is repeated: in a model's text, in a tool's result or argument."""
        if not (self.capture or self.tool_io) or depth > MAX_DEPTH:
            return
        try:
            if isinstance(result, dict):
                for key, value in result.items():
                    if isinstance(value, str) and str(key).lower().endswith("name"):
                        parts = [value, *value.split()]
                        self.masks.extend(p for p in parts if len(p) >= MASK_MIN_CHARS)
                    else:
                        self.learn_names(value, depth + 1)
            elif isinstance(result, list):
                for value in result:
                    self.learn_names(value, depth + 1)
        except Exception:
            self.tracer.stats["errors"] += 1

    def clean(self, text) -> str:
        text = str(text or "")
        settings = self.tracer.settings
        secrets_ = (settings.langfuse_public_key, settings.langfuse_secret_key)
        for mask in sorted({*self.masks, *secrets_}, key=len, reverse=True):
            if mask:
                text = re.sub(re.escape(mask), WITHHELD, text, flags=re.IGNORECASE)
        return text

    def _shown_input(self, messages: list) -> list:
        """The chat's own words and the system prompt. A tool result is never shown, and a lesson
        shows its fixed system prompt only (the notes and objective sit in the other messages)."""
        shown = []
        for message in messages:
            role = message.get("role")
            keep = role == "system" or (self.kind != "lesson" and role in ("user", "assistant"))
            content = self.clean(message.get("content")) if keep else WITHHELD
            entry = {"role": role, "content": content}
            if role == "assistant" and message.get("tool_calls"):
                entry["tool_calls"] = [c["function"]["name"] for c in message["tool_calls"]]
            shown.append(entry)
        return shown

    def _shown_output(self, reply) -> dict:
        text = getattr(reply, "text", "")
        shown = {"text": WITHHELD if self.kind == "lesson" else self.clean(text)}
        asked = [c.name for c in getattr(reply, "calls", ())]
        if asked:
            shown["tool_calls"] = asked
        reasoning = getattr(reply, "reasoning", None)
        if reasoning and self.kind != "lesson":  # a lesson's reasoning could quote the notes
            text = self.clean(reasoning)
            if len(text) > REASONING_MAX_CHARS:
                text = text[:REASONING_MAX_CHARS] + TRUNCATED_SUFFIX
            shown["reasoning"] = text
        return shown


class TracedModel:
    """The run's model, with a generation span for each call. The reply is passed on untouched."""

    def __init__(self, inner, trace: Trace):
        self.inner, self.trace = inner, trace

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def complete(self, messages: list, tools: list):
        started = self.trace.begin()
        try:
            reply = await self.inner.complete(messages, tools)
        except Exception as error:
            self.trace.generation(started, messages, tools, None, error=error)
            raise
        self.trace.generation(started, messages, tools, reply)
        return reply


class TracedServer:
    """A tool server, with a span for each call. The result is passed on untouched."""

    def __init__(self, name: str, inner, trace: Trace):
        self.name, self.inner, self.trace = name, inner, trace

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def list_tools(self):
        return await self.inner.list_tools()

    async def call(self, name: str, arguments: dict):
        started = self.trace.begin()
        try:
            result = await self.inner.call(name, arguments)
        except Exception:
            self.trace.tool(started, self.name, name, "error", arguments)
            raise
        self.trace.learn_names(result)  # first: this call's own names are masked in its span
        self.trace.tool(started, self.name, name, outcome_of(result), arguments, result)
        return result


class NullTrace:
    """Tracing is off. With no usage log the wrappers hand back what they were given and every
    method does nothing. With the usage log on, the same wrappers feed the run's `RunUsage`, and
    nothing else is done."""

    def __init__(self, usage=None):
        self.usage = usage

    def wrap_model(self, model):
        return model if self.usage is None else TracedModel(model, self)

    def wrap_servers(self, servers: dict) -> dict:
        if self.usage is None:
            return servers
        return {name: TracedServer(name, server, self) for name, server in servers.items()}

    def begin(self) -> tuple:
        return Trace.begin()

    def generation(self, started: tuple, messages: list, tools: list, reply, error=None) -> None:
        self._safely(lambda: self.usage.call(Trace._times(started)[2], reply, error))

    def tool(
        self, started: tuple, server: str, name: str, outcome: str,
        arguments=_MISSING, result=_MISSING,
    ) -> None:
        self._safely(lambda: self.usage.tool(name, Trace._times(started)[2], outcome))

    def learn_names(self, result, depth: int = 0) -> None:
        return None

    def finish(self, outcome: str, *, question=None, answer=None, **numbers) -> None:
        self._safely(lambda: self.usage.finish(outcome, numbers.get("repair_rounds")))

    def _safely(self, action) -> None:
        try:
            if self.usage is not None:
                action()
        except Exception:  # (the usage log must never touch a run)
            pass


class NullTracer:
    enabled = False

    def __init__(self, usage_log_=None):
        self.usage_log = usage_log_

    def summary(self) -> dict:
        return dict(OFF_SUMMARY)

    def start(
        self, *, run_id: str, kind: str, token: str | None = None,
        conversation_id: str | None = None, mask=(),
    ) -> NullTrace:
        found = self.usage_log
        if found is None or not found.on:
            return NullTrace()
        return NullTrace(usage_log.RunUsage(
            found, run_id=run_id, kind=kind, conversation_id=conversation_of(conversation_id),
            user_id=user_id_from(token), model=found.model,
        ))

    async def flush(self) -> None:
        return None


class Tracer:
    """The queue and the sender. The queue holds finished runs, each a list of spans. `client` is
    for tests; the real service makes its own for each run (so it never holds a connection across
    event loops)."""

    enabled = True

    def __init__(self, settings, client: httpx.AsyncClient | None = None):
        self.settings, self._client = settings, client
        self.usage_log = safe_usage_log(settings)
        self._queue: deque = deque()
        self._task: asyncio.Task | None = None
        # Counts are of spans, except `errors` (things that went wrong inside the tracer), the
        # `runs_*` counts (requests, one for each run) and `last_status` (an HTTP status or None).
        self.stats = {
            "queued": 0, "sent": 0, "rejected": 0, "failed": 0, "dropped": 0, "errors": 0,
            "runs_sent": 0, "runs_failed": 0, "runs_dropped": 0, "last_status": None,
        }
        _LIVE.add(self)
        _show_info_lines()

    def summary(self) -> dict:
        """What the health check may say about tracing: counts and a status, no host, no key."""
        stats = self.stats
        return {
            "on": True, "runs_sent": stats["runs_sent"], "runs_failed": stats["runs_failed"],
            "runs_dropped": stats["runs_dropped"], "spans_rejected": stats["rejected"],
            "last_status": stats["last_status"],
        }

    def start(
        self, *, run_id: str, kind: str, token: str | None = None,
        conversation_id: str | None = None, mask=(),
    ) -> Trace:
        """Begin a trace for a run. `kind` is chat, lesson or build. `mask` is text that must never
        be sent (a lesson's notes and objective). Nothing is sent until the run finishes."""
        session = conversation_of(conversation_id)
        # The approver token is masked too, so that a tool that repeats it cannot send it.
        hidden = [m for m in (*mask, token) if isinstance(m, str) and m]
        return Trace(self, run_id, kind, hidden, user_id=user_id_from(token), session_id=session)

    def submit(self, spans: list) -> None:
        """Queue a finished run for sending."""
        while len(self._queue) >= QUEUE_MAX:
            self.stats["dropped"] += len(self._queue.popleft())
            self.stats["runs_dropped"] += 1
        self._queue.append(spans)
        self.stats["queued"] += len(spans)
        try:
            if self._task is None or self._task.done():
                self._task = asyncio.get_running_loop().create_task(self._drain())
        except RuntimeError:  # no loop is running: the run waits for a flush
            pass

    async def _drain(self) -> None:
        while self._queue:
            await self._post(self._queue.popleft())

    async def _post(self, spans: list) -> None:
        """Send one run and log ONE line about it: counts and a status, never a message, a body, a
        key or any text of the run."""
        try:
            response = await self._send(spans)
        except Exception as error:  # (the class name only: a message could hold anything)
            self._lost(len(spans), type(error).__name__, None)
            return
        status = response.status_code
        if not 200 <= status < 300:
            self._lost(len(spans), str(status), status)
            return
        rejected = 0
        try:  # a partial success names how many spans were refused; the reason is not kept
            count = response.json().get("partialSuccess", {}).get("rejectedSpans", 0)
            rejected = min(int(count), len(spans))
        except Exception:
            pass
        self.stats["sent"] += len(spans) - rejected
        self.stats["rejected"] += rejected
        self.stats["runs_sent"] += 1
        self.stats["last_status"] = status
        log.log(
            logging.WARNING if rejected else logging.INFO,
            "langfuse: run sent, spans=%d, http=%d, rejected=%d", len(spans), status, rejected,
        )

    async def _send(self, spans: list) -> httpx.Response:
        settings = self.settings
        body = {
            "resourceSpans": [{
                "resource": {"attributes": [attr("service.name", SERVICE_NAME)]},
                "scopeSpans": [{"scope": {"name": SCOPE_NAME}, "spans": spans}],
            }]
        }

        async def post(client: httpx.AsyncClient) -> httpx.Response:
            return await client.post(
                settings.langfuse_host + TRACES_PATH,
                content=json.dumps(body, default=str),
                headers={
                    "Content-Type": "application/json",
                    "x-langfuse-ingestion-version": INGESTION_VERSION,
                },
                auth=(settings.langfuse_public_key, settings.langfuse_secret_key),
                timeout=SEND_TIMEOUT_SECONDS,
            )

        if self._client is not None:
            return await post(self._client)
        async with httpx.AsyncClient() as client:
            return await post(client)

    def _lost(self, count: int, why: str, status: int | None) -> None:
        self.stats["failed"] += count
        self.stats["runs_failed"] += 1
        self.stats["last_status"] = status
        log.warning("langfuse: run not sent, spans=%d, reason=%s", count, why)

    async def flush(self) -> None:
        """Send what is queued, once, and wait at most FLUSH_TIMEOUT_SECONDS for all of it."""
        try:
            await asyncio.wait_for(self._flush(), FLUSH_TIMEOUT_SECONDS)
        except Exception:  # (a timeout, or a task from another loop: counted, never raised)
            self.stats["errors"] += 1
            self.stats["dropped"] += sum(len(run) for run in self._queue)
            self.stats["runs_dropped"] += len(self._queue)
            self._queue.clear()

    async def _flush(self) -> None:
        task = self._task
        if task is not None and not task.done():
            await task
        await self._drain()


async def flush_all() -> None:
    """For the service's shutdown."""
    for tracer in list(_LIVE):
        await tracer.flush()


def tracer_for(settings):
    """The tracer for these settings: a real one only when all three variables are set."""
    if not settings.langfuse_on:
        return NullTracer(safe_usage_log(settings))
    key = (
        settings.langfuse_host, settings.langfuse_public_key, settings.langfuse_secret_key,
        settings.langfuse_capture_content, settings.langfuse_capture_tool_io, settings.model,
        settings.usage_log_file,
    )
    if key not in _CACHE:
        _CACHE[key] = Tracer(settings)
    return _CACHE[key]
