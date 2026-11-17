"""The model's `export_report` tool (T-076), with fakes and no database.

The tool only CHECKS. It reads the session (the signed-in person and the school) from the context,
takes one argument (a report number), asks whether that report exists in the caller's school and is
released, writes one diary line for the attempt, and answers `ok` with the number and the file name,
or with a typed refusal. It returns no file and no bytes, and it renders nothing: the PDF itself is
made later, by the plain HTTP route of T-056, when the person follows the link the system adds.

What is held here: a released report is `ok`; a draft, an unknown number, another school's report,
a malformed number, no sign-in and no school are refused with a typed reason; a malformed number
never reaches the database; every attempt writes exactly one diary line with the person from the
session; the only argument is the report number (an approver, a user or a school argument is a
TypeError); the answer holds no file; the description says what the model may and may not do and
holds no label, rank or pathway wording; the server lists the tool with that one argument; and the
tool's module imports no PDF writer, no model client and no network client.

Expected values are typed by hand. The row shapes are the ones the stored report query returns.
"""

import ast
import inspect
import json
import re
from pathlib import Path

import pytest

from mdeis import tools
from mdeis.db import diary
from mdeis.export import released
from mdeis.tools.context import Approver, ToolContext

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
BEN = Approver("u-ben", "Ben Staff")
SCHOOL = "TST"
RELEASED_BY = ("u-ada", "Ada Teacher")  # who released it: NOT the person who is signed in now
BANNED = ("weak", "remedial", "gifted", "lazy", "slow", "failing", "brilliant", "rank", "ranking",
          "pathway", "career", "label")  # fmt: skip


class Rows:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConn:
    """Answers the stored-report query from a table keyed by the report number."""

    def __init__(self, table=None):
        self.table, self.queries, self.rollbacks = table or {}, [], 0

    def execute(self, sql, params=()):
        self.queries.append((sql, params))
        return Rows(self.table.get(params[0]))

    def rollback(self):
        self.rollbacks += 1

    def commit(self):
        raise AssertionError("the tool must not commit when ctx.commit is False")


def row(report_id="R-000001", status="released", school=SCHOOL, learner="L-0001"):
    """(report_id, learner_id, status, body, findings, template_version, methodology_document_id,
    released_by_id, released_by_name, released_at, school_code)"""
    return (report_id, learner, status, "A body [A-000001].", [], "t-1", "D-000009",
            RELEASED_BY[0], RELEASED_BY[1], None, school)  # fmt: skip


def ctx_with(conn, approver=BEN, school=SCHOOL):
    return ToolContext(conn=conn, run_id="RUN-T", approver=approver,
                       database_url="postgresql://not-used", commit=False, school=school)


@pytest.fixture
def diary_rows(monkeypatch):
    rows = []

    def write(tool, **kw):
        rows.append({"tool": tool, **kw})
        return len(rows)

    monkeypatch.setattr(diary, "write", write)
    return rows


def call(conn, report_id="R-000001", **ctx_kw):
    return tools.export_report(ctx_with(conn, **ctx_kw), report_id=report_id)


def without_event(result):
    return {k: v for k, v in result.items() if k != "logged_event_id"}


# --------------------------------------------------------------------------- the good answer
def test_a_released_report_of_the_callers_school_is_ok_with_its_number_and_file_name(diary_rows):
    result = call(FakeConn({"R-000001": row()}))
    assert result == {
        "status": "ok", "report_id": "R-000001", "file_name": "R-000001.pdf", "logged_event_id": 1,
    }


def test_the_answer_holds_no_file_no_bytes_no_hash_and_no_link(diary_rows):
    result = call(FakeConn({"R-000001": row()}))
    assert set(result) == {"status", "report_id", "file_name", "logged_event_id"}
    json.dumps(result)  # plain data: a bytes value would raise here
    assert not [v for v in result.values() if isinstance(v, bytes)]
    assert not re.search(r"https?://|/portal|#/", json.dumps(result)), "the system adds the link"


def test_the_report_number_asked_about_is_the_one_looked_up_and_nothing_else_is_read(diary_rows):
    conn = FakeConn({"R-000001": row()})
    call(conn)
    (query,) = conn.queries
    assert query[1] == ("R-000001",)
    assert query[0].lstrip().upper().startswith("SELECT"), "read-only"
    assert not re.search(r"\b(INSERT|UPDATE|DELETE)\b", query[0].upper())


# --------------------------------------------------------------------------- refusals
@pytest.mark.parametrize("status", ["pending_review", "draft", "rejected", "anything-else"])
def test_a_report_that_is_not_released_is_refused_with_a_typed_reason(diary_rows, status):
    result = call(FakeConn({"R-000001": row(status=status)}))
    assert result["status"] == "refused" and result["reason"] == "report_not_released"
    assert result["detail"] == released.PLAIN["report_not_released"]
    assert "file_name" not in result and "report_id" not in result
    assert diary_rows[0]["outcome"] == "refused"


def test_an_unknown_report_is_not_found(diary_rows):
    result = call(FakeConn(), "R-999999")
    assert (result["status"], result["reason"]) == ("refused", "report_not_found")
    assert result["detail"] == "There is no such report."


def test_another_schools_report_is_answered_exactly_like_an_unknown_one(diary_rows):
    other = call(FakeConn({"R-000002": row("R-000002", school="OTH")}), "R-000002")
    unknown = call(FakeConn(), "R-000002")
    assert other["reason"] == "report_not_found"
    assert without_event(other) == without_event(unknown), "its existence is not revealed"


@pytest.mark.parametrize("bad", ["", "R-12", "r-000001", "R-000001; DROP TABLE reports",
                                 "R-000001\n", " R-000001", "LP-000001", None, 7, ["R-000001"]])
def test_a_malformed_number_is_not_found_and_never_reaches_the_database(diary_rows, bad):
    conn = FakeConn({"R-000001": row()})
    result = call(conn, bad)
    assert (result["status"], result["reason"]) == ("refused", "report_not_found")
    assert conn.queries == []


def test_with_no_one_signed_in_it_is_refused_and_logged_with_no_person_and_no_query(diary_rows):
    conn = FakeConn({"R-000001": row()})
    result = call(conn, approver=None)
    assert (result["status"], result["reason"]) == ("refused", "not_signed_in")
    assert conn.queries == []
    assert (diary_rows[0]["approver_id"], diary_rows[0]["approver_name"]) == (None, None)
    assert diary_rows[0]["outcome"] == "refused"


def test_with_no_school_in_the_session_it_is_refused_and_logged_with_no_query(diary_rows):
    conn = FakeConn({"R-000001": row()})
    result = call(conn, school=None)
    assert (result["status"], result["reason"]) == ("refused", "no_school")
    assert conn.queries == []
    assert diary_rows[0]["approver_name"] == BEN.name


# --------------------------------------------------------------------------- the diary
def test_every_attempt_writes_exactly_one_diary_line_in_order_with_the_person_from_the_session(
    diary_rows,
):
    conn = FakeConn({"R-000001": row(), "R-000002": row("R-000002", status="pending_review")})
    call(conn, "R-000001")
    call(conn, "R-000002")
    call(conn, "R-999999")
    assert [r["outcome"] for r in diary_rows] == ["ok", "refused", "refused"]
    assert {r["tool"] for r in diary_rows} == {"export_report"}
    for line in diary_rows:
        assert (line["approver_id"], line["approver_name"]) == (BEN.id, BEN.name)
        assert line["run_id"] == "RUN-T"
    assert [r["inputs"] for r in diary_rows] == [
        {"report_id": "R-000001"}, {"report_id": "R-000002"}, {"report_id": "R-999999"}]


def test_the_person_in_the_diary_is_the_signed_in_one_not_the_one_who_released_the_report(
    diary_rows,
):
    call(FakeConn({"R-000001": row()}))
    assert diary_rows[0]["approver_name"] == BEN.name != RELEASED_BY[1]


def test_when_the_diary_cannot_be_written_the_call_fails_and_nothing_is_returned(monkeypatch):
    def down(*args, **kwargs):
        raise diary.DiaryUnavailable("down")

    monkeypatch.setattr(diary, "write", down)
    conn = FakeConn({"R-000001": row()})
    with pytest.raises(diary.DiaryUnavailable):
        call(conn)
    assert conn.rollbacks >= 1


# --------------------------------------------------------------------------- what it takes
def test_the_only_argument_after_the_context_is_the_report_number():
    assert list(inspect.signature(tools.export_report).parameters) == ["ctx", "report_id"]


@pytest.mark.parametrize("extra", [{"approver": "x"}, {"approver_name": "x"}, {"user": "x"},
                                   {"school": "TST"}, {"school_id": "TST"}, {"by": "x"},
                                   {"file_name": "x.pdf"}, {"pdf": b"%PDF"}])
def test_an_extra_argument_naming_a_person_a_school_or_a_file_is_a_type_error(diary_rows, extra):
    with pytest.raises(TypeError):
        tools.export_report(ctx_with(FakeConn()), report_id="R-000001", **extra)
    assert diary_rows == [], "nothing happened"


def test_the_shared_check_takes_only_the_context_and_the_number():
    assert list(inspect.signature(released.check_released).parameters) == ["ctx", "report_id"]
    assert list(inspect.signature(released.find_released).parameters) == ["ctx", "report_id"]


# --------------------------------------------------------------------------- the description
def description():
    return " ".join((tools.export_report.__doc__ or "").split())


@pytest.mark.parametrize("phrase", [
    "Only a released report can be exported",
    "You cannot make, attach or send a PDF yourself",
    "returns no file",
    "the system adds the download link under your answer",
    "Never write a link, a path or a file name",
    "Do not promise a file for a draft",
    "say plainly that the report is not released",
    "the approver releases it",
    "report_not_released",
    "report_not_found",
    "never one you made up",
    "comes from the signed-in session",
])
def test_the_description_says_what_the_model_may_and_may_not_do(phrase):
    assert phrase in description(), phrase


def test_the_description_has_no_label_rank_or_pathway_wording():
    words = set(re.findall(r"[a-z]+", description().lower()))
    assert not words & set(BANNED), words & set(BANNED)


# --------------------------------------------------------------------------- the code
def imports_of(path):
    tree = ast.parse(path.read_text("utf-8"))
    found = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    found |= {("." * n.level) + (n.module or "") for n in ast.walk(tree)
              if isinstance(n, ast.ImportFrom)}
    return found


def test_the_tool_module_renders_nothing_and_reaches_no_model_or_network():
    path = SRC / "mdeis" / "tools" / "export.py"
    found = imports_of(path)
    text = " ".join(found)
    for word in ("pdf_writer", "report_pdf", "report_export", "httpx", "requests", "urllib",
                 "socket", "openai", "anthropic", "orchestrator", "gateway", "portal"):
        assert word not in text, (word, found)
    source = path.read_text("utf-8")
    for word in ("render_report_pdf", "write_pdf", "sha256", "approver=", "Approver("):
        assert word not in source, word


def test_the_tool_does_not_decide_in_two_places():
    """The check is the shared one: the tool does not repeat the stored-report query."""
    source = (SRC / "mdeis" / "tools" / "export.py").read_text("utf-8")
    assert "check_released" in source
    assert "FROM reports" not in source and ".execute(" not in source


def test_the_download_path_and_the_tool_use_the_same_check():
    text = (SRC / "mdeis" / "export" / "report_export.py").read_text("utf-8")
    assert "find_released" in text, "the download decides with the same function"
    assert "FROM reports" not in text, "the query lives in one place"
    assert "FROM reports" in (SRC / "mdeis" / "export" / "released.py").read_text("utf-8")


# --------------------------------------------------------------------------- the server
def server_source():
    return (SRC / "mdeis" / "server.py").read_text("utf-8")


def test_the_server_wraps_the_tool_with_the_report_number_and_nothing_else():
    source = server_source()
    assert "@mcp.tool(description=_about(tools.export_report))" in source
    assert re.search(r"def export_report\(request: Context, report_id: str\) -> dict:", source)
    assert "return _run(request, tools.export_report, report_id=report_id)" in source


def test_the_server_lists_the_tool_with_one_required_argument_and_the_tools_own_description():
    pytest.importorskip("mcp")
    import asyncio

    from mdeis import server

    listed = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    tool = listed["export_report"]
    assert set(tool.inputSchema["properties"]) == {"report_id"}
    assert tool.inputSchema["required"] == ["report_id"]
    assert tool.description == description()
    assert not set(tool.inputSchema["properties"]) & {"approver", "approver_id", "approver_name",
                                                      "user", "user_id", "by"}
