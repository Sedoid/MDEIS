"""T-030: migration 002, document intake tables and the version pins.

Marked `integration`: needs DATABASE_URL (a Postgres with pgvector, the app role).
"""

import pytest
from dbtools import make_document, make_learner, make_template, one, refused, vec
from psycopg import errors

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- documents
def test_a_document_gets_an_id_and_starts_pending(conn):
    doc = make_document(conn)
    assert doc.startswith("D-") and len(doc) >= 8
    row = conn.execute(
        "SELECT status, attempts, active, embedding_status FROM documents WHERE document_id = %s",
        (doc,),
    ).fetchone()
    assert row == ("pending", 0, False, "skipped")


def test_a_failed_document_must_say_why(conn):
    refused(
        conn,
        errors.CheckViolation,
        "INSERT INTO documents (kind, original_name, stored_name, sha256, size_bytes, uploader_id, "
        "status) VALUES ('results', 'x', 'y', repeat('a', 64), 1, 'u1', 'failed')",
    )
    doc = make_document(conn, status="failed", failure_kind="no_text_layer", failure_page=1)
    assert doc


def test_unknown_kind_status_and_hash_are_refused(conn):
    for column, value in (("kind", "poem"), ("status", "lost"), ("sha256", "abc")):
        with pytest.raises(errors.CheckViolation):
            with conn.transaction():
                make_document(conn, **{column: value})


def test_two_active_methodology_documents_cannot_exist(conn):
    make_document(conn, kind="methodology", active=True, status="done")
    with pytest.raises(errors.UniqueViolation):
        with conn.transaction():
            conn.execute(
                "INSERT INTO documents (kind, original_name, stored_name, sha256, size_bytes, "
                "uploader_id, active) "
                "VALUES ('methodology', 'y', 'y', repeat('a', 64), 1, 'u1', true)"
            )
    # Inactive ones can be many, and switching the active one over works in two steps.
    old = one(conn, "SELECT document_id FROM documents WHERE kind = 'methodology' AND active")
    new = make_document(conn, kind="methodology")
    conn.execute("UPDATE documents SET active = false WHERE document_id = %s", (old,))
    conn.execute("UPDATE documents SET active = true WHERE document_id = %s", (new,))


def test_only_a_methodology_can_be_active(conn):
    with pytest.raises(errors.CheckViolation):
        with conn.transaction():
            make_document(conn, kind="results", active=True)


def test_an_acknowledgement_names_a_person_and_a_time_together(conn):
    doc = make_document(conn, kind="methodology", status="needs_review")
    refused(
        conn,
        errors.CheckViolation,
        "UPDATE documents SET acknowledged_by = 'admin' WHERE document_id = %s",
        (doc,),
    )
    conn.execute(
        "UPDATE documents SET acknowledged_by = 'admin', acknowledged_at = now() "
        "WHERE document_id = %s",
        (doc,),
    )


def test_a_duplicate_points_at_the_first_upload(conn):
    first = make_document(conn)
    second = make_document(conn, duplicate_of=first, status="done")
    assert (
        one(conn, "SELECT duplicate_of FROM documents WHERE document_id = %s", (second,)) == first
    )
    refused(
        conn,
        errors.ForeignKeyViolation,
        "UPDATE documents SET duplicate_of = 'D-999999999' WHERE document_id = %s",
        (second,),
    )


def test_document_problems_record_page_and_row(conn):
    doc = make_document(conn, status="needs_review")
    conn.execute(
        'INSERT INTO document_issues (document_id, kind, ref_id, page, "row", detail) '
        "VALUES (%s, 'template_ref_missing', 'TR-1', 2, 14, 'section 6.4 not found')",
        (doc,),
    )
    assert one(conn, "SELECT count(*) FROM document_issues WHERE document_id = %s", (doc,)) == 1
    refused(
        conn,
        errors.CheckViolation,
        "INSERT INTO document_issues (document_id, kind) VALUES (%s, '')",
        (doc,),
    )


# --------------------------------------------------------------------------- methodology pieces
def test_methodology_pieces_are_ordered_and_can_be_searched(conn):
    doc = make_document(conn, kind="methodology", status="done")
    for i, section in enumerate(["6.3", "6.4", "6.5"]):
        conn.execute(
            "INSERT INTO method_chunks (document_id, section_id, title, page, ordinal, text, "
            "text_sha256, embedding) VALUES (%s, %s, 't', 1, %s, 'words', 'h', %s::vector)",
            (doc, section, i, vec(i)),
        )
    refused(
        conn,
        errors.UniqueViolation,
        "INSERT INTO method_chunks (document_id, ordinal, text, text_sha256) "
        "VALUES (%s, 1, 'again', 'h')",
        (doc,),
    )
    nearest = conn.execute(
        "SELECT section_id FROM method_chunks WHERE document_id = %s "
        "ORDER BY embedding <=> %s::vector LIMIT 1",
        (doc, vec(1)),
    ).fetchone()[0]
    assert nearest == "6.4"
    # The title block has no section id.
    conn.execute(
        "INSERT INTO method_chunks (document_id, ordinal, text, text_sha256) "
        "VALUES (%s, 99, 'title block', 'h')",
        (doc,),
    )


# --------------------------------------------------------------------------- report cards
def test_learners_are_unique_by_school_and_card_reference(conn):
    make_learner(conn, "SCH", "REF-1")
    refused(
        conn,
        errors.UniqueViolation,
        "INSERT INTO learners (school_code, school_ref) VALUES ('SCH', 'REF-1')",
    )
    make_learner(conn, "OTHER", "REF-1")  # same reference at another school is fine
    make_learner(conn, "SCH", None)  # synthetic learners have no card reference
    make_learner(conn, "SCH", None)


def test_date_of_birth_and_gender_are_not_stored_anywhere(conn):
    names = [
        r[0]
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema IN ('public', current_schema())"
        )
    ]
    for name in names:
        lowered = name.lower()
        assert "birth" not in lowered and "dob" not in lowered and "gender" not in lowered, name


def test_a_card_and_its_subjects_keep_what_was_printed(conn):
    learner = make_learner(conn, "SCH", "OCC/2022/0147")
    doc = make_document(conn)
    conn.execute(
        "INSERT INTO report_cards (document_id, learner_id, academic_year, stage, school_name, "
        "printed_average, printed_grade, absences, class_decision) "
        "VALUES (%s, %s, '2022/2023', 'Form 1', 'OCC', 13.43, 'C', 4, 'Promoted')",
        (doc, learner),
    )
    conn.execute(
        "INSERT INTO card_subjects "
        '(document_id, subject, coefficient, printed_annual, page, "row") '
        "VALUES (%s, 'Mathematics', 4, 14.00, 1, 3)",
        (doc,),
    )
    refused(
        conn,
        errors.UniqueViolation,
        "INSERT INTO card_subjects (document_id, subject) VALUES (%s, 'Mathematics')",
        (doc,),
    )
    refused(
        conn,
        errors.CheckViolation,
        "INSERT INTO report_cards (document_id, learner_id, academic_year, stage) "
        "VALUES (%s, %s, '2022-2023', 'Form 1')",
        (make_document(conn), learner),
    )


def card_record(conn, learner, doc, term, subject="Mathematics", **extra):
    cols = {
        "learner_id": learner,
        "subject": subject,
        "term": term,
        "score": 17,
        "raw_score": "17.00",
        "denominator": 20,
        "coefficient": 4,
        "academic_year": "2022/2023",
        "stage": "Form 1",
        "source_document_id": doc,
        "source_page": 1,
        "source_row": 3,
    }
    cols.update(extra)
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    return one(
        conn,
        f"INSERT INTO assessments ({names}) VALUES ({marks}) RETURNING assessment_id",
        list(cols.values()),
    )


def test_each_term_score_knows_its_card_page_and_row(conn):
    learner = make_learner(conn)
    doc = make_document(conn)
    record = card_record(conn, learner, doc, 1)
    row = conn.execute(
        "SELECT raw_score, source_document_id, source_page, source_row FROM assessments "
        "WHERE assessment_id = %s",
        (record,),
    ).fetchone()
    assert row == ("17.00", doc, 1, 3)


def test_running_the_worker_twice_cannot_add_the_same_term_twice(conn):
    learner = make_learner(conn)
    doc = make_document(conn)
    card_record(conn, learner, doc, 1)
    refused(
        conn,
        errors.UniqueViolation,
        "INSERT INTO assessments (learner_id, subject, term, source_document_id) "
        "VALUES (%s, 'Mathematics', 1, %s)",
        (learner, doc),
    )
    card_record(conn, learner, doc, 2)
    card_record(conn, learner, doc, 1, subject="English")
    # Another card for the same learner and year is kept (it becomes a duplicate_card issue).
    card_record(conn, learner, make_document(conn), 1)


def test_the_schema_fixes_neither_the_number_of_terms_nor_the_scale(conn):
    """These are rulebook values (CA-1, SC-1). The database only needs a term to be 1 or more."""
    learner = make_learner(conn)
    doc = make_document(conn)
    card_record(conn, learner, doc, 4, denominator=100, score=250)
    refused(
        conn,
        errors.CheckViolation,
        "INSERT INTO assessments (learner_id, subject, term) VALUES (%s, 'X', 0)",
        (learner,),
    )


def test_records_that_did_not_come_from_a_card_stay_valid(conn):
    learner = make_learner(conn)
    record = one(
        conn,
        "INSERT INTO assessments (learner_id, subject, score) VALUES (%s, 'X', 11) "
        "RETURNING assessment_id",
        (learner,),
    )
    assert (
        one(conn, "SELECT source_document_id FROM assessments WHERE assessment_id = %s", (record,))
        is None
    )


# --------------------------------------------------------------------------- the version pins
def pins(conn, report):
    return conn.execute(
        "SELECT template_stale, methodology_stale FROM report_pin_status WHERE report_id = %s",
        (report,),
    ).fetchone()


def test_a_report_remembers_its_versions_and_goes_out_of_date_when_they_change(conn):
    learner = make_learner(conn)
    v1 = make_template(conn, "pin-v1", active=True)
    method = make_document(conn, kind="methodology", active=True, status="done")
    report = one(
        conn,
        "INSERT INTO reports (learner_id, body, template_version, "
        "methodology_document_id) VALUES (%s, 'text', %s, %s) RETURNING report_id",
        (learner, v1, method),
    )
    assert pins(conn, report) == (False, False)

    # A newer rulebook is loaded and becomes the one in use.
    conn.execute("UPDATE templates SET active = false WHERE version_name = %s", (v1,))
    make_template(conn, "pin-v2", active=True)
    assert pins(conn, report) == (True, False)

    # A changed methodology is accepted.
    conn.execute("UPDATE documents SET active = false WHERE document_id = %s", (method,))
    make_document(conn, kind="methodology", active=True, status="done")
    assert pins(conn, report) == (True, True)

    # The report itself still names the versions it was written under.
    assert conn.execute(
        "SELECT template_version, methodology_document_id FROM reports WHERE report_id = %s",
        (report,),
    ).fetchone() == (v1, method)


def test_a_pin_must_point_at_a_real_version_and_a_real_document(conn):
    learner = make_learner(conn)
    refused(
        conn,
        errors.ForeignKeyViolation,
        "INSERT INTO reports (learner_id, body, template_version) VALUES (%s, 't', 'nope')",
        (learner,),
    )
    refused(
        conn,
        errors.ForeignKeyViolation,
        "INSERT INTO reports (learner_id, body, methodology_document_id) "
        "VALUES (%s, 't', 'D-999999999')",
        (learner,),
    )


# --------------------------------------------------------------------------- still locked after 002
def test_the_diary_is_still_append_only_after_migration_002(conn):
    conn.execute("INSERT INTO action_log (tool) VALUES ('after_002')")
    conn.execute("GRANT UPDATE, DELETE ON action_log TO CURRENT_USER")
    refused(conn, errors.RestrictViolation, "UPDATE action_log SET tool = 'x'")
    refused(conn, errors.RestrictViolation, "DELETE FROM action_log")
