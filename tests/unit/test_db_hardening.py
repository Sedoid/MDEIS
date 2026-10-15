"""Migration 003: the small fixes found in review.

Marked `integration`: needs DATABASE_URL. Run on the server with `make test`.
The rights of the read-only role are checked in test_db.py (live schema).
"""

import pytest
from dbtools import make_learner, one, refused
from psycopg import errors

pytestmark = pytest.mark.integration


def test_a_new_draft_report_is_pending_review(conn):
    learner = make_learner(conn)
    status = one(
        conn,
        "INSERT INTO reports (learner_id, body) VALUES (%s, 'x') RETURNING status",
        (learner,),
    )
    assert status == "pending_review"


def test_the_old_draft_status_is_no_longer_accepted(conn):
    learner = make_learner(conn)
    refused(
        conn,
        errors.CheckViolation,
        "INSERT INTO reports (learner_id, body, status) VALUES (%s, 'x', 'pending')",
        (learner,),
    )


def test_a_document_finding_has_a_ref_id_not_a_clause_id(conn):
    names = {
        r[0]
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'document_issues'"
        )
    }
    assert "ref_id" in names
    assert "clause_id" not in names


def test_the_diary_lock_belongs_to_one_schema(conn):
    body = one(conn, "SELECT pg_get_functiondef('action_log_chain'::regproc)")
    assert "current_schema()" in body
    # The rest of the diary's protection is untouched by 003.
    assert "action_log_hash" in body
    triggers = {
        r[0]
        for r in conn.execute(
            "SELECT tgname FROM pg_trigger WHERE tgrelid = 'action_log'::regclass "
            "AND NOT tgisinternal"
        )
    }
    assert triggers == {"action_log_chain", "action_log_no_change", "action_log_no_truncate"}
