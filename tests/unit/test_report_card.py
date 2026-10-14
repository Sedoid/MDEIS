"""The card reader's logic on small hand-made pages (no PDF, no pdfplumber needed).

A page is a list of words, each with its text and position, as pdfplumber would give them. The
helper below puts them where layout OCC-1 puts them. The values are typed here, never taken from the
reader's output. The practice cards and the real sample are read in
tests/acceptance/test_report_cards.py.
"""

import pytest

from mdeis.ingest import report_card as rc

PAGE_TOP = 40  # where the school name sits, measured down from the top of the page
LINE = 13  # distance between two lines of a remark
WRAP = 6  # words to a line of the ANNUAL AVERAGE sentence
ANCHORS = {"coef": 140, "t1": 175, "t2": 217, "t3": 259, "annual": 308, "weighted": 366}


def word(text, x, base, size=9.5):
    return {
        "text": text,
        "x0": x,
        "x1": x + 0.4 * size * len(text),
        "top": base - 0.68 * size,
        "bottom": base + 0.22 * size,
    }


def header(ref="OCC/2024/0001", year="2024/2025", title_year=None):
    out = [
        word("OUTLIERS", 102, 70, 19),
        word("COMPREHENSIVE", 190, 70, 19),
        word("COLLEGE", 300, 70, 19),
    ]
    title = f"ANNUAL REPORT CARD · FORM 1 · ACADEMIC YEAR {title_year or year}"
    out += [word(t, 145 + 40 * i, 96, 11) for i, t in enumerate(title.split())]
    out += [word("Name:", 44, 130), word("Test", 72, 130), word("Learner", 95, 130)]
    out += [word("Date", 226, 130), word("of", 245, 130), word("birth:", 255, 130)]
    out += [word("1", 285, 130), word("May", 295, 130), word("2012", 320, 130)]
    out += [word("Gender:", 402, 130), word("Female", 435, 130)]
    if ref:
        out += [word("ID", 44, 166), word("number:", 56, 166), word(ref, 44, 178)]
    out += [word("Class:", 226, 166), word("Form", 252, 166), word("1", 275, 166)]
    out += [word("Academic", 402, 166), word("year:", 440, 166), word(year, 464, 166)]
    out += [word("Term", 201, 205, 8), word("scores", 222, 205, 8), word("/20", 250, 205, 8)]
    out += [word("Subject", 71, 230, 8), word("Coef.", 140, 220, 8), word("(C)", 143, 230, 8)]
    for i, x in enumerate((175, 217, 259), start=1):
        out += [word("Term", x, 240, 7.5), word(str(i), x + 18, 240, 7.5)]
    out += [word("Annual", 308, 215, 8), word("score", 306, 225, 8), word("/20", 329, 225, 8)]
    out += [word("Annual", 366, 215, 8), word("score", 363, 225, 8), word("×", 386, 225, 8)]
    out += [word("Grade", 425, 230, 8)]
    out += [word("Teacher's", 503, 220, 8), word("remarks", 505, 230, 8)]
    return out


def row(base, subject, coef, terms, annual, weighted, grade, remark, with_numbers=True):
    """One subject row: its numbers on one line, its remark centred on that line."""
    out = []
    cells = [str(coef), *terms, annual, weighted]
    if with_numbers:
        for x, text in zip(ANCHORS.values(), cells, strict=True):
            if text is not None:
                out.append(word(text, x + 3, base))
        for k, part in enumerate(subject.split()):
            out.append(word(part, 40, base + 12 * (k - (len(subject.split()) - 1) / 2), 9))
        out.append(word(grade, 433, base - 5))
        out.append(word("Good", 428, base + 4, 6.5))
    start = base - LINE * (len(remark) - 1) / 2
    for k, line in enumerate(remark):
        for j, text in enumerate(line.split()):
            out.append(word(text, 470 + 28 * j, start + LINE * k, 10))
    return out


def foot(base, total="30", weighted="100.00", average="12.50", grade="C", absences="2"):
    out = [word("TOTAL", 40, base), word(total, 146, base), word(weighted, 366, base)]
    out += [word("ANNUAL", 42, base + 40, 8.5), word("AVERAGE", 85, base + 40, 8.5)]
    sentence = f"Total of (annual score × C) ÷ total coefficients = {weighted} ÷ {total} = "
    sentence += f"{average} / 20 Grade: {grade} (Good)"
    words = sentence.split()
    for k in range(0, len(words), WRAP):  # the sentence wraps inside the box, left of the scale
        line = words[k : k + WRAP]
        out += [word(t, 42 + 36 * i, base + 54 + 11 * k // WRAP, 8.5) for i, t in enumerate(line)]
    out += [word("GRADING", 314, base + 40, 8.5), word("SCALE", 360, base + 40, 8.5)]
    scale = "18-20 A+ Excellent".split()
    out += [word(t, 314 + 30 * i, base + 54, 8.5) for i, t in enumerate(scale)]
    line = f"Absences: {absences} | Class council decision: Promoted to Form 2"
    out += [word(t, 36 + 40 * i, base + 130) for i, t in enumerate(line.split())]
    return out


def good_rows(base=300):
    return [
        row(base, "Mathematics", 4, ["14.00", "15.00", "16.00"], "15.00", "60.00", "B",
            ["Steady work this year."]),
        row(base + 60, "Literature in English", 2, ["10.00", "-", "ABS"], "-", "-", "-",
            ["Two terms are", "blank on this card."]),
    ]  # fmt: skip


def one_page(rows=None, **head):
    words = header(**head) + [w for r in (rows or good_rows()) for w in r] + foot(440)
    return [words]


def parse(pages):
    return rc.parse_pages(pages)


def test_a_clean_card_is_read_field_by_field():
    result = parse(one_page())
    assert result.failure is None
    card = result.card
    assert (card.academic_year, card.stage) == ("2024/2025", "Form 1")
    assert card.learner_ref == "OCC/2024/0001"
    assert (card.learner_name, card.class_name) == ("Test Learner", "Form 1")
    assert card.denominator == "20"
    assert (card.total_coefficients, card.total_weighted) == (30, "100.00")
    assert (card.annual_average, card.grade, card.absences) == ("12.50", "C", 2)
    assert card.council_decision == "Promoted to Form 2"
    first, second = card.rows
    assert (first.row, first.page, first.subject, first.coefficient) == (1, 1, "Mathematics", 4)
    assert first.terms == ("14.00", "15.00", "16.00")
    assert (first.annual_score, first.weighted_score, first.grade) == ("15.00", "60.00", "B")
    assert first.remark == "Steady work this year."
    assert second.subject == "Literature in English", "a wrapped name is joined"
    assert second.remark == "Two terms are blank on this card."


def test_a_blank_term_is_missing_and_never_zero():
    second = parse(one_page()).card.rows[1]
    assert second.terms == ("10.00", None, None)
    assert second.printed_terms == ("10.00", "-", "ABS"), "kept as the card printed it"
    assert (second.annual_score, second.weighted_score, second.grade) == (None, None, None)


def test_the_same_words_always_give_the_same_card():
    assert parse(one_page()).to_dict() == parse(one_page()).to_dict()


def test_a_row_with_a_missing_number_is_unreadable_at_its_page_and_row():
    rows = good_rows()
    rows[1] = [w for w in rows[1] if not (abs(w["x0"] - 369) < 1 and w["text"] == "-")]
    failure = parse(one_page(rows)).failure
    assert (failure.kind, failure.page, failure.row) == (rc.ROW_UNREADABLE, 1, 2)


def test_a_term_that_is_not_a_number_is_unreadable():
    rows = good_rows()
    rows[0] = [dict(w, text="ten") if w["text"] == "15.00" else w for w in rows[0]]
    failure = parse(one_page(rows)).failure
    assert failure.kind == rc.ROW_UNREADABLE and failure.row == 1


def test_no_id_number_and_a_title_year_that_differs_fail_with_their_own_kind():
    assert parse(one_page(ref=None)).failure.kind == rc.LEARNER_REF_MISSING
    failure = parse(one_page(title_year="2023/2024")).failure
    assert failure.kind == rc.YEAR_MISMATCH and failure.page == 1


def test_no_words_means_no_text_layer_and_no_title_means_an_unknown_layout():
    assert parse([[]]).failure.kind == rc.NO_TEXT_LAYER
    without_title = [w for w in one_page()[0] if not 85 < w["top"] < 100]
    assert parse([without_title]).failure.kind == rc.LAYOUT_UNRECOGNISED


def test_the_rest_of_a_remark_on_the_next_page_goes_back_to_the_row_before():
    first = good_rows()
    first[1] = row(360, "Physics", 2, ["11.00", "12.00", "13.00"], "12.00", "24.00", "C",
                   ["Reads well but", "needs more practice"])  # fmt: skip
    on_page_one = header() + [w for r in first for w in r]
    on_page_two = row(60, "", 0, [], "", "", "", ["with long answers."], with_numbers=False)
    on_page_two += foot(200)
    result = parse([on_page_one, on_page_two])
    assert result.failure is None, result.failure
    physics = result.card.rows[1]
    assert physics.remark == "Reads well but needs more practice with long answers."
    assert physics.remark_pages == (1, 2)
    assert result.card.rows[0].remark_pages == (1,)


def test_the_result_survives_the_json_trip_the_worker_makes():
    result = parse(one_page())
    assert rc.ParseResult.from_dict(result.to_dict()) == result
    failed = parse(one_page(ref=None))
    assert rc.ParseResult.from_dict(failed.to_dict()) == failed


def test_the_reader_holds_no_school_value_and_no_network_code():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(rc))
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "httpx"]
    assert "openrouter" not in inspect.getsource(rc).lower()


@pytest.mark.parametrize("text", ["-", "ABS"])
def test_both_blank_markers_are_accepted_in_a_term_cell(text):
    rows = good_rows()
    term_two = ANCHORS["t2"] + 3
    rows[0] = [dict(w, text=text) if abs(w["x0"] - term_two) < 1 else w for w in rows[0]]
    first = parse(one_page(rows)).card.rows[0]
    assert first.terms == ("14.00", None, "16.00") and first.printed_terms[1] == text


def test_an_annual_score_may_end_in_the_star_that_marks_an_average_of_the_terms_with_a_result():
    assert rc.cell_text("annual", "13.80*") == "13.80"
    assert rc.cell_text("annual", "14.40") == "14.40"
    assert rc.cell_text("t1", "ABS") == "ABS"
    assert rc.cell_text("t2", "14.00*") == "14.00*", "a star anywhere else is still not a number"
    assert rc.cell_text("weighted", "55.20*") == "55.20*"
