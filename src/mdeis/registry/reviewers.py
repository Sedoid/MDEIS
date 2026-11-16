"""Who may clear a hold (developer) and who may decide as the teacher: the rows of
`registry_reviewers`. Run only from the command line by the server operator:

    python -m mdeis.admin grant-reviewer --user-id U --name "Name" --role teacher|developer
        [--revoke]

This is an admin step, like `ack-methodology`. It is not a tool: it is not in the MCP server, it is
not offered by `mdeis.registry`, and no model, gateway or portal code imports it. The person who
runs it is the operator of the server, read from the account the command runs as (`operator()`), and
no argument can name another. The reviewer is the person being recorded, not the one who records.

A grant is idempotent: granting what already stands changes nothing. A second name for a standing
grant is refused (the name is copied into every decision, so it is changed by revoking and granting
again). A revoke ends the grant and keeps the row, so who was recorded, and when, stays on file.
Every grant, revoke and refusal is one row in the action log. The functions run on the connection
they are given and do not commit: the command does.
"""

from __future__ import annotations

import getpass

from psycopg.rows import tuple_row

from ..db import diary
from . import transitions as t

ROLES = (t.DEVELOPER, t.TEACHER)
GRANT_TOOL = "admin_grant_reviewer"
REVOKE_TOOL = "admin_revoke_reviewer"
UNKNOWN_OPERATOR = "unknown"


class ReviewerRefused(Exception):
    """The grant or revoke was refused. The message says why, in plain words."""


def operator() -> str:
    """The server operator: the account that runs the command, as `cli:<account>`."""
    try:
        account = getpass.getuser()
    except (OSError, KeyError):
        account = ""
    return f"cli:{account or UNKNOWN_OPERATOR}"


def _refuse(tool: str, inputs: dict, reason: str, url) -> None:
    diary.write(tool, inputs=inputs, outputs={"reason": reason}, outcome="refused", url=url)
    raise ReviewerRefused(reason)


def _bad_input(role, who: str, name: str | None = None) -> str | None:
    if role not in ROLES:
        return f"the role must be one of: {', '.join(ROLES)}"
    if not who:
        return "a user id is needed"
    if name is not None and not name:
        return "a name is needed"
    return None


def grant_reviewer(conn, user_id, name, role, *, by: str, url: str | None = None) -> dict:
    """Record `user_id` as a `role`. `by` is the operator (see `operator()`)."""
    who, label = (user_id or "").strip(), (name or "").strip()
    inputs = {"user_id": who, "name": label, "role": role, "by": by}
    reason = _bad_input(role, who, label)
    row = None
    cursor = conn.cursor(row_factory=tuple_row)
    if reason is None:
        row = cursor.execute(
            "SELECT name, revoked_at FROM registry_reviewers "
            "WHERE user_id = %s AND role = %s FOR UPDATE",
            (who, role),
        ).fetchone()
        if row is not None and row[1] is None and row[0] != label:
            reason = (
                f"{who} is already recorded as a {role} under the name {row[0]!r}: revoke it "
                "first, then grant again with the new name"
            )
    if reason is not None:
        _refuse(GRANT_TOOL, inputs, reason, url)
    if row is None:
        cursor.execute(
            "INSERT INTO registry_reviewers (user_id, role, name, granted_by) "
            "VALUES (%s, %s, %s, %s)",
            (who, role, label, by),
        )
        action = "granted"
    elif row[1] is not None:
        cursor.execute(
            "UPDATE registry_reviewers SET name = %s, granted_by = %s, granted_at = now(), "
            "revoked_at = NULL WHERE user_id = %s AND role = %s",
            (label, by, who, role),
        )
        action = "granted again"
    else:
        action = "unchanged"
    changed = action != "unchanged"
    event = diary.write(
        GRANT_TOOL,
        inputs=inputs,
        outputs={"changed": changed, "action": action},
        outcome="ok",
        url=url,
    )
    return {"user_id": who, "name": label, "role": role, "action": action, "changed": changed,
            "logged_event_id": event}


def revoke_reviewer(conn, user_id, role, *, by: str, url: str | None = None) -> dict:
    """End the grant of `user_id` as a `role`. The row stays, with the time it ended."""
    who = (user_id or "").strip()
    inputs = {"user_id": who, "role": role, "revoke": True, "by": by}
    reason = _bad_input(role, who)
    cursor = conn.cursor(row_factory=tuple_row)
    row = None
    if reason is None:
        row = cursor.execute(
            "SELECT revoked_at FROM registry_reviewers "
            "WHERE user_id = %s AND role = %s FOR UPDATE",
            (who, role),
        ).fetchone()
        if row is None:
            reason = f"there is no {role} recorded for {who}, so there is nothing to revoke"
    if reason is not None:
        _refuse(REVOKE_TOOL, inputs, reason, url)
    changed = row[0] is None
    if changed:
        cursor.execute(
            "UPDATE registry_reviewers SET revoked_at = now() WHERE user_id = %s AND role = %s",
            (who, role),
        )
    event = diary.write(
        REVOKE_TOOL,
        inputs=inputs,
        outputs={"changed": changed, "action": "revoked" if changed else "unchanged"},
        outcome="ok",
        url=url,
    )
    return {"user_id": who, "role": role, "action": "revoked" if changed else "unchanged",
            "changed": changed, "logged_event_id": event}
