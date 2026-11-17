"""The one place `REVIEWERS_ARE_APPROVERS` writes (see `mdeis.approver_setting`).

The database guard on `measure_versions` copies the name of a teacher or a developer from
`registry_reviewers` and refuses a person who has no live row there. When the setting accepts a
signed-in approver who has no row, the row is made here, in the same transaction as the decision
or the cleared hold it is for, by `add_row`. If the database refuses that decision, the work is
undone with the row. After the work is done, `log` writes one action-log row that says the person
was "auto-registered by REVIEWERS_ARE_APPROVERS", with the person from the token.

Both are idempotent: a person who already has a live row is left as they are (the registry's name
stays), and a second call makes no second row and no second log line. Nothing here can name a
person: the person and the school are read from the context the server built from the verified
token. It is not a tool, it is not in `mdeis.registry`'s public list, and no model, gateway or
portal code reaches it.
"""

from __future__ import annotations

from psycopg.rows import tuple_row

from .. import approver_setting as setting
from ..db import diary
from ..tools.context import Refusal, ToolContext
from . import transitions as t

ROLES = (t.DEVELOPER, t.TEACHER)


def add_row(ctx: ToolContext, who, role: str, name: str) -> bool:
    """Make the registry row for `who` in `role`, if there is none. True when a row was made.

    Run it inside the savepoint of the write it is for. A record that an administrator ended is
    refused, and so is a session with no school."""
    if role not in ROLES:
        raise Refusal(f"the role must be one of: {', '.join(ROLES)}")
    if setting.school_of(ctx) is None:
        raise Refusal("no school is attached to this session")
    cursor = ctx.conn.cursor(row_factory=tuple_row)
    made = cursor.execute(
        "INSERT INTO registry_reviewers (user_id, role, name, granted_by) "
        "VALUES (%s, %s, %s, %s) ON CONFLICT (user_id, role) DO NOTHING RETURNING 1",
        (who.id, role, name, setting.GRANTED_BY),
    ).fetchone()
    if made is not None:
        return True
    live = cursor.execute(
        "SELECT 1 FROM registry_reviewers "
        "WHERE user_id = %s AND role = %s AND revoked_at IS NULL",
        (who.id, role),
    ).fetchone()
    if live is None:
        raise Refusal(setting.ended_text(role))
    return False


def log(ctx: ToolContext, who, role: str, name: str) -> None:
    """The action-log row for a row `add_row` made. Call it after the work it belongs to went
    through."""
    diary.write(
        setting.AUTO_TOOL,
        run_id=ctx.run_id,
        inputs={
            "user_id": who.id,
            "role": role,
            "name": name,
            "school": setting.school_of(ctx),
            "setting": setting.FLAG,
            "granted_by": setting.GRANTED_BY,
        },
        outputs={"action": setting.AUTO_NOTE, "role": role},
        outcome="ok",
        approver_id=who.id,
        approver_name=who.name,
        url=ctx.database_url,
    )
