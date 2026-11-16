"""The methodology reader's PDF layout rules (T-051 follow-up, 2026-11-16).

A PDF's headings are found from their size relative to the document's own body text and from their
words, not from absolute sizes or a font name. Two layouts must read the same way:

* the v2.0 sample: Georgia Bold, 23 pt title, 17 pt sections, 13 pt sub-sections, 11 pt body, and
  bold 11 pt lettered measures;
* the Aurora export: a variable font whose names never say Bold, 16 pt sections, 12.8 pt
  sub-sections, 11.2 pt body, 10.4 pt table text.

The first half builds pages of lines by hand (the shape `parse_pdf_lines` takes), so it needs no
PDF library. The second half reads the real PDF files and skips without `pdfplumber`.

Written before the new reader code. The tests of the first half that use the Aurora layout fail on
the old reader (it looked for 17 pt and 13 pt and for "Bold"), and pass once the reader uses
relative sizes.
"""

import json
from pathlib import Path

import pytest

from mdeis.ingest import methodology as m

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "data" / "synthetic" / "methodology"
REAL_V2_PDF = ROOT / "data" / "synthetic" / "samples" / "Methodology_of_mdeis.pdf"
AURORA_PDF = FIXTURES / "methodology-aurora.pdf"
AURORA_MD = FIXTURES / "methodology-aurora.md"

# The line sizes of the two layouts, in points.
AURORA = {"title": 19.0, "section": 16.0, "sub": 12.8, "body": 11.2, "table": 10.4}
GEORGIA = {"title": 23.0, "section": 17.0, "sub": 13.0, "body": 11.0, "measure": 11.0}


class Page:
    """Builds the lines of one page, top to bottom, the way `_page_lines` gives them."""

    def __init__(self):
        self.lines, self.y = [], 50.0

    def add(self, text, size, bold=False, gap=8.0):
        top = self.y + gap
        bottom = top + size * 1.2
        self.lines.append({"text": text, "bold": bold, "size": size, "top": top, "bottom": bottom})
        self.y = bottom
        return self

    def body(self, text, size, count=3):
        for n in range(count):
            self.add(f"{text} {n}", size, gap=2.0)
        return self


def ids(parsed):
    return [(s.id, s.level) for s in parsed.sections if s.id]


def titles(parsed):
    return {s.id: s.title for s in parsed.sections if s.id}


def text_of(parsed, section_id):
    return next(s.text for s in parsed.sections if s.id == section_id)


def aurora_pages():
    """An Aurora-like document: no bold anywhere, a title block, list items, a table."""
    a = AURORA
    page = Page()
    page.add("LUCENT Evaluation Methodology", a["title"])
    page.add("Aurora Comprehensive and Upper Secondary School: a configuration", a["section"])
    page.body("Status. A fictional school.", a["body"])
    page.add("1. Purpose", a["section"], gap=14.0)
    page.body("This document explains how records are read.", a["body"])
    page.add("2. Evaluation process", a["section"], gap=14.0)
    page.add("LUCENT follows these steps for every evaluation:", a["body"])
    page.add("1. Understand the request. Identify the question.", a["body"], gap=6.0)
    page.add("2. Confirm the configuration. Check the version.", a["body"], gap=6.0)
    page.add("3. Retrieve records. Collect only authorised records.", a["body"], gap=6.0)
    page.add("3. Principles", a["section"], gap=14.0)
    page.body("Evidence first.", a["body"])
    page.add("4. School configuration", a["section"], gap=14.0)
    page.add("1 Institution and context Aurora School", a["table"], gap=3.0)
    page.add("2 Analysis design Longitudinal", a["table"], gap=3.0)
    page.body("Every change needs a new version.", a["body"])
    page.add("6. Calculation rules", a["section"], gap=14.0)  # not the next number: 5 is missing
    page.add("5. Evidence", a["section"], gap=14.0)
    page.body("Eligible evidence is listed.", a["body"])
    page.add("6. Calculation rules", a["section"], gap=14.0)
    page.add("6.1 No averaging", a["sub"], gap=10.0)
    page.body("Aurora defines no averages.", a["body"])
    page.add("6.2 Grade scale (illustrative)", a["sub"], gap=10.0)
    page.body("The scale runs from 4 to 10.", a["body"])
    page.add("Appendix A. Rule register", a["section"], gap=14.0)
    page.body("Rules are listed.", a["body"])
    return [page.lines]


def georgia_pages():
    """A v2.0-like document: bold headings, a Step, lettered bold measures, a bullet."""
    g = GEORGIA
    page = Page()
    page.add("MDEIS", g["title"], bold=True)
    page.add("Simple Evaluation Methodology and Assessment Framework", g["section"], bold=True)
    page.add("1. Purpose", g["section"], bold=True, gap=14.0)
    page.body("This document explains how records are read.", g["body"], count=6)
    page.add("2. Main principles", g["section"], bold=True, gap=14.0)
    page.add("2.1 Use evidence", g["sub"], bold=True, gap=10.0)
    page.body("Findings rest on records.", g["body"])
    page.add("3. Evaluation settings", g["section"], bold=True, gap=14.0)
    page.body("Settings are chosen first.", g["body"])
    page.add("4. Standard evaluation process", g["section"], bold=True, gap=14.0)
    page.add("Step 1: Understand the request", g["sub"], bold=True, gap=10.0)
    page.body("The system identifies the request.", g["body"])
    page.add("5. Evaluation type A", g["section"], bold=True, gap=14.0)
    page.add("5.3 Main measures", g["sub"], bold=True, gap=10.0)
    page.add("MDEIS may calculate the following.", g["body"])
    page.add("A. Proficiency distribution", g["measure"], bold=True, gap=10.0)
    page.body("This shows the percentage of learners.", g["body"])
    page.add("B. Performance by area", g["measure"], bold=True, gap=10.0)
    page.body("This shows results for areas.", g["body"])
    return [page.lines]


# --------------------------------------------------------------------------- the body size
def test_the_body_size_is_the_size_most_characters_are_set_in():
    pages = [[
        {"text": "x" * 100, "bold": False, "size": 11.2, "top": 0, "bottom": 5},
        {"text": "y" * 30, "bold": False, "size": 10.4, "top": 6, "bottom": 9},
        {"text": "z" * 10, "bold": False, "size": 16.0, "top": 10, "bottom": 20},
    ]]
    assert m.body_size(pages) == 11.2


def test_the_body_size_counts_characters_not_lines():
    long = {"text": "x" * 200, "bold": False, "size": 11.0, "top": 0, "bottom": 5}
    short = [{"text": "yy", "bold": False, "size": 9.0, "top": i, "bottom": i} for i in range(20)]
    assert m.body_size([[long] + short]) == 11.0


def test_a_tie_in_the_body_size_goes_to_the_smaller_size_whatever_the_order():
    one = {"text": "x" * 10, "bold": False, "size": 12.0, "top": 0, "bottom": 1}
    two = {"text": "y" * 10, "bold": False, "size": 11.0, "top": 2, "bottom": 3}
    assert m.body_size([[one, two]]) == m.body_size([[two, one]]) == 11.0


def test_a_document_with_no_text_has_body_size_zero_and_no_heading():
    assert m.body_size([[]]) == 0.0
    assert m.body_size([[{"text": " ", "bold": False, "size": 12.0, "top": 0, "bottom": 1}]]) == 0.0
    parsed = m.parse_pdf_lines([[]])
    assert not any(s.id for s in parsed.sections)


# --------------------------------------------------------------------------- the Aurora layout
def test_aurora_layout_sections_and_sub_sections_are_found_with_no_bold_and_other_sizes():
    parsed = m.parse_pdf_lines(aurora_pages())
    assert ids(parsed) == [
        ("1", 1), ("2", 1), ("3", 1), ("4", 1), ("5", 1), ("6", 1), ("6.1", 2), ("6.2", 2),
    ]
    assert titles(parsed)["6.1"] == "No averaging"
    assert titles(parsed)["6.2"] == "Grade scale (illustrative)"
    assert not any(line["bold"] for line in aurora_pages()[0])


def test_aurora_layout_numbered_list_items_in_a_section_stay_text():
    parsed = m.parse_pdf_lines(aurora_pages())
    text = text_of(parsed, "2")
    assert "1. Understand the request." in text and "3. Retrieve records." in text
    found = [s.id for s in parsed.sections if s.id]
    assert found.count("3") == 1, "the list item 3 is not section 3"
    assert titles(parsed)["3"] == "Principles"


def test_aurora_layout_the_title_block_is_kept_as_it_was():
    parsed = m.parse_pdf_lines(aurora_pages())
    first = parsed.sections[0]
    assert first.id is None and first.level == 0 and first.page == 1
    assert "LUCENT Evaluation Methodology" in first.text
    assert "Aurora Comprehensive and Upper Secondary School" in first.text


def test_aurora_layout_a_heading_sized_line_with_no_number_is_text_not_a_section():
    parsed = m.parse_pdf_lines(aurora_pages())
    assert "Appendix A. Rule register" in text_of(parsed, "6.2")
    assert len([s for s in parsed.sections if s.id]) == 8


def test_aurora_layout_an_out_of_sequence_number_at_heading_size_is_text():
    parsed = m.parse_pdf_lines(aurora_pages())
    assert "6. Calculation rules" in text_of(parsed, "4"), (
        "a 6 right after 4 is not the next number, so it stays in section 4")


def test_aurora_layout_table_text_smaller_than_the_body_is_not_a_heading():
    parsed = m.parse_pdf_lines(aurora_pages())
    assert "1 Institution and context Aurora School" in text_of(parsed, "4")


# --------------------------------------------------------------------------- the v2.0 layout
def test_georgia_layout_reads_as_it_did_before():
    parsed = m.parse_pdf_lines(georgia_pages())
    assert ids(parsed) == [
        ("1", 1), ("2", 1), ("2.1", 2), ("3", 1), ("4", 1), ("4.S1", 2), ("5", 1), ("5.3", 2),
        ("5.3.A", 3), ("5.3.B", 3),
    ]
    assert titles(parsed)["4.S1"] == "Understand the request"
    assert titles(parsed)["5.3.A"] == "Proficiency distribution"
    first = parsed.sections[0]
    assert first.id is None and "Simple Evaluation Methodology" in first.text


def test_a_lettered_measure_needs_the_font_to_say_bold_a_plain_one_is_text():
    pages = georgia_pages()
    for line in pages[0]:
        if line["text"].startswith("B. "):
            line["bold"] = False
    parsed = m.parse_pdf_lines(pages)
    assert "5.3.B" not in [s.id for s in parsed.sections]
    assert "B. Performance by area" in text_of(parsed, "5.3.A")


def test_a_lettered_measure_is_bold_at_body_size_and_is_level_3():
    parsed = m.parse_pdf_lines(georgia_pages())
    assert [(s.id, s.level) for s in parsed.sections if s.id and s.id.count(".") == 2] == [
        ("5.3.A", 3), ("5.3.B", 3)]


# --------------------------------------------------------------------------- how clear the size is
def lines_with_heading_size(size):
    page = Page()
    page.body("Body text goes here.", 10.0, count=6)
    page.add("1. Purpose", size, gap=14.0)
    page.body("More body text.", 10.0)
    return [page.lines]


def test_a_line_one_tenth_larger_than_the_body_is_a_heading():
    parsed = m.parse_pdf_lines(lines_with_heading_size(10.0 * m.HEADING_MIN_RATIO))
    assert [s.id for s in parsed.sections if s.id] == ["1"]


def test_a_line_only_a_little_larger_than_the_body_is_not_a_heading():
    parsed = m.parse_pdf_lines(lines_with_heading_size(10.5))
    assert [s.id for s in parsed.sections if s.id] == []


def test_no_absolute_size_and_no_font_name_rule_is_left_in_the_reader():
    source = (ROOT / "src" / "mdeis" / "ingest" / "methodology.py").read_text(encoding="utf-8")
    for gone in ("SECTION_SIZE", "SUB_SIZE", "TITLE_SIZE"):
        assert gone not in source, gone
    assert not hasattr(m, "SECTION_SIZE") and not hasattr(m, "SUB_SIZE")
    assert 0 < m.SIZE_TOL < 1 and m.HEADING_MIN_RATIO > 1


# --------------------------------------------------------------------------- wrapped headings
def test_a_sub_heading_that_wraps_onto_a_second_line_is_one_title():
    g = GEORGIA
    page = Page()
    page.add("1. Purpose", g["section"], bold=True)
    page.body("Text goes here.", g["body"], count=6)
    page.add("1.1 A long title that is set over", g["sub"], bold=True, gap=10.0)
    page.add("two lines", g["sub"], bold=True, gap=1.0)
    page.body("More text goes here.", g["body"], count=6)
    parsed = m.parse_pdf_lines([page.lines])
    assert titles(parsed)["1.1"] == "A long title that is set over two lines"
    assert "two lines" not in text_of(parsed, "1.1")


def test_a_section_heading_that_wraps_onto_a_second_line_is_one_title_in_any_font():
    a = AURORA
    page = Page()
    page.body("Body text goes here.", a["body"], count=6)
    page.add("1. A long section title that is set over", a["section"], gap=14.0)
    page.add("two lines", a["section"], gap=1.0)
    page.body("More text.", a["body"])
    parsed = m.parse_pdf_lines([page.lines])
    assert titles(parsed)["1"] == "A long section title that is set over two lines"


def test_a_new_numbered_heading_right_under_another_is_not_taken_for_a_wrap():
    a = AURORA
    page = Page()
    page.body("Body text goes here.", a["body"], count=6)
    page.add("1. Purpose", a["section"], gap=14.0)
    page.add("2. Process", a["section"], gap=1.0)
    parsed = m.parse_pdf_lines([page.lines])
    assert ids(parsed) == [("1", 1), ("2", 1)]


def test_a_sub_heading_under_its_section_heading_is_not_taken_for_a_wrap():
    a = AURORA
    page = Page()
    page.body("Body text goes here.", a["body"], count=6)
    page.add("1. Rules", a["section"], gap=14.0)
    page.add("1.1 No averaging", a["sub"], gap=1.0)
    parsed = m.parse_pdf_lines([page.lines])
    assert ids(parsed) == [("1", 1), ("1.1", 2)]
    assert titles(parsed) == {"1": "Rules", "1.1": "No averaging"}


def test_the_same_lines_read_twice_give_the_same_sections():
    assert m.parse_pdf_lines(aurora_pages()) == m.parse_pdf_lines(aurora_pages())


# --------------------------------------------------------------------------- tables in a PDF
class FakeRow:
    def __init__(self, bbox, cells=None):
        self.bbox = bbox
        if cells is not None:  # the boxes of the row's cells, left to right (None for a gap)
            self.cells = cells


class FakeTable:
    """What pdfplumber's find_tables() gives: a box, the rows' boxes and the cells' text."""

    def __init__(self, bbox, cells, row_boxes=None):
        self.bbox = bbox
        self._cells = cells
        if row_boxes is not None:
            self.rows = [FakeRow(b) for b in row_boxes]

    def extract(self):
        return self._cells


class FakePage:
    def __init__(self, words, tables=()):
        self.words, self.tables = words, list(tables)

    def extract_words(self, **kwargs):
        return list(self.words)

    def find_tables(self):
        return self.tables


def word(text, x0, top, size=11.2, fontname="AAAAAC+.SFUI-Regular_wght2BC0000"):
    width = len(text) * size * 0.5
    return {"text": text, "x0": x0, "x1": x0 + width, "top": top, "bottom": top + size * 1.2,
            "size": size, "fontname": fontname}


def settings_table_page():
    """A line of text, then a settings table drawn with borders, then a line of text.

    pdfplumber reads the cells word by word, so the first column's wrapped name and the value text
    interleave. The page is the words as pdfplumber gives them, in text-flow order.
    """
    words = [
        word("Every", 50, 20), word("change", 90, 20), word("needs", 140, 20),
        # header row
        word("#", 52, 60, 10.4), word("Setting", 80, 60, 10.4), word("Value", 220, 60, 10.4),
        # row 1: the name wraps over two lines and the value text sits between them
        word("1", 52, 80, 10.4), word("Institution", 80, 80, 10.4), word("Aurora", 220, 80, 10.4),
        word("and", 80, 93, 10.4), word("context", 100, 93, 10.4), word("School", 220, 93, 10.4),
        # row 2
        word("2", 52, 120, 10.4), word("Analysis", 80, 120, 10.4), word("design", 130, 120, 10.4),
        word("Longitudinal", 220, 120, 10.4),
        word("After", 50, 160), word("the", 90, 160), word("table.", 120, 160),
    ]
    cells = [
        ["#", "Setting", "Value"],
        ["1", "Institution\nand context", "Aurora\nSchool"],
        ["2", "Analysis design", "Longitudinal"],
    ]
    boxes = [(50, 55, 400, 75), (50, 76, 400, 112), (50, 114, 400, 135)]
    table = FakeTable((50, 55, 400, 135), cells, boxes)
    return FakePage(words, [table])


def test_a_table_is_read_as_one_markdown_line_per_row_in_place_of_its_raw_lines():
    lines = m._page_lines(settings_table_page())
    assert [line["text"] for line in lines] == [
        "Every change needs",
        "| # | Setting | Value |",
        "| 1 | Institution and context | Aurora School |",
        "| 2 | Analysis design | Longitudinal |",
        "After the table.",
    ]
    assert [line.get("table", False) for line in lines] == [False, True, True, True, False]


def test_a_table_row_joins_the_inner_line_breaks_and_runs_of_spaces_with_one_space():
    page = FakePage(
        [word("x", 52, 60, 10.4)],
        [FakeTable((50, 55, 400, 75), [["x", "a\n b\n", "c   d"]])],
    )
    assert [line["text"] for line in m._page_lines(page)] == ["| x | a b | c d |"]


def test_an_empty_cell_stays_in_a_row_when_its_column_is_not_empty_in_every_row():
    page = FakePage(
        [word("x", 52, 60, 10.4), word("y", 52, 90, 10.4)],
        [FakeTable((50, 55, 400, 105), [["x", "", "a"], ["y", "m", None]])],
    )
    texts = [line["text"] for line in m._page_lines(page)]
    assert texts == ["| x |  | a |", "| y | m |  |"]


def aurora_page_3_table():
    """The shape the real Aurora PDF gives: five columns for three, an empty edge on each side,
    an empty first row, and a row that is only empty cells."""
    cells = [
        ["", "", "", "", ""],
        ["", "", "", "Value", ""],
        ["", "1", "Institution and context", "Aurora Comprehensive\nSchool (fictional)", ""],
        [None, "2", "Analysis design", "Longitudinal analysis", None],
        ["", "", "", "", ""],
        ["", "6", "Assessment rules", "See section 5", ""],
    ]
    boxes = [(50, 55 + 20 * i, 400, 74 + 20 * i) for i in range(6)]
    words = [word("1", 70, 95, 10.4), word("Institution", 100, 95, 10.4),
             word("2", 70, 115, 10.4), word("Analysis", 100, 115, 10.4),
             word("6", 70, 155, 10.4), word("Assessment", 100, 155, 10.4)]
    return FakePage(words, [FakeTable((50, 55, 400, 175), cells, boxes)])


def test_a_column_that_is_empty_in_every_row_is_dropped_and_so_is_a_row_that_is_then_empty():
    lines = m._page_lines(aurora_page_3_table())
    assert [line["text"] for line in lines] == [
        "|  |  | Value |",
        "| 1 | Institution and context | Aurora Comprehensive School (fictional) |",
        "| 2 | Analysis design | Longitudinal analysis |",
        "| 6 | Assessment rules | See section 5 |",
    ]


def test_the_rows_that_are_kept_keep_their_own_place_after_empty_rows_are_dropped():
    lines = m._page_lines(aurora_page_3_table())
    assert [(line["top"], line["bottom"]) for line in lines] == [
        (75, 94), (95, 114), (115, 134), (155, 174)]


def test_a_table_that_is_empty_everywhere_gives_no_line():
    page = FakePage([word("x", 52, 60, 10.4)],
                    [FakeTable((50, 55, 400, 75), [["", None], [None, ""]])])
    assert m._page_lines(page) == []


def test_a_ragged_table_with_shorter_rows_is_padded_not_refused():
    page = FakePage([word("x", 52, 60, 10.4)],
                    [FakeTable((50, 55, 400, 95), [["a", "b", "c"], ["d"]])])
    assert [line["text"] for line in m._page_lines(page)] == ["| a | b | c |", "| d |  |  |"]


def test_the_aurora_table_shape_gives_lines_the_gate_reads_as_settings():
    from mdeis.ingest import methodology_gate as gate

    lines = m._page_lines(aurora_page_3_table())
    lines.insert(0, {"text": "1. Configuration", "bold": False, "size": 16.0,
                     "top": 0.0, "bottom": 10.0})
    section = next(s for s in m.parse_pdf_lines([lines]).sections if s.id == "1")
    found = gate.check([section], {}, gate.load_gate())
    named = " ".join(f.needed for f in found if f.rule == "GT-1")
    assert "Institution and educational context" not in named and "Evaluation type" not in named
    assert "Assessment rules" not in named and "Learner or group" in named


def test_a_table_line_has_the_tables_own_size_not_bold_and_the_rows_own_place():
    lines = m._page_lines(settings_table_page())
    rows = [line for line in lines if line.get("table")]
    assert {line["size"] for line in rows} == {10.4} and not any(line["bold"] for line in rows)
    assert [(line["top"], line["bottom"]) for line in rows] == [(55, 75), (76, 112), (114, 135)]
    assert all(a["bottom"] <= b["top"] for a, b in zip(rows, rows[1:], strict=False))


def test_a_table_without_row_boxes_still_reads_in_row_order():
    page = settings_table_page()
    page.tables[0] = FakeTable(page.tables[0].bbox, page.tables[0].extract())
    texts = [line["text"] for line in m._page_lines(page)]
    assert texts[1:4] == ["| # | Setting | Value |",
                          "| 1 | Institution and context | Aurora School |",
                          "| 2 | Analysis design | Longitudinal |"]


def test_a_page_with_no_table_reads_exactly_as_before():
    words = [word("1.", 50, 20, 16.0), word("Purpose", 80, 20, 16.0),
             word("Some", 50, 60), word("text", 90, 60), word("here.", 130, 60)]
    lines = m._page_lines(FakePage(words))
    assert [line["text"] for line in lines] == ["1. Purpose", "Some text here."]
    assert [line["size"] for line in lines] == [16.0, 11.2]
    assert all(not line.get("table") for line in lines)
    assert lines[0]["bold"] is False


def test_words_outside_the_table_box_are_kept_and_words_inside_it_are_not_read_twice():
    texts = [line["text"] for line in m._page_lines(settings_table_page())]
    assert "Institution" not in " ".join(t for t in texts if not t.startswith("|"))
    assert texts.count("| 1 | Institution and context | Aurora School |") == 1


def test_a_table_row_is_never_read_as_a_heading_even_when_its_text_is_large():
    sentence = "This is the body text of the page".split()
    body = [word(w, 50 + 40 * i, 20) for i, w in enumerate(sentence)]
    big = [word("1.", 52, 80, 18.0), word("Purpose", 80, 80, 18.0)]
    page = FakePage(body + big, [FakeTable((50, 70, 400, 110), [["1. Purpose"]])])
    parsed = m.parse_pdf_lines([m._page_lines(page)])
    assert not any(s.id for s in parsed.sections)
    assert "| 1. Purpose |" in parsed.sections[0].text


def sectioned_table_page():
    """The table page under a section heading, with some plain body text after it."""
    page = settings_table_page()
    heading = [word("1.", 50, 2, 16.0), word("School", 80, 2, 16.0),
               word("configuration", 140, 2, 16.0)]
    sentence = "This is plain body text that fills".split()
    filler = [word(w, 50 + 40 * i, 190 + 14 * row)
              for row in range(3) for i, w in enumerate(sentence)]
    return FakePage(heading + page.words + filler, page.tables)


def test_a_table_in_a_section_is_text_of_that_section_and_a_blank_line_separates_it():
    parsed = m.parse_pdf_lines([m._page_lines(sectioned_table_page())])
    assert [(s.id, s.title) for s in parsed.sections if s.id] == [("1", "School configuration")]
    text = parsed.sections[-1].text.split("\n")
    first_row = text.index("| # | Setting | Value |")
    assert text[first_row - 1] == "", "a blank line before the table, as in Markdown"
    assert text[first_row + 3] == "" and text[first_row + 4] == "After the table."


def test_the_settings_of_a_pdf_table_are_defined_for_the_gate_as_in_markdown():
    from mdeis.ingest import methodology_gate as gate

    parsed = m.parse_pdf_lines([m._page_lines(sectioned_table_page())])
    section = next(s for s in parsed.sections if s.id == "1")
    assert "| 1 | Institution and context | Aurora School |" in section.text
    found = gate.check([section], {}, gate.load_gate())
    named = " ".join(f.needed for f in found if f.rule == "GT-1")
    assert "Institution and educational context" not in named, "the alias is found in the row"
    assert "Evaluation type" not in named, "'Analysis design' is its alias"
    assert "Learner or group" in named, "a setting the table does not hold is still missing"


# --------------------------------------------------------------------------- a row split by a page
# The Aurora settings table: three real columns (number, setting, value) between two thin empty
# columns that pdfplumber also reports. Row 13 is cut by the page break, so the next page starts
# with a table whose first row holds only the end of the name.
PAGE_3_COLUMNS = [(50, 52), (52, 80), (80, 220), (220, 400), (400, 402)]
PAGE_4_COLUMNS = [(52, 80), (80, 220), (220, 400)]
ROW_13 = ["", "13", "Reporting, roles and", "See sections 11 and 12", ""]


def table_page(rows, columns, geometry=True, lead=None, tail=None):
    """A fake page: optional text above, a table of `rows` (a 20 pt box each), optional text below.

    Each non-empty cell has one word in it. With `geometry` the rows also give their cells' boxes.
    """
    words, boxes = [], []
    for i, row in enumerate(rows):
        top, bottom = 55 + 20 * i, 74 + 20 * i
        cell_boxes = [(x0, top, x1, bottom) for x0, x1 in columns]
        boxes.append(FakeRow((columns[0][0], top, columns[-1][1], bottom),
                             cell_boxes if geometry else None))
        words += [word(text, x0 + 3, top + 2, 10.4)
                  for text, (x0, _) in zip(row, columns, strict=True) if text]
    end = 74 + 20 * (len(rows) - 1)
    table = FakeTable((columns[0][0], 55, columns[-1][1], end), rows)
    table.rows = boxes
    if lead:
        words.append(word(lead, 50, 20))
    if tail:
        words.append(word(tail, 50, end + 20))
    return FakePage(words, [table])


def pages_of(*fake_pages):
    return [m._page_lines(page) for page in fake_pages]


def texts(page):
    return [line["text"] for line in page]


def test_the_end_of_a_row_cut_by_the_page_break_is_joined_to_the_row_it_continues():
    pages = pages_of(
        table_page([["", "12", "Reporting", "x", ""], ROW_13], PAGE_3_COLUMNS),
        table_page([["", "approvals", ""], ["", "", ""]], PAGE_4_COLUMNS),
    )
    assert texts(pages[1]) == ["| approvals |"], "before the join the fragment is a row of its own"
    joined = m._join_split_rows(pages)
    assert texts(joined[0]) == [
        "| 12 | Reporting | x |",
        "| 13 | Reporting, roles and approvals | See sections 11 and 12 |",
    ]
    assert joined[1] == [], "the table that held only the fragment is gone"


def test_a_row_that_has_its_own_number_is_not_joined():
    pages = pages_of(
        table_page([ROW_13], PAGE_3_COLUMNS),
        table_page([["14", "Next", "A value"]], PAGE_4_COLUMNS),
    )
    before = [texts(p) for p in pages]
    assert [texts(p) for p in m._join_split_rows(pages)] == before


def test_a_fragment_that_falls_in_the_number_column_is_not_joined():
    pages = pages_of(
        table_page([ROW_13], PAGE_3_COLUMNS),
        table_page([["x", "", ""]], PAGE_4_COLUMNS),
    )
    before = [texts(p) for p in pages]
    assert [texts(p) for p in m._join_split_rows(pages)] == before


def test_a_fragment_that_falls_outside_every_column_of_the_row_above_is_not_joined():
    pages = pages_of(
        table_page([ROW_13], PAGE_3_COLUMNS),
        table_page([["", "approvals", ""]], [(500, 520), (520, 600), (600, 700)]),
    )
    before = [texts(p) for p in pages]
    assert [texts(p) for p in m._join_split_rows(pages)] == before


def test_a_table_that_is_not_the_first_thing_on_its_page_is_not_joined():
    pages = pages_of(
        table_page([ROW_13], PAGE_3_COLUMNS),
        table_page([["", "approvals", ""]], PAGE_4_COLUMNS, lead="Some text above"),
    )
    before = [texts(p) for p in pages]
    assert [texts(p) for p in m._join_split_rows(pages)] == before


def test_a_previous_page_that_ends_in_a_text_line_is_not_joined():
    pages = pages_of(
        table_page([ROW_13], PAGE_3_COLUMNS, tail="Text after the table"),
        table_page([["", "approvals", ""]], PAGE_4_COLUMNS),
    )
    before = [texts(p) for p in pages]
    assert [texts(p) for p in m._join_split_rows(pages)] == before


def test_a_previous_page_with_no_lines_or_a_page_with_no_table_is_left_alone():
    plain = FakePage([word("Some", 50, 20), word("text.", 90, 20)])
    pages = pages_of(plain, table_page([["", "approvals", ""]], PAGE_4_COLUMNS))
    assert [texts(p) for p in m._join_split_rows(pages)] == [texts(p) for p in pages]
    pages = pages_of(table_page([ROW_13], PAGE_3_COLUMNS), plain)
    assert [texts(p) for p in m._join_split_rows(pages)] == [texts(p) for p in pages]
    assert m._join_split_rows([[], []]) == [[], []]


def test_the_other_rows_of_the_second_table_stay_when_only_its_first_row_is_joined():
    pages = pages_of(
        table_page([ROW_13], PAGE_3_COLUMNS),
        table_page([["", "approvals", ""], ["14", "Next", "A value"]], PAGE_4_COLUMNS),
    )
    joined = m._join_split_rows(pages)
    assert texts(joined[0]) == [
        "| 13 | Reporting, roles and approvals | See sections 11 and 12 |"]
    assert texts(joined[1]) == ["| 14 | Next | A value |"]


def test_a_cell_in_each_of_two_columns_is_joined_column_by_column_with_one_space():
    pages = pages_of(
        table_page([["", "13", "Reporting, roles", "See sections", ""]], PAGE_3_COLUMNS),
        table_page([["", "and approvals", "11 and 12"]], PAGE_4_COLUMNS),
    )
    joined = m._join_split_rows(pages)
    assert texts(joined[0]) == ["| 13 | Reporting, roles and approvals | See sections 11 and 12 |"]


def test_without_cell_boxes_a_fragment_with_the_same_columns_and_an_empty_first_cell_is_joined():
    pages = pages_of(
        table_page([["13", "Reporting, roles and", "See sections 11 and 12"]],
                   PAGE_4_COLUMNS, geometry=False),
        table_page([["", "approvals", ""], ["14", "Next", "A value"]],
                   PAGE_4_COLUMNS, geometry=False),
    )
    joined = m._join_split_rows(pages)
    assert texts(joined[0]) == ["| 13 | Reporting, roles and approvals | See sections 11 and 12 |"]
    assert texts(joined[1]) == ["| 14 | Next | A value |"]


def test_without_cell_boxes_a_fragment_with_its_own_number_or_other_columns_is_not_joined():
    own = pages_of(
        table_page([ROW_13[1:4]], PAGE_4_COLUMNS, geometry=False),
        table_page([["14", "Next", "A value"]], PAGE_4_COLUMNS, geometry=False),
    )
    assert [texts(p) for p in m._join_split_rows(own)] == [texts(p) for p in own]
    narrow = pages_of(
        table_page([ROW_13[1:4]], PAGE_4_COLUMNS, geometry=False),
        table_page([["", "approvals", ""]], PAGE_4_COLUMNS, geometry=False),
    )
    assert [texts(p) for p in m._join_split_rows(narrow)] == [texts(p) for p in narrow]


def test_the_joined_row_is_one_row_of_the_section_text_with_the_whole_name():
    from mdeis.ingest import methodology_gate as gate

    pages = pages_of(
        table_page([["", "1", "Reporting, roles and", "See sections 11 and 12", ""]],
                   PAGE_3_COLUMNS),
        table_page([["", "approvals", ""]], PAGE_4_COLUMNS),
    )
    joined = m._join_split_rows(pages)
    head = {"text": "1. Configuration", "bold": False, "size": 16.0, "top": 0.0, "bottom": 10.0}
    parsed = m.parse_pdf_lines([[head] + joined[0], joined[1]])
    section = next(s for s in parsed.sections if s.id == "1")
    assert "| 1 | Reporting, roles and approvals | See sections 11 and 12 |" in section.text
    found = gate.check([section], {}, gate.load_gate())
    assert "Reporting preferences" not in " ".join(f.needed for f in found if f.rule == "GT-1")


# --------------------------------------------------------------------------- the real files
AURORA_IDS = [None, "1", "2", "3", "4", "5", "6", "6.1", "6.2", "6.3", "6.4", "6.5", "7", "8",
              "9", "10", "11", "12", "13"]


def read_real(path):
    pytest.importorskip("pdfplumber")
    if not path.exists():
        pytest.skip(f"{path.name} is not here")
    parsed = m.parse_methodology(path)
    assert not isinstance(parsed, m.Failure), parsed
    return parsed


def test_the_aurora_markdown_has_the_sections_the_readme_describes():
    parsed = m.parse_methodology(AURORA_MD)
    assert [s.id for s in parsed.sections] == AURORA_IDS


def test_the_aurora_pdf_reads_to_exactly_the_sections_of_the_aurora_markdown():
    pdf = read_real(AURORA_PDF)
    md = m.parse_methodology(AURORA_MD)
    assert [s.id for s in pdf.sections] == [s.id for s in md.sections] == AURORA_IDS
    assert [s.level for s in pdf.sections] == [s.level for s in md.sections]
    assert [s.title for s in pdf.sections] == [s.title for s in md.sections]
    assert not pdf.issues, "no repeated or missing numbering"


def test_aurora_pdf_numbered_list_items_of_section_2_are_not_sections():
    pdf = read_real(AURORA_PDF)
    found = [s.id for s in pdf.sections if s.id]
    assert found.count("2") == 1 and "2.1" not in found
    assert "Understand the request" in text_of(pdf, "2")
    assert "Route for human review" in text_of(pdf, "2")


def test_aurora_pdf_pages_run_in_order_and_the_title_block_is_on_page_one():
    pdf = read_real(AURORA_PDF)
    pages = [s.page for s in pdf.sections]
    assert pages == sorted(pages) and max(pages) <= pdf.pages
    assert pdf.sections[0].id is None and pdf.sections[0].page == 1


def test_the_real_v2_0_pdf_still_reads_to_the_same_section_list_as_before():
    pdf = read_real(REAL_V2_PDF)
    truth = json.loads((FIXTURES / "methodology-v2.0.sections.json").read_text(encoding="utf-8"))
    found = [{"id": s.id, "level": s.level, "title": s.title} for s in pdf.sections if s.id]
    assert found == truth
