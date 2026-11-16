"""Batches: the school year of the first card held for a learner (integration: needs DATABASE_URL).

A batch is a filing label, not a school rule, so it is a view over the cards and not a template
clause. The views run in a throwaway schema made from the migrations, so nothing live is touched.
Who may read which view is checked here from the catalogue, and again in
deploy/postgres/checks/roles.sql on the running stack.
"""

import psycopg
import pytest
from dbtools import make_document, make_learner, run_migrate
from psycopg.rows import dict_row

pytestmark = pytest.mark.integration


@pytest.fixture
def db(scratch_schema):
    schema, url = scratch_schema
    built = run_migrate(url)
    assert built.returncode == 0, built.stderr
    with psycopg.connect(url) as conn:
        conn.schema = schema
        yield conn
        conn.rollback()


_sha = iter(range(1, 10_000))


def q(db, sql, params=()):
    """A query whose rows come back as dicts (the shared helpers want tuples)."""
    return db.cursor(row_factory=dict_row).execute(sql, params)


def add_card(db, learner, year, stage):
    doc = make_document(db, status="done", sha256=f"{next(_sha):064x}")
    db.execute(
        "INSERT INTO report_cards (document_id, learner_id, academic_year, stage) "
        "VALUES (%s, %s, %s, %s)",
        (doc, learner, year, stage),
    )
    return doc


def learner(db, ref, name=None):
    who = make_learner(db, "TEST", ref)
    if name:
        db.execute("UPDATE learners SET display_name = %s WHERE learner_id = %s", (name, who))
    return who


def row(db, who):
    return q(db, "SELECT * FROM learner_batches WHERE learner_id = %s", (who,)).fetchone()


def test_the_batch_is_the_school_year_of_the_earliest_card_whatever_order_they_came_in(db):
    who = learner(db, "T/1")
    add_card(db, who, "2023/2024", "Form 3")  # uploaded first, but not the earliest year
    add_card(db, who, "2021/2022", "Form 1")
    add_card(db, who, "2022/2023", "Form 2")
    got = row(db, who)
    assert (got["batch"], got["first_stage"]) == ("2021/2022", "Form 1")
    assert (got["last_year"], got["last_stage"], got["cards"]) == ("2023/2024", "Form 3", 3)


def test_a_learner_who_repeats_a_stage_stays_in_the_batch_they_started_with(db):
    who = learner(db, "T/2")
    for year, stage in [("2021/2022", "Form 1"), ("2022/2023", "Form 2"),
                        ("2023/2024", "Form 2"), ("2024/2025", "Form 3")]:
        add_card(db, who, year, stage)
    got = row(db, who)
    assert got["batch"] == "2021/2022"
    assert (got["last_year"], got["last_stage"], got["cards"]) == ("2024/2025", "Form 3", 4)


def test_an_older_card_uploaded_later_moves_the_learner_to_an_earlier_batch(db):
    # A batch is derived from the cards held now, not stored. It is what the cards show, never an
    # enrolment date, so a missing early card that arrives later is allowed to change it.
    who = learner(db, "T/2b")
    first = add_card(db, who, "2022/2023", "Form 2")
    assert row(db, who)["batch"] == "2022/2023"
    add_card(db, who, "2020/2021", "Form 1")
    got = row(db, who)
    assert (got["batch"], got["first_stage"]) == ("2020/2021", "Form 1")
    assert q(db, "SELECT batch FROM card_batches WHERE document_id = %s", (first,)).fetchone() == {
        "batch": "2020/2021"
    }


def test_a_learner_whose_first_card_is_not_form_1_says_so_in_first_stage(db):
    who = learner(db, "T/3")
    add_card(db, who, "2023/2024", "Form 3")
    got = row(db, who)
    assert (got["batch"], got["first_stage"]) == ("2023/2024", "Form 3")


def test_a_learner_with_no_card_has_no_batch(db):
    who = learner(db, "T/4")
    assert row(db, who) is None


def test_two_learners_in_one_batch_and_one_in_another_are_told_apart(db):
    a, b, c = learner(db, "T/5"), learner(db, "T/6"), learner(db, "T/7")
    for who, year in [(a, "2021/2022"), (b, "2021/2022"), (c, "2022/2023")]:
        add_card(db, who, year, "Form 1")
    found = q(
        db, "SELECT learner_id FROM learner_batches WHERE batch = %s", ("2021/2022",)
    ).fetchall()
    assert {r["learner_id"] for r in found} == {a, b}


def test_the_assistants_view_has_exactly_these_columns_and_no_score(db):
    cols = [d.name for d in db.execute("SELECT * FROM learner_batches LIMIT 0").description]
    assert cols == ["learner_id", "school_ref", "display_name", "batch", "first_stage",
                    "last_year", "last_stage", "cards", "batch_note"]


def test_the_batch_note_states_the_first_card_and_that_it_is_not_an_enrolment_date(db):
    who = learner(db, "T/9")
    add_card(db, who, "2023/2024", "Form 3")
    assert row(db, who)["batch_note"] == (
        "The first report card on file for this learner is for Form 3, 2023/2024. "
        "Their batch is that school year; it is not a recorded enrolment date."
    )


def test_the_batch_note_follows_the_earliest_card_when_an_older_one_arrives(db):
    who = learner(db, "T/10")
    add_card(db, who, "2022/2023", "Form 2")
    add_card(db, who, "2020/2021", "Form 1")
    note = row(db, who)["batch_note"]
    assert "Form 1, 2020/2021" in note and "2022/2023" not in note


def test_the_batch_note_names_no_score_and_no_judgement(db):
    who = learner(db, "T/11")
    add_card(db, who, "2021/2022", "Form 1")
    note = row(db, who)["batch_note"].lower()
    for word in ("weak", "strong", "gifted", "remedial", "behind", "ahead", "rank", "average"):
        assert word not in note


def test_the_upload_pages_view_has_the_document_and_the_batch_and_no_learner(db):
    who = learner(db, "T/8", "Test Name")
    doc = add_card(db, who, "2021/2022", "Form 1")
    cols = [d.name for d in db.execute("SELECT * FROM card_batches LIMIT 0").description]
    assert cols == ["document_id", "batch"]
    got = q(db, "SELECT * FROM card_batches WHERE document_id = %s", (doc,)).fetchone()
    assert got == {"document_id": doc, "batch": "2021/2022"}


def test_the_upload_page_may_read_only_the_card_view_and_the_assistant_the_learner_view(db, roles):
    def role_exists(name):
        return q(db, "SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone()

    if not (role_exists(roles["ro"]) and role_exists(roles["portal"])):
        pytest.skip("the database roles are made by the init script; this database has none")

    def may(role, view):
        return q(
            db, "SELECT has_table_privilege(%s, %s, 'SELECT') AS ok",
            (role, f'"{db.schema}".{view}'),
        ).fetchone()["ok"]

    # The assistant's role is granted the learner view. (In the live database it can also read
    # every new object by default, so only this grant can be checked here, in a scratch schema.)
    assert may(roles["ro"], "learner_batches")
    # The upload page's role has no default: it reads card_batches and nothing else of these.
    assert may(roles["portal"], "card_batches")
    assert not may(roles["portal"], "learner_batches")
    assert not may(roles["portal"], "learner_first_card")
