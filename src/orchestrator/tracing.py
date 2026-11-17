"""Optional tracing to Langfuse: tokens, time, tool calls and the cost in USD, for people to watch.

The Postgres action log is the audit log and is not touched here. This is a window on a run, and it
is off unless LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_HOST are all set. Off means a
no-op: no client, no network, no change in any answer.

How it works, in four parts:
* `Tracer` holds a bounded queue and sends batches to Langfuse's public ingestion route with httpx
  (basic auth, the public key as user and the secret key as password). No Langfuse package.
* `Trace` is one run: its id is the run id, so a trace lines up with the action log rows of that
  run. It wraps the run's model (`TracedModel`: one generation event per call) and its tool servers
  (`TracedServer`: one span event per call). The graphs and the loop are not changed: they are
  handed the wrapped objects.
* Metadata only by default: tokens, cost, time, model, provider, tool names, outcomes, counts.
  Never a prompt, a reply, a tool argument or a tool result. With LANGFUSE_CAPTURE_CONTENT on, the
  text of a chat's model calls is added (tool results are still withheld), for debugging with
  SYNTHETIC data. A lesson's notes and objective are never sent in any mode, and nor is a learner
  name that a tool returned.
* Fire and forget: sending runs in a background task with a short timeout. Any failure is swallowed
  and counted in `Tracer.stats`; a run is never slowed, changed or broken by the tracer.

The cost is what OpenRouter reported (`usage.cost`, in USD). It is never worked out here.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
import uuid
import weakref
from collections import deque
from datetime import UTC, datetime, timedelta

import httpx

from .model import ModelError, usage_of

INGEST_PATH = "/api/public/ingestion"
SEND_TIMEOUT_SECONDS = 3.0  # one batch to Langfuse: give up quickly, the run does not wait for it
FLUSH_TIMEOUT_SECONDS = 5.0  # the most a shutdown flush waits in all
QUEUE_MAX = 500  # events waiting to be sent; the oldest are dropped (and counted) beyond this
BATCH_MAX = 50  # events in one request
MASK_MIN_CHARS = 3  # a learner name, or a part of one, shorter than this is not masked
MAX_DEPTH = 6  # how deep a tool result is searched for names
MAX_ID_CHARS = 200
WITHHELD = "[withheld]"
LEVELS = {"refused": "WARNING", "error": "ERROR"}  # a Langfuse level for a tool outcome

log = logging.getLogger("orchestrator.tracing")
_LIVE: weakref.WeakSet = weakref.WeakSet()  # every tracer, so shutdown can flush them all
_CACHE: dict = {}


def _iso(moment: datetime) -> str:
    # Fixed width (milliseconds), so that two times compare correctly as text.
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


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

    def __init__(self, tracer: Tracer, run_id: str, kind: str, masks: list):
        self.tracer, self.run_id, self.kind = tracer, run_id, kind
        self.capture = tracer.settings.langfuse_capture_content
        self.masks = masks
        self.calls = 0

    # -- the wrappers the app hands to the graphs
    def wrap_model(self, model):
        return TracedModel(model, self)

    def wrap_servers(self, servers: dict) -> dict:
        return {name: TracedServer(name, server, self) for name, server in servers.items()}

    # -- events
    def _safe(self, build):
        try:
            build()
        except Exception:
            self.tracer.stats["errors"] += 1

    def begin(self) -> tuple:
        return datetime.now(UTC), time.monotonic()

    def _times(self, started: tuple) -> tuple:
        wall, mono = started
        elapsed = time.monotonic() - mono
        return _iso(wall), _iso(wall + timedelta(seconds=elapsed)), round(elapsed * 1000)

    def finish(self, outcome: str, **numbers) -> None:
        def build():
            meta = {"outcome": outcome}
            meta.update({k: v for k, v in numbers.items() if isinstance(v, int | str)})
            self.tracer.emit("trace-create", {"id": self.run_id, "metadata": meta})

        self._safe(build)

    def generation(self, started: tuple, messages: list, tools: list, reply, error=None) -> None:
        def build():
            self.calls += 1
            start, end, ms = self._times(started)
            asked = [c.name for c in getattr(reply, "calls", ())]
            meta = {
                "graph_kind": self.kind, "call": self.calls, "latency_ms": ms,
                "tools_offered": len(tools), "tool_calls_requested": len(asked),
            }
            body = {
                "id": str(uuid.uuid4()), "traceId": self.run_id, "name": f"{self.kind}-model-call",
                "startTime": start, "endTime": end, "model": self.tracer.settings.model,
                "metadata": meta,
            }
            if asked:
                meta["tool_names_requested"] = asked
            provider = getattr(reply, "provider", None)
            if provider:
                meta["provider"] = provider
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
                body["usageDetails"] = counts
            if "cost" in usage:  # USD, as the service reported it. Never worked out here.
                body["costDetails"] = {"total": usage["cost"]}
            if error is not None:
                body["level"] = "ERROR"
                # Only our own model-client errors have a message that is safe to send.
                body["statusMessage"] = str(error) if isinstance(error, ModelError) else (
                    type(error).__name__
                )
            if self.capture:
                body["input"] = self._shown_input(messages)
                body["output"] = self._shown_output(reply)
            self.tracer.emit("generation-create", body)

        self._safe(build)

    def tool(self, started: tuple, server: str, name: str, outcome: str) -> None:
        def build():
            start, end, ms = self._times(started)
            self.tracer.emit("span-create", {
                "id": str(uuid.uuid4()), "traceId": self.run_id, "name": name,
                "startTime": start, "endTime": end, "level": LEVELS.get(outcome, "DEFAULT"),
                "metadata": {
                    "graph_kind": self.kind, "server": server, "outcome": outcome, "latency_ms": ms,
                },
            })

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
        secrets = (settings.langfuse_public_key, settings.langfuse_secret_key)
        for mask in sorted({*self.masks, *secrets}, key=len, reverse=True):
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
    """The run's model, with a generation event for each call. The reply is passed on untouched."""

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
    """A tool server, with a span event for each call. The result is passed on untouched."""

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
    """The queue and the sender. `client` is for tests; the real service makes its own for each
    batch (so it never holds a connection across event loops)."""

    enabled = True

    def __init__(self, settings, client: httpx.AsyncClient | None = None):
        self.settings, self._client = settings, client
        self._queue: deque = deque()
        self._task: asyncio.Task | None = None
        self.stats = {"queued": 0, "sent": 0, "rejected": 0, "failed": 0, "dropped": 0, "errors": 0}
        _LIVE.add(self)

    def start(
        self, *, run_id: str, kind: str, token: str | None = None,
        conversation_id: str | None = None, mask=(),
    ) -> Trace:
        """Open a trace for a run. `kind` is chat, lesson or build. `mask` is text that must never
        be sent (a lesson's notes and objective)."""
        trace = Trace(self, run_id, kind, [m for m in mask if isinstance(m, str) and m])
        try:
            body = {
                "id": run_id, "name": f"mdeis-{kind}", "tags": [kind],
                "metadata": {"graph_kind": kind, "model": self.settings.model},
            }
            user = user_id_from(token)
            if user:
                body["userId"] = user
            if conversation_id:
                body["sessionId"] = str(conversation_id)[:MAX_ID_CHARS]
            self.emit("trace-create", body)
        except Exception:
            self.stats["errors"] += 1
        return trace

    def emit(self, type_: str, body: dict) -> None:
        if len(self._queue) >= QUEUE_MAX:
            self._queue.popleft()
            self.stats["dropped"] += 1
        self._queue.append(
            {"id": str(uuid.uuid4()), "type": type_, "timestamp": _iso(datetime.now(UTC)),
             "body": body}
        )
        self.stats["queued"] += 1
        try:
            if self._task is None or self._task.done():
                self._task = asyncio.get_running_loop().create_task(self._drain())
        except RuntimeError:  # no loop is running: the events wait for a flush
            pass

    async def _drain(self) -> None:
        while self._queue:
            batch = [self._queue.popleft() for _ in range(min(BATCH_MAX, len(self._queue)))]
            await self._post(batch)

    async def _post(self, batch: list) -> None:
        try:
            response = await self._send(batch)
        except Exception as error:  # (the class only: a message could hold anything)
            self._lost(len(batch), type(error).__name__)
            return
        if response.status_code not in (200, 201, 207):
            self._lost(len(batch), f"status {response.status_code}")
            return
        rejected = 0
        try:
            errors = response.json().get("errors")
            rejected = len(errors) if isinstance(errors, list) else 0
        except Exception:
            pass
        self.stats["sent"] += len(batch) - rejected
        if rejected:
            self.stats["rejected"] += rejected
            log.warning("langfuse: %d events were rejected", rejected)

    async def _send(self, batch: list) -> httpx.Response:
        settings = self.settings

        async def post(client: httpx.AsyncClient) -> httpx.Response:
            return await client.post(
                settings.langfuse_host + INGEST_PATH,
                content=json.dumps({"batch": batch}, default=str),
                headers={"Content-Type": "application/json"},
                auth=(settings.langfuse_public_key, settings.langfuse_secret_key),
                timeout=SEND_TIMEOUT_SECONDS,
            )

        if self._client is not None:
            return await post(self._client)
        async with httpx.AsyncClient() as client:
            return await post(client)

    def _lost(self, count: int, why: str) -> None:
        self.stats["failed"] += count
        log.warning("langfuse: %d events not delivered (%s)", count, why)

    async def flush(self) -> None:
        """Send what is queued, once, and wait at most FLUSH_TIMEOUT_SECONDS for all of it."""
        try:
            await asyncio.wait_for(self._flush(), FLUSH_TIMEOUT_SECONDS)
        except Exception:  # (a timeout, or a task from another loop: counted, never raised)
            self.stats["errors"] += 1
            self.stats["dropped"] += len(self._queue)
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

