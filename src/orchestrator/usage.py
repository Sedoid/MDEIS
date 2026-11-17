"""The usage log: one JSON line for each finished run, kept apart from any tracer.

It answers "what did one question cost?" from our own file, with tracing on or off. When
USAGE_LOG_FILE names a file, each chat or lesson run appends one line (schema v1, see
`RunUsage.line`) when it ends. Empty means off: nothing is collected, nothing is written.

What a line holds: ids, the model's name, counts, times, tool names and outcomes. Never a prompt, a
reply, a tool argument or result, a reasoning text, a learner name, a lesson note, a key or a
token. The user is the id only.

Where the numbers come from: tokens and the USD cost are what the model service reported for each
call (`Reply.usage`). Nothing is worked out here and there is no price table. A cost that no call
reported is null, and `cost_reported_calls` says how many calls reported one, so a partial sum
shows.

It never changes or slows an answer. The line is built when the run ends, after the answer, and
handed to one writer thread, so the run does not wait for the disk. The writer does one `write()` of
the whole line on a file opened for append, one line at a time, so two runs cannot interleave a
line.
Any failure (no directory, no permission, a full disk) is counted, logged as one counts-only line
and swallowed.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from .model import usage_of

SCHEMA_VERSION = 1
MAX_NAME_CHARS = 80  # a tool name, a provider or an outcome is cut to this (a model can invent one)
NS_PER_MS = 1_000_000
FILE_MODE = 0o640
COST_DECIMALS = 12  # only to remove float noise from a sum of reported costs
WAIT_SECONDS = 5.0  # the most `wait_all` waits for the writer (shutdown and tests, never a run)

log = logging.getLogger("orchestrator.usage")
_LOGS: dict = {}
_GUARD = threading.Lock()


def _write_line(path: str, data: bytes) -> None:
    """Append one whole line with a single write on an O_APPEND file."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, FILE_MODE)
    try:
        written = os.write(fd, data)
    finally:
        os.close(fd)
    if written != len(data):
        raise OSError("short write")


class UsageLog:
    """The file, the single writer and the counters. One for each file path."""

    def __init__(self, path: str, model: str = ""):
        self.path, self.model = path, model
        self._stats = {"lines_written": 0, "lines_failed": 0}
        self._lock = threading.Lock()
        self._pool: ThreadPoolExecutor | None = None

    @property
    def on(self) -> bool:
        return bool(self.path)

    def summary(self) -> dict:
        """What the health check may say: on or off, and counts. No path, no text."""
        with self._lock:
            return {"on": self.on, **self._stats}

    def _count(self, name: str) -> None:
        with self._lock:
            self._stats[name] += 1

    def fail(self, why: str) -> None:
        self._count("lines_failed")
        log.warning("usage log: line not written, reason=%s", why)  # the class name only

    def submit(self, line: dict) -> None:
        """Queue one line for the writer. Returns at once; never raises."""
        if not self.on:
            return
        try:
            data = (json.dumps(line, separators=(",", ":")) + "\n").encode("utf-8")
            with self._lock:
                if self._pool is None:
                    self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="usage-log")
                pool = self._pool
            pool.submit(self._append, data)
        except Exception as error:
            self.fail(type(error).__name__)

    def _append(self, data: bytes) -> None:
        try:
            _write_line(self.path, data)
        except Exception as error:
            self.fail(type(error).__name__)
            return
        self._count("lines_written")

    def wait(self, timeout: float) -> None:
        """Wait until every line queued so far has been written or has failed."""
        pool = self._pool
        if pool is not None:
            pool.submit(lambda: None).result(timeout)


def log_for(settings) -> UsageLog:
    """The log for these settings: one shared object for each file, so the counters add up."""
    key = (settings.usage_log_file, settings.model)
    with _GUARD:
        if key not in _LOGS:
            _LOGS[key] = UsageLog(*key)
        return _LOGS[key]


def wait_all(timeout: float = WAIT_SECONDS) -> None:
    """For the service's shutdown and for tests: wait for the lines still queued."""
    with _GUARD:
        logs = list(_LOGS.values())
    for found in logs:
        try:
            found.wait(timeout)
        except Exception:  # (a timeout: the writer is still busy; counted by the writer itself)
            pass


def _cut(text) -> str:
    return str(text)[:MAX_NAME_CHARS]


def _stamp(ns: int) -> str:
    moment = datetime.fromtimestamp(ns / 1e9, UTC)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class RunUsage:
    """What one run used, collected while it goes on and written when it ends."""

    def __init__(
        self, log_: UsageLog | None, *, run_id: str, kind: str, conversation_id: str | None,
        user_id: str | None, model: str,
    ):
        self.log, self.run_id, self.kind = log_, run_id, kind
        self.conversation_id, self.user_id, self.model = conversation_id, user_id, model
        self.started_wall, self.started_mono = time.time_ns(), time.monotonic_ns()
        self.calls: list = []
        self.reported_totals: list = []  # the service's own total_tokens for each call
        self.tools: list = []
        self.provider: str | None = None
        self.done = False

    def call(self, latency_ms: int, reply, error=None) -> None:
        """One model call. A failed call has no reply: it counts, with no numbers."""
        got = usage_of({"usage": getattr(reply, "usage", None)}) or {}
        entry = {
            "n": len(self.calls) + 1,
            "input_tokens": got.get("prompt_tokens"),
            "output_tokens": got.get("completion_tokens"),
        }
        if "reasoning_tokens" in got:
            entry["reasoning_tokens"] = got["reasoning_tokens"]
        entry["cost_usd"] = got.get("cost")
        entry["latency_ms"] = latency_ms
        entry["tools_requested"] = [_cut(c.name) for c in getattr(reply, "calls", ())]
        self.calls.append(entry)
        self.reported_totals.append(got.get("total_tokens", 0))
        provider = getattr(reply, "provider", None)
        if provider and self.provider is None:  # the first call that names one
            self.provider = _cut(provider)

    def tool(self, name: str, latency_ms: int, outcome: str) -> None:
        self.tools.append({"name": _cut(name), "latency_ms": latency_ms, "outcome": _cut(outcome)})

    def totals(self) -> dict:
        """Sums of what the service reported. A call that reported nothing adds nothing."""
        calls = self.calls
        costs = [c["cost_usd"] for c in calls if c["cost_usd"] is not None]
        reasoning = [c["reasoning_tokens"] for c in calls if "reasoning_tokens" in c]
        found = {
            "total_input_tokens": sum(c["input_tokens"] or 0 for c in calls),
            "total_output_tokens": sum(c["output_tokens"] or 0 for c in calls),
            "total_tokens": sum(self.reported_totals),
        }
        if costs:  # absent when no call reported a cost
            found["total_cost_usd"] = round(math.fsum(costs), COST_DECIMALS)
        if reasoning:
            found["total_reasoning_tokens"] = sum(reasoning)
        found["model_calls"] = len(calls)
        found["tool_calls"] = len(self.tools)
        found["cost_reported_calls"] = len(costs)
        if calls:
            found["models"] = [self.model]
        return found

    def line(self, outcome: str, repair_rounds=None) -> dict:
        """The line, schema v1. The key order is the order below."""
        totals = self.totals()
        elapsed = time.monotonic_ns() - self.started_mono
        line = {
            "v": SCHEMA_VERSION,
            "run_id": self.run_id,
            "conversation_id": self.conversation_id,
            "kind": self.kind,
            "started_at": _stamp(self.started_wall),
            "ended_at": _stamp(self.started_wall + elapsed),
            "latency_ms": elapsed // NS_PER_MS,
            "outcome": _cut(outcome),
            "user_id": self.user_id,
            "model": self.model,
            "provider": self.provider,
            "model_calls": totals["model_calls"],
            "tool_calls": totals["tool_calls"],
            "input_tokens": totals["total_input_tokens"],
            "output_tokens": totals["total_output_tokens"],
            "total_tokens": totals["total_tokens"],
        }
        if "total_reasoning_tokens" in totals:
            line["reasoning_tokens"] = totals["total_reasoning_tokens"]
        rounds = repair_rounds if isinstance(repair_rounds, int) else None
        line["cost_usd"] = totals.get("total_cost_usd")
        line["cost_reported_calls"] = totals["cost_reported_calls"]
        line["repair_rounds"] = None if isinstance(repair_rounds, bool) else rounds
        line["calls"] = self.calls
        line["tools"] = self.tools
        return line

    def finish(self, outcome: str, repair_rounds=None) -> None:
        """Write the line, once. Nothing happens when the log is off."""
        if self.done:
            return
        self.done = True
        if self.log is None or not self.log.on:
            return
        try:
            self.log.submit(self.line(outcome, repair_rounds))
        except Exception as error:
            self.log.fail(type(error).__name__)
