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
* Fire and forget: a background task sends the queued runs with a short timeout. Any failure is
  swallowed and counted in `Tracer.stats`; a run is never slowed, changed or broken by the tracer.

The cost is what OpenRouter reported (`usage.cost`, in USD). It is never worked out here.
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
LEVELS = {"refused": "WARNING", "error": "ERROR"}  # a Langfuse level for a tool outcome
RUN_UUID = re.compile(r"^RUN-([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})$")

log = logging.getLogger("orchestrator.tracing")
_LIVE: weakref.WeakSet = weakref.WeakSet()  # every tracer, so shutdown can flush them all
_CACHE: dict = {}


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
        self.masks = masks
        self.user_id, self.session_id = user_id, session_id
        self.trace_id, self.root_id = trace_id_for(run_id), new_span_id()
        self.started = self.begin()
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

    def finish(self, outcome: str, **numbers) -> None:
        """End the run: build the root span and send every span of the run in one request."""

        def build():
            if self.done:
                return
            self.done = True
            times = self._times(self.started)
            meta = {"outcome": outcome}
            meta.update({k: v for k, v in numbers.items() if isinstance(v, int | str)})
            common = self._trace_attributes(meta)
            failed = outcome == "error"
            root = [
                attr("langfuse.observation.type", "span"),
                attr("langfuse.observation.level", "ERROR" if failed else "DEFAULT"),
                *metadata_attrs(
                    "langfuse.observation.metadata",
                    {"graph_kind": self.kind, "outcome": outcome, "latency_ms": times[2]},
                ),
            ]
            spans = [self._span(None, f"mdeis-{self.kind}", times, common + root, failed)]
            spans += [{**c, "attributes": common + c["attributes"]} for c in self.children]
            self.children = []
            self.tracer.submit(spans)

        self._safe(build)

    def generation(self, started: tuple, messages: list, tools: list, reply, error=None) -> None:
        def build():
            self.calls += 1
            times = self._times(started)
            asked = [c.name for c in getattr(reply, "calls", ())]
            meta = {
                "graph_kind": self.kind, "call": self.calls, "latency_ms": times[2],
                "tools_offered": len(tools), "tool_calls_requested": len(asked),
            }
            if asked:
                meta["tool_names_requested"] = asked
            provider = getattr(reply, "provider", None)
            if provider:
                meta["provider"] = provider
            model = self.tracer.settings.model
            attributes = [
                attr("langfuse.observation.type", "generation"),
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

    def tool(self, started: tuple, server: str, name: str, outcome: str) -> None:
        def build():
            times = self._times(started)
            attributes = [
                attr("langfuse.observation.type", "span"),
                attr("langfuse.observation.level", LEVELS.get(outcome, "DEFAULT")),
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

    # -- what may be shown, with content capture on
    def learn_names(self, result, depth: int = 0) -> None:
        """Remember any value under a key that ends in `name` (a learner's display name), so it can
        be masked wherever it is repeated."""
        if not self.capture or depth > MAX_DEPTH:
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
            self.trace.tool(started, self.name, name, "error")
            raise
        self.trace.tool(started, self.name, name, outcome_of(result))
        self.trace.learn_names(result)
        return result


class NullTrace:
    """Tracing is off: every method does nothing and the wrappers hand back what they were given."""

    def wrap_model(self, model):
        return model

    def wrap_servers(self, servers: dict) -> dict:
        return servers

    def finish(self, outcome: str, **numbers) -> None:
        return None


class NullTracer:
    enabled = False

    def start(self, **kwargs) -> NullTrace:
        return NullTrace()

    async def flush(self) -> None:
        return None


class Tracer:
    """The queue and the sender. The queue holds finished runs, each a list of spans. `client` is
    for tests; the real service makes its own for each run (so it never holds a connection across
    event loops)."""

    enabled = True

    def __init__(self, settings, client: httpx.AsyncClient | None = None):
        self.settings, self._client = settings, client
        self._queue: deque = deque()
        self._task: asyncio.Task | None = None
        # Counts are of spans, except `errors` (things that went wrong inside the tracer).
        self.stats = {"queued": 0, "sent": 0, "rejected": 0, "failed": 0, "dropped": 0, "errors": 0}
        _LIVE.add(self)

    def start(
        self, *, run_id: str, kind: str, token: str | None = None,
        conversation_id: str | None = None, mask=(),
    ) -> Trace:
        """Begin a trace for a run. `kind` is chat, lesson or build. `mask` is text that must never
        be sent (a lesson's notes and objective). Nothing is sent until the run finishes."""
        session = str(conversation_id)[:MAX_ID_CHARS] if conversation_id else None
        return Trace(
            self, run_id, kind, [m for m in mask if isinstance(m, str) and m],
            user_id=user_id_from(token), session_id=session,
        )

    def submit(self, spans: list) -> None:
        """Queue a finished run for sending."""
        while len(self._queue) >= QUEUE_MAX:
            self.stats["dropped"] += len(self._queue.popleft())
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
        try:
            response = await self._send(spans)
        except Exception as error:  # (the class only: a message could hold anything)
            self._lost(len(spans), type(error).__name__)
            return
        if not 200 <= response.status_code < 300:
            self._lost(len(spans), f"status {response.status_code}")
            return
        rejected = 0
        try:  # a partial success names how many spans were refused; the reason is not kept
            count = response.json().get("partialSuccess", {}).get("rejectedSpans", 0)
            rejected = min(int(count), len(spans))
        except Exception:
            pass
        self.stats["sent"] += len(spans) - rejected
        if rejected:
            self.stats["rejected"] += rejected
            log.warning("langfuse: %d spans were rejected", rejected)

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

    def _lost(self, count: int, why: str) -> None:
        self.stats["failed"] += count
        log.warning("langfuse: %d spans not delivered (%s)", count, why)

    async def flush(self) -> None:
        """Send what is queued, once, and wait at most FLUSH_TIMEOUT_SECONDS for all of it."""
        try:
            await asyncio.wait_for(self._flush(), FLUSH_TIMEOUT_SECONDS)
        except Exception:  # (a timeout, or a task from another loop: counted, never raised)
            self.stats["errors"] += 1
            self.stats["dropped"] += sum(len(run) for run in self._queue)
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
        return NullTracer()
    key = (
        settings.langfuse_host, settings.langfuse_public_key, settings.langfuse_secret_key,
        settings.langfuse_capture_content, settings.model,
    )
    if key not in _CACHE:
        _CACHE[key] = Tracer(settings)
    return _CACHE[key]
