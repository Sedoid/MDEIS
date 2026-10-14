"""What the upload page reads from the database (integration: needs DATABASE_URL).

The queries run in a throwaway schema made from the migrations, so no live document is touched.
The grant that lets the portal role read the stage and year is checked in
deploy/postgres/checks/roles.sql.
"""

import psycopg
import pytest
from dbtools import make_document, make_learner
from psycopg.rows import dict_row

from portal import store

pytestmark = pytest.mark.integration


class AsDicts:
    """The store's queries want rows as dicts; the test helpers want tuples."""

    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, args=None):
        return self.conn.cursor(row_factory=dict_row).execute(sql, args)


@pytest.fixture
def db(scratch_schema):
    """A fresh schema with every migration applied, so no live document is touched."""
    _, url = scratch_schema
    with psycopg.connect(url) as conn:
        yield conn
        conn.rollback()


def card(db, name, year="2023/2024", stage="Form 2", status="done", sha="c" * 64, who="u1"):
    doc = make_document(
        db, original_name=name, status=status, sha256=sha, uploader_id=who,
        **({"failure_kind": "row_unreadable"} if status == "failed" else {}),
    )
    if status == "done":
        learner = make_learner(db, "TEST", f"T/{name}")
        db.execute(
            "INSERT INTO report_cards (document_id, learner_id, academic_year, stage) "
            "VALUES (%s, %s, %s, %s)",
            (doc, learner, year, stage),
        )
    return doc


def names(rows):
    return [r["original_name"] for r in rows]


def test_a_card_that_was_read_carries_its_stage_and_year_and_one_that_was_not_does_not(db):
    card(db, "read.pdf", "2024/2025", "Form 4 Science", sha="1" * 64)
    make_document(db, original_name="waiting.pdf", status="pending", sha256="2" * 64)
    rows = {r["original_name"]: r for r in store.list_documents(AsDicts(db))}
    assert (rows["read.pdf"]["stage"], rows["read.pdf"]["academic_year"]) == (
        "Form 4 Science", "2024/2025")
    assert rows["waiting.pdf"]["stage"] is None and rows["waiting.pdf"]["academic_year"] is None


def test_a_failure_that_was_later_read_from_the_same_file_is_not_listed_any_more(db):
    card(db, "retried.pdf", sha="3" * 64, status="failed")
    card(db, "retried.pdf", sha="3" * 64)  # the same file, read after the reader was fixed
    card(db, "still-broken.pdf", sha="4" * 64, status="failed")
    rows = store.list_documents(AsDicts(db))
    assert [r["status"] for r in rows if r["original_name"] == "retried.pdf"] == ["done"]
    assert [r["status"] for r in rows if r["original_name"] == "still-broken.pdf"] == ["failed"]


def test_a_person_sees_only_their_own_files_and_an_administrator_sees_all(db):
    card(db, "mine.pdf", sha="5" * 64, who="u-me")
    card(db, "theirs.pdf", sha="6" * 64, who="u-other")
    assert names(store.list_documents(AsDicts(db), "u-me")) == ["mine.pdf"]
    assert {"mine.pdf", "theirs.pdf"} <= set(names(store.list_documents(AsDicts(db))))


def test_the_list_carries_no_learner_and_no_score(db):
    card(db, "x.pdf", sha="7" * 64)
    row = next(r for r in store.list_documents(AsDicts(db)) if r["original_name"] == "x.pdf")
    assert not {"learner_id", "printed_average", "stored_name", "sha256"} & set(row)


def test_no_methodology_yet(db):
    assert store.methodology_state(AsDicts(db)) == {"in_force": None, "waiting": [], "earlier": []}


def test_the_one_in_force_the_one_waiting_and_the_earlier_ones(db):
    old = make_document(db, kind="methodology", original_name="old.pdf", status="done",
                        sha256="8" * 64)
    now = make_document(db, kind="methodology", original_name="now.pdf", status="done",
                        sha256="9" * 64, active=True, acknowledged_by="Ada Teacher",
                        acknowledged_at="2026-10-14T10:00:00Z")
    new = make_document(db, kind="methodology", original_name="new.pdf", status="needs_review",
                        sha256="a" * 64)
    same = make_document(db, kind="methodology", original_name="again.pdf", status="done",
                         sha256="9" * 64, duplicate_of=now)
    state = store.methodology_state(AsDicts(db))
    assert state["in_force"]["document_id"] == now
    assert state["in_force"]["acknowledged_by"] == "Ada Teacher"
    assert [r["document_id"] for r in state["waiting"]] == [new]
    earlier = {r["document_id"]: r for r in state["earlier"]}
    assert set(earlier) == {old, same}
    assert earlier[same]["duplicate_of"] == now and earlier[old]["duplicate_of"] is None
