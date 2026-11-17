"""Exporting a stored report as a PDF, against a real database (T-056, E-44 and E-45). Integration:
needs DATABASE_URL, and works in a throwaway schema with one connection per test, as
`test_lessons_store.py` does. Skipped without DATABASE_URL (a skip is not a pass).

What is held here: a released report exports, and its PDF holds the stored body, every record and
clause ID of the stored findings, the person who released it and the pin; a draft, an unknown id,
an id of another school and a malformed id are refused with a typed reason and no file; every
attempt writes one diary row naming the signed-in person (the person in the session, not the
person who released the report); nothing is written but that row; the same stored report and the
same time give identical bytes; and the diary failing means no file.
"""

import re
from datetime import UTC, datetime

import psycopg
import pytest
from dbtools import run_migrate
from pdfread import lines, plumber_text, squashed
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from mdeis.db import diary
from mdeis.export import report_export
from mdeis.tools.context import Approver, ToolContext

pytestmark = pytest.mark.integration

WHEN = datetime(2026, 11, 17, 9, 30, tzinfo=UTC)
RELEASED_AT = datetime(2026, 11, 16, 11, 5, tzinfo=UTC)
SCHOOL, OTHER_SCHOOL = "TST", "OTH"
DOWNLOADER = Approver("u-dl", "Mr Downloader")  # signed in now; did NOT release the report
RELEASER = ("u-ada", "Ada Teacher")  # released it, as the stored row says
BODY = (
    "Reading was in the Form 1 range [A-000001] [A-000002].\n\n"
    "The evidence rule applies [TR-1] [TR-2]. See the methodology [M-6.4]."
)
FINDINGS = [
    {"skill": "Reading", "kind": "subject", "status": "ok", "trend": "steady",
     "result_hash": "c3" * 32, "record_ids": ["A-000001", "A-000002"],
     "clause_ids": ["TR-1", "TR-2"]},
]
TABLES = ("reports", "assessments", "learners", "issues", "resolutions", "clauses", "templates")


@pytest.fixture
def db(scratch_schema):
    schema, url = scratch_schema
    built = run_migrate(url)
    assert built.returncode == 0, built.stderr
    with psycopg.connect(url) as conn:
        conn.url = url
        conn.execute(
            "INSERT INTO templates (version_name, file_name, sha256, template_id, content, active) "
            "VALUES ('t-export', 't-export.yaml', repeat('a', 64), 'tid', %s, true)",
            (Jsonb({}),),
        )
        for clause, text in (("TR-1", "Evidence rule."), ("TR-2", "Move rule.")):
            conn.execute(
                "INSERT INTO clauses (version_name, clause_id, area, text, text_sha256) "
                "VALUES ('t-export', %s, 'trend', %s, repeat('b', 64))",
                (clause, text),
            )
        conn.execute(
            "INSERT INTO learners (learner_id, school_code, school_ref) VALUES "
            "('L-0001', %s, 'TST/1'), ('L-0002', %s, 'OTH/1')",
            (SCHOOL, OTHER_SCHOOL),
        )
        conn.execute(
            "INSERT INTO documents (document_id, kind, original_name, stored_name, sha256, "
            "size_bytes, uploader_id, status) VALUES "
            "('D-000003', 'results', 'card.pdf', 'x.pdf', repeat('c', 64), 10, 'u1', 'done'), "
            "('D-000009', 'methodology', 'method.md', 'y.md', repeat('d', 64), 10, 'u1', 'done')"
        )
        for n, term, page, row in ((1, 1, 2, 5), (2, 2, 2, 6)):
            conn.execute(
                "INSERT INTO assessments (assessment_id, learner_id, subject, score, "
                "academic_year, stage, term, denominator, source_document_id, source_page, "
                "source_row) VALUES (%s, 'L-0001', 'Reading', 55, '2025/2026', 'Form 1', %s, 100, "
                "'D-000003', %s, %s)",
                (f"A-{n:06d}", term, page, row),
            )
        conn.execute(
            "INSERT INTO method_chunks (document_id, section_id, title, page, ordinal, text, "
            "text_sha256) VALUES ('D-000009', '6.4', 'Reading the scores', 3, 1, 'x', "
            "repeat('e', 64))"
        )
        yield conn
        conn.rollback()


def make_report(db, status="released", learner="L-0001", body=BODY, findings=FINDINGS, **over):
    row = {
        "learner_id": learner, "status": status, "body": body, "findings": Jsonb(findings),
        "citations_ok": True, "template_version": "t-export",
        "methodology_document_id": "D-000009", **over,
    }
    if status == "released":
        row.setdefault("released_by_id", RELEASER[0])
        row.setdefault("released_by_name", RELEASER[1])
        row.setdefault("released_at", RELEASED_AT)
    names = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    return db.execute(
        f"INSERT INTO reports ({names}) VALUES ({marks}) RETURNING report_id", list(row.values())
    ).fetchone()[0]


def ctx_for(db, who=DOWNLOADER, school=SCHOOL, run="RUN-EXPORT"):
    return ToolContext(
        conn=db, run_id=run, approver=who, database_url=db.url, commit=False, school=school
    )


def export(db, report_id, **kw):
    ctx = kw.pop("ctx", None) or ctx_for(db)
    return report_export.export_released_report(ctx, report_id, WHEN, **kw)


def diary_rows(db, run="RUN-EXPORT"):
    return db.cursor(row_factory=dict_row).execute(
        "SELECT * FROM action_log WHERE run_id = %s ORDER BY event_id", (run,)
    ).fetchall()


def counts(db):
    return {t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}


# --------------------------------------------------------------------------- E-44
def test_E44_a_released_report_exports_with_its_body_its_ids_its_releaser_and_its_pin(db):
    report_id = make_report(db)
    result = export(db, report_id)
    assert result["status"] == "ok" and result["file_name"] == f"{report_id}.pdf"
    pdf = result["pdf"]
    assert pdf.startswith(b"%PDF-1.4") and result["size_bytes"] == len(pdf)
    read = squashed(plumber_text(pdf))
    assert "Reading was in the Form 1 range [A-000001] [A-000002]." in read
    assert "The evidence rule applies [TR-1] [TR-2]. See the methodology [M-6.4]." in read
    for stored_id in ("A-000001", "A-000002", "TR-1", "TR-2"):
        assert stored_id in read
    assert "Released by Ada Teacher on 2026-11-16 11:05 UTC" in read
    assert "Template version: t-export" in read and "Methodology document: D-000009" in read
    assert "Learner number: L-0001" in read
    assert "Result hash: " + "c3" * 32 in read


def test_the_sources_are_resolved_to_document_page_and_row_from_the_database(db):
    got = lines(export(db, make_report(db))["pdf"])
    assert "A-000001 | Reading | 2025/2026 term 1 | D-000003 | page 2 | row 5" in got
    assert "A-000002 | Reading | 2025/2026 term 2 | D-000003 | page 2 | row 6" in got
    assert "TR-1 | template t-export | Evidence rule." in got
    assert "M-6.4 | D-000009 | page 3" in got and "M-6.4: Reading the scores" in got


def test_the_releaser_on_the_page_is_the_stored_one_and_the_diary_names_the_signed_in_person(db):
    report_id = make_report(db)
    export(db, report_id)
    (row,) = diary_rows(db)
    assert (row["tool"], row["outcome"]) == ("export_report_pdf", "ok")
    assert (row["approver_id"], row["approver_name"]) == (DOWNLOADER.id, DOWNLOADER.name)
    assert row["inputs"] == {"report_id": report_id}
    assert row["outputs"]["status"] == "ok" and row["outputs"]["file_name"] == f"{report_id}.pdf"
    assert re.fullmatch(r"[0-9a-f]{64}", row["outputs"]["sha256"])
    assert "pdf" not in row["outputs"], "the file itself is not copied into the diary"
    assert "Released by Ada Teacher" in "\n".join(lines(export(db, report_id)["pdf"]))


def test_the_diary_row_ties_to_the_bytes_by_sha256(db):
    import hashlib

    result = export(db, make_report(db))
    assert result["sha256"] == hashlib.sha256(result["pdf"]).hexdigest()
    assert diary_rows(db)[0]["outputs"]["sha256"] == result["sha256"]


def test_the_same_stored_report_and_time_give_identical_bytes(db):
    report_id = make_report(db)
    first, second = export(db, report_id), export(db, report_id)
    assert first["pdf"] == second["pdf"] and first["sha256"] == second["sha256"]
    assert len(diary_rows(db)) == 2, "each download is its own diary row"


def test_the_practice_footer_follows_the_deployments_setting(db):
    report_id = make_report(db)
    assert "Practice data only" not in lines(export(db, report_id)["pdf"])
    assert lines(export(db, report_id, practice_data=True)["pdf"])[-1] == "Practice data only"


def test_the_export_reads_the_stored_report_and_writes_only_one_diary_row(db):
    report_id = make_report(db)
    before = counts(db)
    export(db, report_id)
    assert counts(db) == before
    assert [r["tool"] for r in diary_rows(db)] == ["export_report_pdf"]


def test_a_released_report_still_exports_after_the_active_template_changes(db):
    """The page is built from what was stored and pinned, not from what is in force now."""
    report_id = make_report(db)
    first = export(db, report_id)["pdf"]
    db.execute("UPDATE templates SET active = false")
    db.execute(
        "INSERT INTO templates (version_name, file_name, sha256, template_id, content, active) "
        "VALUES ('t-later', 't-later.yaml', repeat('a', 64), 'tid', %s, true)", (Jsonb({}),)
    )
    assert export(db, report_id)["pdf"] == first


# --------------------------------------------------------------------------- E-45
def test_E45_a_draft_is_refused_with_a_typed_reason_and_no_file_and_is_logged(db):
    draft = make_report(db, status="pending_review", citations_ok=False)
    result = export(db, draft)
    assert result["status"] == "refused" and result["reason"] == "report_not_released"
    assert "pdf" not in result and "file_name" not in result
    (row,) = diary_rows(db)
    assert (row["tool"], row["outcome"], row["approver_name"]) == (
        "export_report_pdf", "refused", DOWNLOADER.name)
    assert row["outputs"]["reason"] == "report_not_released"
    assert row["inputs"] == {"report_id": draft}


def test_E45_an_unknown_report_is_refused_and_logged(db):
    result = export(db, "R-999999")
    assert result["status"] == "refused" and result["reason"] == "report_not_found"
    assert "pdf" not in result
    (row,) = diary_rows(db)
    assert (row["outcome"], row["approver_id"]) == ("refused", DOWNLOADER.id)


@pytest.mark.parametrize("bad", ["", "R-12", "r-000001", "R-000001; DROP TABLE reports", None, 7])
def test_a_malformed_id_is_refused_as_not_found_and_logged_without_the_text(db, bad):
    result = export(db, bad)
    assert (result["status"], result["reason"]) == ("refused", "report_not_found")
    (row,) = diary_rows(db)
    assert row["outcome"] == "refused" and row["inputs"] == {"report_id": None}


def test_a_report_of_another_school_is_not_found_not_forbidden(db):
    other = make_report(db, learner="L-0002")
    result = export(db, other)
    assert (result["status"], result["reason"]) == ("refused", "report_not_found")
    assert "pdf" not in result
    assert diary_rows(db)[0]["outcome"] == "refused"


def test_no_one_signed_in_is_refused_and_logged_with_no_person(db):
    report_id = make_report(db)
    ctx = ToolContext(conn=db, run_id="RUN-EXPORT", approver=None, database_url=db.url,
                      commit=False, school=SCHOOL)
    result = export(db, report_id, ctx=ctx)
    assert (result["status"], result["reason"]) == ("refused", "not_signed_in")
    assert "pdf" not in result
    (row,) = diary_rows(db)
    assert (row["outcome"], row["approver_id"], row["approver_name"]) == ("refused", None, None)


def test_a_session_with_no_school_is_refused_and_logged(db):
    report_id = make_report(db)
    result = export(db, report_id, ctx=ctx_for(db, school=None))
    assert (result["status"], result["reason"]) == ("refused", "no_school")
    assert "pdf" not in result and diary_rows(db)[0]["outcome"] == "refused"


def test_every_refusal_has_a_plain_detail_and_a_logged_event_number(db):
    result = export(db, "R-999999")
    assert result["detail"] == report_export.PLAIN["report_not_found"]
    assert isinstance(result["logged_event_id"], int)


# --------------------------------------------------------------------------- the diary is a gate
def test_when_the_diary_cannot_be_written_no_file_is_returned(db, monkeypatch):
    report_id = make_report(db)

    def down(*args, **kwargs):
        raise diary.DiaryUnavailable("down")

    monkeypatch.setattr(diary, "write", down)
    with pytest.raises(diary.DiaryUnavailable):
        export(db, report_id)


def test_a_released_report_is_not_changed_by_exporting_it(db):
    report_id = make_report(db)
    before = db.execute(
        "SELECT body, findings, status, released_by_name, released_at FROM reports "
        "WHERE report_id = %s", (report_id,)
    ).fetchone()
    export(db, report_id)
    after = db.execute(
        "SELECT body, findings, status, released_by_name, released_at FROM reports "
        "WHERE report_id = %s", (report_id,)
    ).fetchone()
    assert before == after
