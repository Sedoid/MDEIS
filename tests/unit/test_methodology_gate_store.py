"""The gate against a real database (integration: needs DATABASE_URL).

Each test works inside the `conn` fixture's transaction, which is rolled back, so no live document
is touched. They check what the recorder tests cannot: that Postgres accepts the failed document
and its faults, that the previous methodology really stays in force, that the worker reports a
turned-back document as failed, and that the portal's queries return the faults.
"""

import pytest
from dbtools import make_document, one
from psycopg.rows import dict_row
from test_methodology_gate import FREE_FORM, doc

from mdeis.ingest import methodology as m
from mdeis.ingest import worker
from portal import store as portal_store

pytestmark = pytest.mark.integration

VAGUE = doc(section_2="The method must be suitable for the group.")


class AsDicts:
    """The portal store's queries want rows as dicts."""

    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, args=None):
        return self.conn.cursor(row_factory=dict_row).execute(sql, args)


@pytest.fixture(autouse=True)
def nothing_in_force(conn, monkeypatch):
    conn.execute("UPDATE documents SET active = false WHERE kind = 'methodology' AND active")
    monkeypatch.setattr(m, "template_refs", lambda c: {})


def ingest(conn, markdown, sha="1"):
    document_id = make_document(conn, kind="methodology", status="processing", sha256=sha * 64)
    return document_id, m.store_methodology(conn, document_id, m.parse_markdown(markdown))


def row(conn, document_id):
    return conn.execute(
        "SELECT status, failure_kind, failure_detail, active FROM documents WHERE document_id = %s",
        (document_id,),
    ).fetchone()


def issues(conn, document_id):
    return conn.execute(
        "SELECT kind, ref_id, page, detail FROM document_issues WHERE document_id = %s "
        "ORDER BY document_issue_id",
        (document_id,),
    ).fetchall()


def test_a_clear_document_is_done_and_in_force(conn):
    document_id, result = ingest(conn, doc())
    assert result["status"] == "done"
    assert row(conn, document_id) == ("done", None, None, True)
    assert issues(conn, document_id) == []


def test_a_vague_document_fails_with_its_faults_and_is_not_in_force(conn):
    document_id, result = ingest(conn, VAGUE)
    status, kind, detail, active = row(conn, document_id)
    assert (status, kind, active) == ("failed", "methodology_unclear", False) and detail
    found = issues(conn, document_id)
    assert [(k, ref) for k, ref, _, _ in found] == [("vague_term", "2")]
    assert found[0][2] is not None and "GT-4" in found[0][3]


def test_the_sections_of_a_turned_back_document_are_kept_for_the_uploader_to_read(conn):
    document_id, result = ingest(conn, VAGUE)
    count = one(conn, "SELECT count(*) FROM method_chunks WHERE document_id = %s", (document_id,))
    assert count == result["sections"] > 0


def test_a_turned_back_document_leaves_the_one_in_force_in_force(conn):
    first, _ = ingest(conn, doc(), sha="a")
    second, result = ingest(conn, VAGUE, sha="b")
    assert result["previous"] == first
    assert row(conn, first)[3] is True and row(conn, second)[3] is False
    assert {k for k, *_ in issues(conn, second)} == {"vague_term"}


def test_a_corrected_file_is_a_new_upload_and_goes_through(conn):
    _, bad = ingest(conn, VAGUE, sha="c")
    fixed, good = ingest(conn, doc(), sha="d")
    assert bad["status"] == "failed" and good["status"] == "done"
    assert row(conn, fixed)[3] is True


def test_a_free_form_document_is_turned_back_with_one_fault_on_no_section(conn):
    document_id, result = ingest(conn, FREE_FORM, sha="e")
    assert result["status"] == "failed"
    assert [(k, ref) for k, ref, _, _ in issues(conn, document_id)] == [("no_numbering", None)]


def test_a_clear_change_still_waits_for_a_named_person_as_before(conn):
    first, _ = ingest(conn, doc(), sha="a")
    second, result = ingest(conn, doc(section_2="MDEIS describes the records in plain words."),
                            sha="b")
    assert result["status"] == "needs_review"
    assert row(conn, first)[3] is True and row(conn, second)[3] is False
    assert [k for k, *_ in issues(conn, second)] == ["section_changed"]


# --------------------------------------------------------------------------- the worker
def test_the_worker_reports_a_turned_back_methodology_as_refused_and_a_clear_one_as_ok(
        conn, monkeypatch):
    def reader(markdown):
        return lambda module, path: ({"parsed": m.parse_markdown(markdown).to_dict()}, None)

    bad = make_document(conn, kind="methodology", status="processing", sha256="f" * 64)
    monkeypatch.setattr(worker, "_run_reader", reader(VAGUE))
    outcome, summary = worker._methodology(conn, {"document_id": bad}, "unused")
    assert outcome == "refused"
    assert (summary["status"], summary["failure_kind"]) == ("failed", "methodology_unclear")

    good = make_document(conn, kind="methodology", status="processing", sha256="9" * 64)
    monkeypatch.setattr(worker, "_run_reader", reader(doc()))
    outcome, summary = worker._methodology(conn, {"document_id": good}, "unused")
    assert (outcome, summary["status"]) == ("ok", "done")


# --------------------------------------------------------------------------- what the portal reads
def test_the_portal_reads_the_faults_and_lists_the_turned_back_upload(conn):
    document_id, _ = ingest(conn, VAGUE, sha="7")
    detail = portal_store.document_detail(AsDicts(conn), document_id)
    assert detail["failure_kind"] == "methodology_unclear" and detail["status"] == "failed"
    assert [(i["kind"], i["ref_id"]) for i in detail["issues"]] == [("vague_term", "2")]
    assert detail["issues"][0]["page"] is not None and "GT-4" in detail["issues"][0]["detail"]
    state = portal_store.methodology_state(AsDicts(conn))
    listed = {d["document_id"]: d for d in state["earlier"]}
    assert listed[document_id]["failure_kind"] == "methodology_unclear"
    assert state["in_force"] is None
