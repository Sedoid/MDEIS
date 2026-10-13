"""What every tool is given: the database connection, the run, and who is signed in.

The approver is set here by the server, from the verified token (mdeis.core.approver_token). It is
not a tool argument and the AI writer never sees it. A tool that needs a named person reads
`ctx.approver` and refuses when it is None.
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
