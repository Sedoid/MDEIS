"""Write the diary (the `action_log` table) on its own short connection.

Why a separate connection: a tool does its work inside a transaction. If it wrote its diary line in
that same transaction, a failed or refused call would roll the line back with the work, and the
refusal would leave no trace. Worse, the diary takes a lock that is held until the transaction ends,
so one slow transaction would stall every other writer. Here each line is one INSERT on a fresh
connection that commits at once, so the lock is held for a few milliseconds.

Fail closed: if the line cannot be written (database down, lock not obtained in time), `write`
raises DiaryUnavailable and the call must be refused. A call that is not in the diary did not happen
as far as the rules are concerned.

Two ways to use it:

    diary.write("get_criterion", run_id="R-0001", inputs={...}, outcome="ok")   # one line

    with diary.logged_call("approve_report", run_id=rid, inputs={...}, work=conn) as call:
        ...do the work on `conn`...
        call.outputs = {"report_id": rid}
        # leaving the block normally: the line is written, then `conn` is committed
        # raising inside the block: `conn` is rolled back, then the line is written
        # (outcome "error", or "refused"/"blocked" if the exception is a CallRefused)

Known limit: a hard crash of the process in the middle of a call leaves no line. Nothing inside the
process can log that; the run record shows the call never finished.
"""

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

OUTCOMES = ("ok", "refused", "blocked", "error")

LOCK_TIMEOUT_MS = 2000  # how long one attempt waits for the diary's turn
STATEMENT_TIMEOUT_MS = 5000
CONNECT_TIMEOUT_S = 3
ATTEMPTS = 2  # one retry, for a brief clash with another writer
RETRY_PAUSE_S = 0.05

INSERT_SQL = (
    "INSERT INTO action_log (run_id, tool, inputs, outputs, outcome, approver_id, approver_name) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING event_id"
)


class DiaryUnavailable(RuntimeError):
    """The diary line could not be written. The call must be refused."""


class CallRefused(Exception):
    """Raise inside `logged_call` to refuse a call on purpose.

    `outcome` is "refused" (the rules say no) or "blocked" (a gate stopped it). The reason goes into
    the diary line's outputs.
    """

    def __init__(self, reason: str, outcome: str = "refused"):
        super().__init__(reason)
        if outcome not in ("refused", "blocked"):
            raise ValueError("outcome must be 'refused' or 'blocked'")
        self.reason = reason
        self.outcome = outcome


def jsonb(value: Any) -> Jsonb:
    """A value as a jsonb parameter. Anything json cannot hold is written as text."""
    return Jsonb(value, dumps=lambda o: json.dumps(o, default=str, ensure_ascii=False))


def _conninfo(url: str | None, lock_timeout_ms: int) -> str:
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        raise DiaryUnavailable("DATABASE_URL is not set, so the diary cannot be written")
    # Keep any options already in the URL (the tests point search_path at a scratch schema).
    existing = conninfo_to_dict(url).get("options", "")
    ours = f"-c lock_timeout={lock_timeout_ms} -c statement_timeout={STATEMENT_TIMEOUT_MS}"
    return make_conninfo(
        url, connect_timeout=CONNECT_TIMEOUT_S, options=f"{existing} {ours}".strip()
    )


def write(
    tool: str,
    *,
    run_id: str | None = None,
    inputs: dict | None = None,
    outputs: dict | None = None,
    outcome: str = "ok",
    approver_id: str | None = None,
    approver_name: str | None = None,
    url: str | None = None,
    lock_timeout_ms: int = LOCK_TIMEOUT_MS,
    attempts: int = ATTEMPTS,
) -> int:
    """Add one line to the diary and commit it. Returns the event number.

    Raises DiaryUnavailable if the line could not be written. Never returns without a line.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}, not {outcome!r}")
    if not tool:
        raise ValueError("tool must not be empty")
    params = (
        run_id,
        tool,
        jsonb(inputs or {}),
        jsonb(outputs or {}),
        outcome,
        approver_id,
        approver_name,
    )
    conninfo = _conninfo(url, lock_timeout_ms)
    last: Exception | None = None
    for attempt in range(max(1, attempts)):
        if attempt:
            time.sleep(RETRY_PAUSE_S)
        try:
            # autocommit: the INSERT is its own transaction, so the diary lock is released at once.
            with psycopg.connect(conninfo, autocommit=True) as c:
                return c.execute(INSERT_SQL, params).fetchone()[0]
        except psycopg.Error as e:
            last = e
    raise DiaryUnavailable(f"could not write the diary line for {tool!r}: {last}") from last


@dataclass
class Call:
    """What a `logged_call` block fills in. Set `outputs` (and `approver_*`) before leaving."""

    tool: str
    run_id: str | None = None
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    approver_id: str | None = None
    approver_name: str | None = None
    event_id: int | None = None


class logged_call:  # noqa: N801  (used like a function, written like a context manager)
    """Run a block as one logged call. See the module docstring.

    `work` is the connection the block does its database work on. If given, it is committed only
    after the diary line is safely written, and rolled back if the block raises or the line cannot
    be written. If the block does no database work, leave it out.
    """

    def __init__(
        self,
        tool: str,
        *,
        run_id: str | None = None,
        inputs: dict | None = None,
        work: "psycopg.Connection | None" = None,
        url: str | None = None,
    ):
        self.call = Call(tool=tool, run_id=run_id, inputs=dict(inputs or {}))
        self.work = work
        self.url = url

    def _write(self, outcome: str, outputs: dict) -> int:
        c = self.call
        return write(
            c.tool,
            run_id=c.run_id,
            inputs=c.inputs,
            outputs=outputs,
            outcome=outcome,
            approver_id=c.approver_id,
            approver_name=c.approver_name,
            url=self.url,
        )

    def __enter__(self) -> Call:
        return self.call

    def __exit__(self, exc_type, exc, tb) -> bool:
        c = self.call
        if exc is None:
            try:
                c.event_id = self._write("ok", c.outputs)
            except DiaryUnavailable:
                if self.work is not None:
                    self.work.rollback()
                raise
            if self.work is not None:
                try:
                    self.work.commit()
                except Exception as commit_error:
                    self.work.rollback()
                    # The "ok" line is already in. Add a line that says the work did not happen.
                    self._write("error", {"commit_failed": str(commit_error)})
                    raise
            return False

        if self.work is not None:
            self.work.rollback()
        if isinstance(exc, CallRefused):
            outcome, outputs = exc.outcome, {**c.outputs, "reason": exc.reason}
        elif isinstance(exc, Exception):
            outcome, outputs = "error", {**c.outputs, "error": f"{type(exc).__name__}: {exc}"}
        else:  # KeyboardInterrupt, SystemExit: leave as they are, but still note them
            outcome, outputs = "error", {"interrupted": type(exc).__name__}
        try:
            c.event_id = self._write(outcome, outputs)
        except DiaryUnavailable as lost:
            # The refusal itself could not be logged. Say so, and keep the original as the cause.
            raise lost from exc
        return False  # the original exception continues to the caller
