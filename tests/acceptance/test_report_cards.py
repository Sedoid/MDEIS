"""The report card reader against the truth sheets.

Every committed practice card has a truth sheet beside it, made when the card was drawn: each value
the card prints, with its page and row. The reader must return exactly that, field by field. The
real sample card (synthetic data in the real school's layout) has a truth sheet read by eye. The six
broken cards must each fail with their own typed failure at the right page and row.

Needs pdfplumber, and reads the committed files in data/synthetic, so it needs no generator.
"""

import json
import re
from pathlib import Path

import pytest

pytest.importorskip("pdfplumber")

from mdeis.ingest import report_card  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CARDS = ROOT / "data" / "synthetic" / "cards"
BAD = ROOT / "data" / "synthetic" / "cards_bad"
SAMPLE = ROOT / "data" / "synthetic" / "samples" / "amara_nfor_form1_2022-2023.pdf"
INGEST_SOURCE = ROOT / "src" / "mdeis" / "ingest"


def card_files():
    files = sorted(CARDS.glob("*.pdf")) + [SAMPLE]
    assert len(files) > 30, "the practice cards are missing: run `make practice-data`"
    return files


def truth_of(pdf):
    return json.loads(pdf.with_suffix(".truth.json").read_text(encoding="utf-8"))


def read(pdf):
    result = report_card.parse_report_card(pdf)
    assert result.failure is None, f"{pdf.name}: {result.failure}"
    return result.card


@pytest.mark.parametrize("pdf", card_files(), ids=lambda p: p.stem)
def test_a_card_reads_back_as_its_truth_sheet(pdf):
    card, truth = read(pdf), truth_of(pdf)
    assert card.academic_year == truth["academic_year"] == truth["title_year"]
    assert card.stage == truth["stage"]
    assert card.class_name == truth["class"]
    assert card.learner_ref == truth["learner_ref"]
    assert card.learner_name == truth["name"]
    assert card.school_name == truth.get("school", "OUTLIERS COMPREHENSIVE COLLEGE")
    assert card.denominator == "20"
    assert card.pages == truth["pages"]
    assert card.total_coefficients == truth["total_coefficients"]
    assert card.total_weighted == truth["total_weighted"]
    assert card.annual_average == truth["annual_average"]
    assert card.grade == truth["grade"]
    assert card.absences == truth["absences"]
    assert card.council_decision == truth["council_decision"]
    assert len(card.rows) == len(truth["rows"])
    for got, want in zip(card.rows, truth["rows"], strict=True):
        where = f"row {want['row']} {want['subject']}"
        assert got.row == want["row"], where
        assert got.subject == want["subject"], where
        assert got.coefficient == want["coefficient"], where
        assert list(got.terms) == want["terms"], where
        assert got.annual_score == want["annual_score"], where
        assert got.weighted_score == want["weighted_score"], where
        assert got.grade == want["grade"], where
        assert got.remark == want["remark"], where
        assert got.page == want["page"], where
        assert list(got.remark_pages) == want["remark_pages"], where


@pytest.mark.parametrize("pdf", card_files(), ids=lambda p: p.stem)
def test_every_term_score_has_its_page_and_row_and_a_blank_is_never_zero(pdf):
    card, truth = read(pdf), truth_of(pdf)
    if "records" not in truth:
        pytest.skip("this truth sheet lists rows, not records")
    flat = [
        (r.subject, term, printed, value, r.page, r.row)
        for r in card.rows
        for term, (printed, value) in enumerate(zip(r.printed_terms, r.terms, strict=True), start=1)
    ]
    assert len(flat) == len(truth["records"])
    for got, want in zip(flat, truth["records"], strict=True):
        subject, term, printed, value, page, row = got
        place = (want["subject"], want["term"], want["page"], want["row"])
        assert (subject, term, page, row) == place
        assert (value is None) == want["missing"], (subject, term)
        if want["missing"]:
            assert printed in report_card.BLANKS
        else:
            assert printed == want["raw_score"] == value, "kept exactly as printed"


def test_the_real_sample_card_has_the_numbers_the_school_printed():
    card = read(SAMPLE)
    assert len(card.rows) == 14
    assert sum(r.coefficient for r in card.rows) == card.total_coefficients == 30
    assert card.total_weighted == "456.00"
    assert (card.annual_average, card.grade, card.absences) == ("15.20", "B", 0)
    assert sum(len(r.terms) for r in card.rows) == 42
    assert {r.page for r in card.rows} == {1, 2, 3}, "rows on three pages"


def test_some_cards_have_a_remark_that_runs_over_the_page_break():
    carried = 0
    for pdf in card_files():
        for row in read(pdf).rows:
            if len(row.remark_pages) > 1:
                carried += 1
                assert row.remark_pages[0] == row.page
    assert carried >= 3, "the practice set is meant to include rows split across pages"


def test_the_same_card_read_twice_gives_the_same_result():
    pdf = card_files()[0]
    first, second = report_card.parse_report_card(pdf), report_card.parse_report_card(pdf)
    assert first.to_dict() == second.to_dict()


# --------------------------------------------------------------------------- strict reading
EXPECTED = json.loads((BAD / "expected_failures.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_a_broken_card_fails_with_its_own_kind_at_the_right_place(name):
    want = EXPECTED[name]
    result = report_card.parse_report_card(BAD / name)
    assert result.card is None, "a failed card returns no card, so nothing can be stored"
    failure = result.failure
    assert failure.kind == want["failure_kind"], failure
    assert failure.page == want["page"], failure
    assert failure.row == want["row"], failure
    assert failure.detail, "a failure says what is wrong in words"


def test_a_file_over_the_size_limit_is_refused_before_it_is_read(tmp_path, monkeypatch):
    big = tmp_path / "big.pdf"
    big.write_bytes(b"%PDF-" + b"0" * 100)
    monkeypatch.setattr(report_card, "MAX_BYTES", 50)
    assert report_card.parse_report_card(big).failure.kind == report_card.TOO_LARGE


def test_a_pdf_with_too_many_pages_is_refused(monkeypatch):
    monkeypatch.setattr(report_card, "MAX_PAGES", 1)
    two_pages = next(p for p in card_files() if truth_of(p)["pages"] == 2)
    assert report_card.parse_report_card(two_pages).failure.kind == report_card.TOO_MANY_PAGES


def test_the_reader_makes_no_network_call_and_has_no_model_in_it():
    for path in INGEST_SOURCE.glob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        assert not re.search(r"openrouter|httpx|requests\.|urllib", text), path.name
