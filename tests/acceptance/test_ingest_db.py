"""E-13 (no row stored), E-14 and E-15, the storing half (docs/TESTS.md, docs/step-09.md).

These run the real worker against a throwaway database schema: a document is put in the inbox and
in the `documents` table as the portal would, and `worker.run_once` reads it. Each test gets a fresh
schema with both rulebooks loaded, so what one test commits cannot touch another.

Needs DATABASE_URL, pdfplumber and the committed practice files. Skipped (and reported as skipped)
without a database.
"""

import json
import shutil
import uuid
from pathlib import Path

import psycopg
import pytest

pytest.importorskip("pdfplumber")

import tool_env  # noqa: E402

from mdeis.ingest import methodology, worker  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CARDS = ROOT / "data" / "synthetic" / "cards"
BAD = ROOT / "data" / "synthetic" / "cards_bad"
METHOD = ROOT / "data" / "synthetic" / "methodology"
EXPECTED = json.loads((BAD / "expected_failures.json").read_text(encoding="utf-8"))
SHEET_TABLES = ("learners", "report_cards", "card_subjects", "assessments")


@pytest.fixture
def db(templates_dir):
    url = tool_env.database_url()
    if not url:
        pytest.skip("DATABASE_URL is not set: the intake tests are skipped")
    name, scoped = tool_env.build_schema(url)
    try:
        tool_env.load_rulebooks(scoped, templates_dir)
        yield scoped
    finally:
        tool_env.drop_schema(url, name)


@pytest.fixture
def inbox(tmp_path):
    folder = tmp_path / "inbox"
    folder.mkdir()
    return folder


def add(url, inbox, source, kind="results", uploader="u-ada"):
    """Put a file in the inbox and record it as the portal would. Returns the document id."""
    import hashlib

    stored = uuid.uuid4().hex + Path(source).suffix
    shutil.copy(source, inbox / stored)
    digest = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    with psycopg.connect(url) as conn:
        document_id = conn.execute(
            "INSERT INTO documents (kind, original_name, stored_name, sha256, size_bytes, "
            "uploader_id, uploader_name) VALUES (%s, %s, %s, %s, %s, %s, 'Ada Teacher') "
            "RETURNING document_id",
            (kind, Path(source).name, stored, digest, Path(source).stat().st_size, uploader),
        ).fetchone()[0]
        conn.commit()
    return document_id


def run(url, inbox, document_id=None):
    return worker.run_once(url, inbox, only=document_id)


def document(url, document_id):
    with psycopg.connect(url) as conn:
        row = conn.execute(
            "SELECT status, failure_kind, failure_page, failure_row, duplicate_of, active "
            "FROM documents WHERE document_id = %s",
            (document_id,),
        ).fetchone()
    keys = ("status", "failure_kind", "page", "row", "duplicate_of", "active")
    return dict(zip(keys, row, strict=True))


def count(url, table):
    with psycopg.connect(url) as conn:
        return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608


def a_card_with(predicate):
    for pdf in sorted(CARDS.glob("*.pdf")):
        truth = json.loads(pdf.with_suffix(".truth.json").read_text(encoding="utf-8"))
        if predicate(truth):
            return pdf, truth
    pytest.fail("no practice card has what this test needs")


# --------------------------------------------------------------------------- a good card
def test_a_good_card_is_stored_with_a_page_and_row_on_every_score(db, inbox):
    pdf, truth = a_card_with(lambda t: any(None in r["terms"] for r in t["rows"]))
    document_id = add(db, inbox, pdf)
    summary = run(db, inbox)
    assert summary["status"] == "done" and summary["records"] == 3 * len(truth["rows"])
    assert document(db, document_id)["status"] == "done"
    assert [count(db, t) for t in SHEET_TABLES] == [1, 1, len(truth["rows"]), summary["records"]]
    with psycopg.connect(db) as conn:
        rows = conn.execute(
            "SELECT subject, term, score, raw_score, denominator, source_page, source_row, "
            "academic_year, stage, coefficient FROM assessments ORDER BY assessment_id"
        ).fetchall()
        learner = conn.execute("SELECT school_code, school_ref FROM learners").fetchone()
    assert learner == (truth["learner_ref"].split("/")[0], truth["learner_ref"])
    for got, want in zip(rows, truth["records"], strict=True):
        subject, term, score, raw, denominator, page, row, year, stage, coefficient = got
        place = (want["subject"], want["term"], want["page"], want["row"])
        assert (subject, term, page, row) == place
        shown = (year, stage, int(coefficient), int(denominator))
        assert shown == (want["year"], want["stage"], want["coefficient"], want["denominator"])
        if want["missing"]:
            assert score is None and raw in ("-", "ABS"), "a blank term is missing, never zero"
        else:
            assert str(score) == raw == want["raw_score"], "kept as printed"


def test_the_same_file_twice_adds_nothing(db, inbox):
    pdf, _ = a_card_with(lambda t: True)
    first, second = add(db, inbox, pdf), add(db, inbox, pdf, uploader="u-other")
    one = run(db, inbox, first)
    before = [count(db, t) for t in SHEET_TABLES]
    two = run(db, inbox, second)
    assert one["status"] == "done" and two["status"] == "done" and two["duplicate_of"] == first
    assert document(db, second)["duplicate_of"] == first
    assert [count(db, t) for t in SHEET_TABLES] == before


def test_a_different_card_for_the_same_learner_and_year_is_stored_as_well(db, inbox):
    one, _ = a_card_with(lambda t: t["card_id"].endswith("-2"))  # the second card of a learner-year
    both = sorted(CARDS.glob(one.name.split("_card2")[0] + "*.pdf"))
    assert len(both) == 2
    for pdf in both:
        add(db, inbox, pdf)
    while run(db, inbox):
        pass
    assert count(db, "report_cards") == 2 and count(db, "learners") == 1


# --------------------------------------------------------------------------- a card that fails
@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_a_broken_card_fails_with_its_kind_and_stores_no_row(db, inbox, name):
    want = EXPECTED[name]
    document_id = add(db, inbox, BAD / name)
    summary = run(db, inbox)
    assert summary["status"] == "failed"
    got = document(db, document_id)
    shown = (got["status"], got["failure_kind"], got["page"], got["row"])
    assert shown == ("failed", want["failure_kind"], want["page"], want["row"])
    assert [count(db, t) for t in SHEET_TABLES] == [0, 0, 0, 0]


def test_a_worker_that_breaks_half_way_leaves_no_partial_rows(db, inbox, monkeypatch):
    def breaks(conn, document_id, card):
        conn.execute("INSERT INTO learners (school_code, school_ref) VALUES ('OCC', 'half-done')")
        raise RuntimeError("the disk is full")

    monkeypatch.setattr(worker, "store_report_card", breaks)
    pdf, _ = a_card_with(lambda t: True)
    document_id = add(db, inbox, pdf)
    summary = run(db, inbox)
    got = document(db, document_id)
    assert summary["status"] == "failed" and got["failure_kind"] == worker.INTERNAL_ERROR
    assert count(db, "learners") == 0
    with psycopg.connect(db) as conn:
        detail = conn.execute("SELECT failure_detail FROM documents").fetchone()[0]
    assert "disk is full" not in detail, "the detail names the kind of error, never the message"


def test_a_file_that_is_not_what_was_uploaded_is_refused(db, inbox):
    pdf, _ = a_card_with(lambda t: True)
    document_id = add(db, inbox, pdf)
    for stored in inbox.iterdir():
        stored.write_bytes(stored.read_bytes() + b"changed")
    run(db, inbox)
    assert document(db, document_id)["failure_kind"] == worker.INTERNAL_ERROR
    assert count(db, "assessments") == 0


# --------------------------------------------------------------------------- claiming and recovery
def test_two_workers_do_not_take_the_same_document(db, inbox):
    pdf, _ = a_card_with(lambda t: True)
    add(db, inbox, pdf)
    with psycopg.connect(db) as a, psycopg.connect(db) as b:
        first = worker.claim(a)
        second = worker.claim(b)
    assert first is not None and second is None


def test_a_document_stuck_in_processing_is_put_back_once_then_failed(db, inbox):
    pdf, _ = a_card_with(lambda t: True)
    document_id = add(db, inbox, pdf)
    with psycopg.connect(db) as conn:
        worker.claim(conn)  # attempt 1, then the worker dies
        conn.execute("UPDATE documents SET started_at = now() - interval '1 hour'")
        conn.commit()
        assert worker.recover_stuck(conn) == 1
        assert document(db, document_id)["status"] == "pending"
        worker.claim(conn)  # attempt 2, and it dies again
        conn.execute("UPDATE documents SET started_at = now() - interval '1 hour'")
        conn.commit()
        assert worker.recover_stuck(conn) == 1
    got = document(db, document_id)
    assert (got["status"], got["failure_kind"]) == ("failed", worker.PARSE_TIMEOUT)


def test_a_reader_that_runs_too_long_fails_with_parse_timeout(db, inbox, monkeypatch):
    monkeypatch.setattr(worker, "PARSE_TIMEOUT_SECONDS", 0.001)
    pdf, _ = a_card_with(lambda t: True)
    document_id = add(db, inbox, pdf)
    run(db, inbox)
    assert document(db, document_id)["failure_kind"] == worker.PARSE_TIMEOUT
    assert count(db, "assessments") == 0


def test_every_document_leaves_one_diary_line_without_learner_names(db, inbox):
    pdf, truth = a_card_with(lambda t: True)
    good = add(db, inbox, pdf)
    bad = add(db, inbox, BAD / "no_id_number.pdf")
    run(db, inbox, good)
    run(db, inbox, bad)
    with psycopg.connect(db) as conn:
        lines = conn.execute(
            "SELECT outcome, inputs::text || outputs::text FROM action_log WHERE tool = 'ingestd' "
            "ORDER BY event_id"
        ).fetchall()
    assert [o for o, _ in lines] == ["ok", "refused"]
    for _, text in lines:
        assert truth["name"] not in text and truth["learner_ref"] not in text


# --------------------------------------------------------------------------- the methodology
def test_the_first_methodology_is_in_force_and_a_changed_one_waits_for_a_person(db, inbox):
    base = add(db, inbox, METHOD / "methodology-v2.0.md", kind="methodology")
    small = add(db, inbox, METHOD / "methodology-v2.1-small.md", kind="methodology")
    one = run(db, inbox, base)
    assert (one["status"], one["active"]) == ("done", True)
    two = run(db, inbox, small)
    assert (two["status"], two["active"]) == ("needs_review", False)
    assert document(db, base)["active"] is True and document(db, small)["active"] is False
    with psycopg.connect(db) as conn:
        found = conn.execute(
            "SELECT kind, ref_id FROM document_issues WHERE document_id = %s ORDER BY kind, ref_id",
            (small,),
        ).fetchall()
        sections = conn.execute(
            "SELECT count(*) FROM method_chunks WHERE document_id = %s AND section_id IS NOT NULL",
            (base,),
        ).fetchone()[0]
    assert found == [("referenced_section_changed", "TR-1"), ("section_changed", "6.4")]
    assert sections == 62


def test_a_methodology_waits_until_a_named_person_acknowledges_it(db, inbox):
    base = add(db, inbox, METHOD / "methodology-v2.0.md", kind="methodology")
    small = add(db, inbox, METHOD / "methodology-v2.1-small.md", kind="methodology")
    run(db, inbox, base)
    run(db, inbox, small)
    with psycopg.connect(db) as conn:
        with pytest.raises(methodology.AckRefused):
            methodology.acknowledge(conn, small, "  ", "no name given", db)
        with pytest.raises(methodology.AckRefused):
            methodology.acknowledge(conn, base, "Ada Teacher", "", db)  # already in force
        with pytest.raises(methodology.AckRefused):
            methodology.acknowledge(conn, "D-999999", "Ada Teacher", "", db)
        assert document(db, small)["active"] is False
        done = methodology.acknowledge(conn, small, "Ada Teacher", "read 6.4", db)
        conn.commit()
    assert isinstance(done["logged_event_id"], int)
    assert document(db, small)["active"] is True and document(db, base)["active"] is False
    with psycopg.connect(db) as conn:
        lines = conn.execute(
            "SELECT outcome, approver_name FROM action_log WHERE tool = 'ack_methodology' "
            "ORDER BY event_id"
        ).fetchall()
        who = conn.execute(
            "SELECT acknowledged_by FROM documents WHERE document_id = %s", (small,)
        ).fetchone()[0]
    refused, ok = ("refused", "Ada Teacher"), ("ok", "Ada Teacher")
    assert lines == [("refused", None), refused, refused, ok]
    assert who == "Ada Teacher"


def test_a_free_form_document_needs_review_and_is_not_in_force(db, inbox):
    free = add(db, inbox, METHOD / "methodology-free-form.md", kind="methodology")
    got = run(db, inbox, free)
    assert got["status"] == "needs_review" and got["active"] is False
    with psycopg.connect(db) as conn:
        kinds = [r[0] for r in conn.execute("SELECT kind FROM document_issues").fetchall()]
    assert "no_section_markers" in kinds


def test_without_an_embedder_the_sections_are_stored_without_vectors(db, inbox, monkeypatch):
    monkeypatch.delenv("EMBEDDER_URL", raising=False)
    base = add(db, inbox, METHOD / "methodology-v2.0.md", kind="methodology")
    run(db, inbox, base)
    with psycopg.connect(db) as conn:
        status = conn.execute(
            "SELECT embedding_status FROM documents WHERE document_id = %s", (base,)
        ).fetchone()[0]
    assert status == "skipped" and document(db, base)["status"] == "done"
