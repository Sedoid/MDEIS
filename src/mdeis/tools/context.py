"""What every tool is given: the database connection, the run, and who is signed in.

The approver is set here by the server, from the verified token (mdeis.core.approver_token). It is
not a tool argument and the AI writer never sees it. A tool that needs a named person reads
`ctx.approver` and refuses when it is None.

The school is set the same way: from the `sch` claim of the verified token, never from a tool
argument. A tool that works on one school's data reads `ctx.school` and refuses when it is None.
"""

from dataclasses import dataclass

import psycopg


class Refusal(Exception):
    """Raise inside a tool to refuse the call. The reason is shown to the caller and logged."""


@dataclass(frozen=True)
class Approver:
    id: str
    name: str


@dataclass
class ToolContext:
    conn: psycopg.Connection
    run_id: str
    approver: Approver | None = None
    database_url: str | None = None  # where the diary line is written, on its own connection
    commit: bool = True  # the tests set this to False so that each test can be rolled back
    school: str | None = None  # the school code from the verified token, or None when it has none
