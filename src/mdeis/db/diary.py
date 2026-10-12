"""Write one line in the diary (the `action_log` table). Every tool call writes exactly one.

The contract for every tool:

1. One call, one line, whatever the outcome (ok, refused, blocked or error).
2. A call that is refused, blocked or fails writes its line with `log_event`. That uses its own
   short connection, so the line stays even if the work the call was doing is rolled back.
3. A call that succeeds and changes data writes its line with `write_line` on the same connection,
   in the same transaction as the change. The change and its line are saved together or not at all.
4. If the line cannot be written, the call fails (`DiaryError`) and nothing is reported as done.
   The diary is never skipped to keep a call going.
5. The approver and the run id come from the verified session, never from the tool's arguments.

What this does not cover: a crash that kills the process in the middle of a call (power loss, a
killed container) leaves no line for that call. The diary records calls that finished.

The database sets the event number and both hashes itself (`action_log_chain`). Nothing sent for
them is used, so this module has no way to pass them.
"""

import os

import psycopg
from psycopg.types.json import Jsonb

OUTCOMES = ("ok", "refused", "blocked", "error")

INSERT = (
    "INSERT INTO action_log (tool, run_id, inputs, outputs, outcome, approver_id, approver_name) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING event_id"
)


class DiaryError(RuntimeError):
    """The diary line could not be written. The call that wanted it must fail."""


def write_line(
    conn,
    *,
    tool,
    run_id=None,
    inputs=None,
    outputs=None,
    outcome="ok",
    approver_id=None,
    approver_name=None,
):
    """Add one line on `conn`, inside whatever transaction it is in. Returns the event number."""
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}")
    row = conn.execute(
        INSERT,
        (
            tool,
            run_id,
            Jsonb(inputs or {}),
            Jsonb(outputs or {}),
            outcome,
            approver_id,
            approver_name,
        ),
    ).fetchone()
    return row[0]


def log_event(
    *,
    tool,
    run_id=None,
    inputs=None,
    outputs=None,
    outcome="ok",
    approver_id=None,
    approver_name=None,
    database_url=None,
    lock_timeout_ms=5000,
    connect_timeout_s=5,
):
    """Add one line on its own connection and save it at once. Returns the event number.

    The connection does nothing else, so the diary's lock is held for the length of one insert.
    If the lock cannot be had within `lock_timeout_ms`, or the database cannot be reached, this
    raises DiaryError rather than waiting for ever.
    """
    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        raise DiaryError("could not write the diary: DATABASE_URL is not set")
    try:
        with psycopg.connect(url, autocommit=True, connect_timeout=connect_timeout_s) as conn:
            conn.execute(
                "SELECT set_config('lock_timeout', %s, false)", (f"{int(lock_timeout_ms)}ms",)
            )
            return write_line(
                conn,
                tool=tool,
                run_id=run_id,
                inputs=inputs,
                outputs=outputs,
                outcome=outcome,
                approver_id=approver_id,
                approver_name=approver_name,
            )
    except psycopg.Error as e:
        # The class name only: the message can carry connection details.
        raise DiaryError(f"could not write the diary ({type(e).__name__})") from e
