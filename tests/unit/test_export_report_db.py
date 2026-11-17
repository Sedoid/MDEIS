"""The model's `export_report` tool against a real database (T-076). Integration: needs
DATABASE_URL, works in a throwaway schema (the `db` fixture of `test_report_export_db.py`).
Skipped without DATABASE_URL (a skip is not a pass).

What is held here: a released report of the caller's school is `ok`; a draft, an unknown number,
another school's report and a malformed number are refused with a typed reason; every attempt
writes exactly one diary line (tool `export_report`) with the person from the session; nothing else
is written and the report is not changed; the tool says `ok` exactly when the download of T-056
would give a file (the two share one check), for the same four cases; and the tool's file name is
the download's.
"""

# ruff: noqa: F811  (the `db` fixture is imported by name and used as an argument)

import pytest
from psycopg.rows import dict_row
from test_report_export_db import (
    DOWNLOADER,
    SCHOOL,
    WHEN,
    counts,
    ctx_for,
    db,  # noqa: F401  (a fixture, used by name)
    make_report,
)

from mdeis import tools
from mdeis.export import report_export

pytestmark = pytest.mark.integration


def ask(db, report_id, run="RUN-TOOL", **ctx_kw):
    return tools.export_report(ctx_for(db, run=run, **ctx_kw), report_id=report_id)


def lines(db, run="RUN-TOOL"):
    return db.cursor(row_factory=dict_row).execute(
        "SELECT * FROM action_log WHERE run_id = %s ORDER BY event_id", (run,)
    ).fetchall()


def test_a_released_report_is_ok_with_its_number_and_file_name_and_one_diary_line(db):
    report_id = make_report(db)
    result = ask(db, report_id)
    assert result["status"] == "ok" and result["report_id"] == report_id
    assert result["file_name"] == f"{report_id}.pdf"
    assert set(result) == {"status", "report_id", "file_name", "logged_event_id"}
    (row,) = lines(db)
    assert (row["tool"], row["outcome"]) == ("export_report", "ok")
    assert (row["approver_id"], row["approver_name"]) == (DOWNLOADER.id, DOWNLOADER.name)
    assert row["inputs"] == {"report_id": report_id}
    assert row["outputs"]["status"] == "ok" and row["outputs"]["file_name"] == f"{report_id}.pdf"
    assert row["event_id"] == result["logged_event_id"]


def test_a_draft_is_refused_typed_and_logged_and_stays_a_draft(db):
    draft = make_report(db, status="pending_review", citations_ok=False)
    result = ask(db, draft)
    assert (result["status"], result["reason"]) == ("refused", "report_not_released")
    assert "file_name" not in result
    (row,) = lines(db)
    assert (row["tool"], row["outcome"], row["approver_name"]) == (
        "export_report", "refused", DOWNLOADER.name)
    assert row["outputs"]["reason"] == "report_not_released"
    status = db.execute("SELECT status FROM reports WHERE report_id = %s", (draft,)).fetchone()[0]
    assert status == "pending_review", "asking for a PDF never releases anything"


def test_an_unknown_number_and_another_schools_report_are_both_not_found_and_logged(db):
    other = make_report(db, learner="L-0002")
    for number in ("R-999999", other):
        result = ask(db, number)
        assert (result["status"], result["reason"]) == ("refused", "report_not_found"), number
    assert [r["outcome"] for r in lines(db)] == ["refused", "refused"]


@pytest.mark.parametrize("bad", ["", "R-12", "r-000001", "R-000001; DROP TABLE reports", None, 7])
def test_a_malformed_number_is_not_found_and_logged(db, bad):
    result = ask(db, bad)
    assert (result["status"], result["reason"]) == ("refused", "report_not_found")
    (row,) = lines(db)
    assert row["outcome"] == "refused"


def test_no_one_signed_in_and_no_school_are_refused_and_logged(db):
    report_id = make_report(db)
    none = ask(db, report_id, who=None)
    no_school = ask(db, report_id, run="RUN-TOOL-2", school=None)
    assert none["reason"] == "not_signed_in" and no_school["reason"] == "no_school"
    (first,) = lines(db)
    assert (first["approver_id"], first["approver_name"]) == (None, None)
    (second,) = lines(db, "RUN-TOOL-2")
    assert second["approver_name"] == DOWNLOADER.name


def test_the_tool_writes_only_its_diary_line_and_changes_no_report(db):
    report_id = make_report(db)
    before = counts(db)
    stored = db.execute("SELECT body, findings, status, released_by_name, released_at FROM reports "
                        "WHERE report_id = %s", (report_id,)).fetchone()
    ask(db, report_id)
    assert counts(db) == before
    assert db.execute("SELECT body, findings, status, released_by_name, released_at FROM reports "
                      "WHERE report_id = %s", (report_id,)).fetchone() == stored
    assert [r["tool"] for r in lines(db)] == ["export_report"]


def test_the_tool_says_ok_exactly_when_the_download_would_give_a_file(db):
    cases = {
        "released": make_report(db),
        "draft": make_report(db, status="pending_review", citations_ok=False),
        "unknown": "R-999999",
        "other school": make_report(db, learner="L-0002"),
    }
    for label, report_id in cases.items():
        said = ask(db, report_id, run=f"RUN-A-{label}")
        got = report_export.export_released_report(
            ctx_for(db, run=f"RUN-B-{label}"), report_id, WHEN)
        assert said["status"] == got["status"], label
        assert said.get("reason") == got.get("reason"), label
        if got["status"] == "ok":
            assert said["file_name"] == got["file_name"] and got["pdf"].startswith(b"%PDF")
    assert ask(db, cases["released"], run="RUN-C")["status"] == "ok"


def test_the_school_is_the_sessions_not_anything_the_caller_can_name(db):
    report_id = make_report(db)
    assert ask(db, report_id, school="OTH")["reason"] == "report_not_found"
    assert ask(db, report_id, school=SCHOOL, run="RUN-TOOL-3")["status"] == "ok"
