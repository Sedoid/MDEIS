"""Read one report card (layout OCC-1, docs/INGEST.md section 3). No model, no network, no database.

`parse_report_card(path)` opens the PDF with pdfplumber and hands the words (each with its position)
to `parse_pages`, which is pure: the same words always give the same card, or one typed failure.

How a card is read, by position and by the numbers on a line, never as a table:

* The title line gives the stage and the school year. The labels in the header box give the
  learner's reference, name and year. Date of birth and gender are checked to be there and are not
  returned.
* The table header gives the x-position of each column. A word belongs to the column whose header is
  nearest to its left edge. The words left of the coefficient column are the subject name.
* A subject row is a line with a whole number in the coefficient column and values in the term
  columns. It must carry a coefficient, three term cells, the annual score and the weighted score. A
  blank term is `-` or `ABS` and is returned as missing, never as zero. An annual score may end in
  `*` (the average of the terms that have a result). Anything else is a failure.
* The name, the grade letter and the remarks belong to the row nearest to them. A block of remark
  lines that has no row inside its height (the rest of a remark that ran over the page break) goes
  to the row before it.
* Then TOTAL, the ANNUAL AVERAGE sentence, `Absences:` and `Class council decision:`.

Nothing printed is recomputed, corrected or rounded: `ingest_assessments` compares them later.
Every number stays the text the card printed.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from statistics import median

# --- limits (a file outside them is refused before it is read) ---------------------------------
MAX_BYTES = 8_000_000
MAX_PAGES = 12
PDF_HEADER = b"%PDF-"

# --- failure kinds (docs/INGEST.md section 2) --------------------------------------------------
NOT_A_PDF = "not_a_pdf"
NO_TEXT_LAYER = "no_text_layer"
TOO_LARGE = "too_large"
TOO_MANY_PAGES = "too_many_pages"
LAYOUT_UNRECOGNISED = "layout_unrecognised"
LEARNER_REF_MISSING = "learner_ref_missing"
YEAR_MISMATCH = "year_mismatch"
NO_SUBJECT_ROWS = "no_subject_rows"
ROW_UNREADABLE = "row_unreadable"

# --- layout OCC-1: distances on the page, in points (not school rules) --------------------------
LINE_TOL = 2.5  # words this close in height are on one line
SUBJECT_GAP = 6  # the subject name ends this far left of the Coef. header
REACH = 60  # a name or grade further than this from every row belongs to none
TERM_DIGIT_REACH = 25  # the digit of "Term 1" may sit this far below the word "Term"
LABEL_JOIN = 8  # header labels this close in x are one column
PITCH_GROWTH = 1.15  # remark lines further apart than the line pitch times this are two blocks
BLOCK_PAD = 2  # a remark block covers its lines plus this much

TITLE = re.compile(
    r"ANNUAL REPORT CARD\s*[·•|]\s*(.+?)\s*[·•|]\s*ACADEMIC YEAR\s*(\d{4}/\d{4})"
)
LABELS = ("Name:", "Date of birth:", "Gender:", "ID number:", "Class:", "Academic year:")
LABEL_SPLIT = re.compile("(" + "|".join(re.escape(label) for label in LABELS) + ")")
LABEL_AT = re.compile("^(" + "|".join(re.escape(label) for label in LABELS) + ")")
YEAR = re.compile(r"\d{4}/\d{4}")
WHOLE = re.compile(r"\d+")
DECIMAL = re.compile(r"\d+(?:\.\d+)?")
BLANKS = {"-", "ABS"}
AVERAGED = "*"  # printed after an annual score that averages only the terms with a result
GRADE = re.compile(r"[A-F]\+?")
DENOMINATOR = re.compile(r"Term scores\s*/\s*(\d+)")
AVERAGE = re.compile(
    r"=\s*(?P<total>\d+(?:\.\d+)?)\s*÷\s*(?P<coef>\d+)\s*=\s*(?P<avg>\d+(?:\.\d+)?)\s*/\s*\d+\s*"
    r"Grade:\s*(?P<grade>\S+)"
)
ABSENCES = re.compile(
    r"Absences:\s*(?P<n>\d+)\s*\|\s*Class council decision:\s*(?P<text>.+?)"
    r"\s*(?:Head of Class Council:|$)"
)
VALUE_COLUMNS = ("t1", "t2", "t3", "annual", "weighted")
NUMBER_COLUMNS = ("coef", *VALUE_COLUMNS)


@dataclass(frozen=True)
class Failure:
    kind: str
    detail: str
    page: int | None = None
    row: int | None = None


@dataclass(frozen=True)
class Row:
    row: int
    page: int
    subject: str
    coefficient: int
    terms: tuple  # each term as printed, or None when blank (`-` or `ABS`)
    printed_terms: tuple  # each term cell exactly as printed
    annual_score: str | None
    weighted_score: str | None
    grade: str | None
    remark: str
    remark_pages: tuple


@dataclass(frozen=True)
class Card:
    school_name: str
    academic_year: str
    stage: str
    class_name: str
    learner_ref: str
    learner_name: str
    denominator: str
    rows: tuple
    total_coefficients: int
    total_weighted: str
    annual_average: str
    grade: str | None
    absences: int
    council_decision: str
    pages: int


@dataclass(frozen=True)
class ParseResult:
    card: Card | None = None
    failure: Failure | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> ParseResult:
        if data.get("failure"):
            return ParseResult(failure=Failure(**data["failure"]))
        card = dict(data["card"])
        card["rows"] = tuple(
            Row(
                **{
                    **r,
                    "terms": tuple(r["terms"]),
                    "printed_terms": tuple(r["printed_terms"]),
                    "remark_pages": tuple(r["remark_pages"]),
                }
            )
            for r in card["rows"]
        )
        return ParseResult(card=Card(**card))


def fail(kind, detail, page=None, row=None) -> ParseResult:
    return ParseResult(failure=Failure(kind, detail, page, row))


# --- words -------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Tok:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    page: int

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2


def tokens_of(pages) -> list:
    """pdfplumber words (dicts with text, x0, x1, top, bottom) as tokens, page by page."""
    return [
        [Tok(w["text"], w["x0"], w["x1"], w["top"], w["bottom"], number) for w in words]
        for number, words in enumerate(pages, start=1)
    ]


def lines_of(tokens) -> list:
    """Tokens grouped into lines (top to bottom), each line left to right."""
    out = []
    for t in sorted(tokens, key=lambda t: (t.page, t.cy, t.x0)):
        if out and out[-1][-1].page == t.page and abs(t.cy - out[-1][-1].cy) <= LINE_TOL:
            out[-1].append(t)
        else:
            out.append([t])
    return [sorted(line, key=lambda t: t.x0) for line in out]


def text_of(line) -> str:
    return " ".join(t.text for t in line)


def nearest(value, options: dict):
    return min(options, key=lambda name: abs(options[name] - value))


def height_of(line) -> float:
    return median(t.bottom - t.top for t in line)


# --- the reader --------------------------------------------------------------------------------
def parse_pages(pages) -> ParseResult:
    """The card in `pages` (a list of word lists, one per page), or the first failure found."""
    pages = tokens_of(pages)
    if not any(pages):
        return fail(NO_TEXT_LAYER, "the PDF has no text layer; scanned cards are not supported", 1)
    first = pages[0]
    head = _title(first)
    if isinstance(head, ParseResult):
        return head
    header = _table_header(first)
    if isinstance(header, ParseResult):
        return header
    fields = _fields(first, head, header)
    problem = _check_fields(fields, head)
    if problem:
        return problem
    return _read_table(pages, head, header, fields)


def _title(first) -> dict | ParseResult:
    """The title line of page 1: stage, school year, school name."""
    lines = lines_of(first)
    title_line = next((ln for ln in lines if TITLE.search(text_of(ln))), None)
    if title_line is None:
        return fail(LAYOUT_UNRECOGNISED, "no 'ANNUAL REPORT CARD' title line", 1)
    match = TITLE.search(text_of(title_line))
    above = [ln for ln in lines if ln[0].cy < title_line[0].cy - LINE_TOL]
    return {
        "title_line": title_line,
        "stage": " ".join(w.capitalize() for w in match.group(1).split()),
        "title_year": match.group(2),
        "school": " ".join(text_of(ln) for ln in above),
    }


def _table_header(first) -> dict | ParseResult:
    """Where each column of the table is, from the header words on page 1."""

    def named(text):
        return [t for t in first if t.text == text]

    coef, subject = named("Coef."), named("Subject")
    grade, teacher = named("Grade"), named("Teacher's")
    annual = sorted(named("Annual"), key=lambda t: t.cy)[:2]
    if not (coef and subject and grade and teacher and len(annual) == 2):
        return fail(LAYOUT_UNRECOGNISED, "the table header (Subject, Coef., ...) is missing", 1)
    terms = {}
    for word in named("Term"):
        digits = [
            d
            for d in first
            if d.text in ("1", "2", "3")
            and d.x0 >= word.x0 - LINE_TOL
            and -LINE_TOL <= d.cy - word.cy <= TERM_DIGIT_REACH
        ]
        if digits:
            # the digit on the same line first, else the one just below, then the leftmost
            digit = min(digits, key=lambda d: (abs(d.cy - word.cy) > LINE_TOL, d.x0 - word.x0))
            terms[digit.text] = word.x0
    if set(terms) != {"1", "2", "3"}:
        return fail(LAYOUT_UNRECOGNISED, "the Term 1, Term 2 and Term 3 headers are missing", 1)
    scale = next((ln for ln in lines_of(first) if DENOMINATOR.search(text_of(ln))), None)
    if scale is None:
        return fail(LAYOUT_UNRECOGNISED, "no 'Term scores /n' header, so no scale", 1)
    annual = sorted(annual, key=lambda t: t.x0)
    anchors = {
        "coef": coef[0].x0,
        "t1": terms["1"],
        "t2": terms["2"],
        "t3": terms["3"],
        "annual": annual[0].x0,
        "weighted": annual[1].x0,
        "grade": grade[0].x0,
        "remarks": teacher[0].x0,
    }
    return {
        "anchors": anchors,
        "denominator": DENOMINATOR.search(text_of(scale)).group(1),
        "top": min(t.top for t in [*coef, *subject, *annual, *named("Term")]),
        "subject_edge": anchors["coef"] - SUBJECT_GAP,
    }


def _fields(first, head, header) -> dict:
    """Label -> value for the header box. Each column of labels is read on its own, so a value that
    wraps under its label stays with it."""
    below_title = head["title_line"][0].bottom
    region = [t for t in first if t.top > below_title and t.bottom <= header["top"] + LINE_TOL]
    starts = []
    for line in lines_of(region):
        for k, t in enumerate(line):
            if LABEL_AT.match(text_of(line[k : k + 3])):
                starts.append(t.x0)
    columns = []
    for x in sorted(starts):
        if not columns or x - columns[-1] > LABEL_JOIN:
            columns.append(x)
    values = {}
    for k, start in enumerate(columns):
        end = columns[k + 1] if k + 1 < len(columns) else float("inf")
        mine = [t for t in region if start - LABEL_JOIN <= t.x0 < end - LABEL_JOIN]
        parts = LABEL_SPLIT.split(" ".join(text_of(ln) for ln in lines_of(mine)))
        for i in range(1, len(parts) - 1, 2):
            values[parts[i]] = parts[i + 1].strip()
    return values


def _check_fields(fields, head) -> ParseResult | None:
    if not fields.get("Name:"):
        return fail(LAYOUT_UNRECOGNISED, "no 'Name:' field in the header box", 1)
    if not fields.get("ID number:"):
        return fail(LEARNER_REF_MISSING, "the card has no 'ID number'", 1)
    year = fields.get("Academic year:", "")
    if not YEAR.fullmatch(year):
        return fail(LAYOUT_UNRECOGNISED, "no 'Academic year:' field in the header box", 1)
    if year != head["title_year"]:
        return fail(
            YEAR_MISMATCH,
            f"the title says {head['title_year']} but the Academic year field says {year}",
            1,
        )
    if not fields.get("Date of birth:") or not fields.get("Gender:"):
        return fail(LAYOUT_UNRECOGNISED, "the header box lacks Date of birth or Gender", 1)
    return None


@dataclass(frozen=True)
class Total:
    page: int
    cy: float


def _read_table(pages, head, header, fields) -> ParseResult:
    anchors, edge = header["anchors"], header["subject_edge"]

    def column(t):
        return "subject" if t.x0 < edge else nearest(t.x0, anchors)

    total = _find_total(pages, column, header)
    if total is None:
        return fail(LAYOUT_UNRECOGNISED, "no TOTAL line under the table", 1)
    bodies = _bodies(pages[: total.page], column, header, total)
    found = []
    for body in bodies:
        rows = _rows_of(body, column, len(found))
        if isinstance(rows, ParseResult):
            return rows
        found.extend(rows)
    if not found:
        return fail(NO_SUBJECT_ROWS, "no subject row (a coefficient and five numbers) found", 1)
    rows = _fill(found, bodies, column)
    if isinstance(rows, ParseResult):
        return rows
    foot = _foot(pages, total, column)
    if isinstance(foot, ParseResult):
        return foot
    on_total = [t for t in pages[total.page - 1] if abs(t.cy - total.cy) <= LINE_TOL]
    total_coef = [t.text for t in on_total if column(t) == "coef" and WHOLE.fullmatch(t.text)]
    total_weighted = [
        t.text for t in on_total if column(t) == "weighted" and DECIMAL.fullmatch(t.text)
    ]
    if len(total_coef) != 1 or len(total_weighted) != 1:
        return fail(LAYOUT_UNRECOGNISED, "the TOTAL line lacks its two totals", total.page)
    return ParseResult(
        card=Card(
            school_name=head["school"],
            academic_year=fields["Academic year:"],
            stage=head["stage"],
            class_name=fields.get("Class:", ""),
            learner_ref=fields["ID number:"],
            learner_name=fields["Name:"],
            denominator=header["denominator"],
            rows=tuple(rows),
            total_coefficients=int(total_coef[0]),
            total_weighted=total_weighted[0],
            annual_average=foot["average"],
            grade=foot["grade"],
            absences=foot["absences"],
            council_decision=foot["decision"],
            pages=len(pages),
        )
    )


def _find_total(pages, column, header) -> Total | None:
    for page in pages:
        for t in page:
            below_header = t.page > 1 or t.top > header["top"]
            if t.text == "TOTAL" and column(t) == "subject" and below_header:
                return Total(t.page, t.cy)
    return None


def _bodies(pages, column, header, total) -> list:
    """The table words of each page up to TOTAL: below the header on page 1, above TOTAL there."""
    first_bottom = _header_bottom(pages[0], column, header)
    out = []
    for page in pages:
        body = []
        for t in page:
            if t.page == 1 and t.top <= first_bottom:
                continue
            if t.page == total.page and t.cy >= total.cy - LINE_TOL:
                continue
            body.append(t)
        out.append(body)
    return out


def _header_bottom(first, column, header) -> float:
    """The bottom of the lowest header word of the number columns: the table starts below it."""
    numeric = [t for t in first if column(t) in NUMBER_COLUMNS and t.top >= header["top"]]
    for line in lines_of(numeric):
        if any(column(t) == "coef" and WHOLE.fullmatch(t.text) for t in line):
            above = [t for t in numeric if t.cy < line[0].cy - LINE_TOL]
            return max((t.bottom for t in above), default=header["top"])
    return header["top"]


def cell_text(column: str, text: str) -> str:
    """A cell as the number it prints. An annual score may end in `*`: the card says that mark means
    the score averages the terms that have a result (a term with `ABS` is left out). The mark is
    dropped here; our own sum does the same averaging and checks it against the printed score."""
    if column == "annual" and text.endswith(AVERAGED):
        return text[: -len(AVERAGED)]
    return text


def _rows_of(body, column, already) -> list | ParseResult:
    """The subject rows of one page: a line with a coefficient and values under the terms."""
    out = []
    for line in lines_of([t for t in body if column(t) in NUMBER_COLUMNS]):
        by = {}
        for t in line:
            by.setdefault(column(t), []).append(t)
        if "coef" not in by or not any(c in by for c in VALUE_COLUMNS):
            continue
        number, page = already + len(out) + 1, line[0].page

        def bad(why, number=number, page=page):
            return fail(ROW_UNREADABLE, f"row {number}: {why}", page, number)

        if len(by["coef"]) != 1 or not WHOLE.fullmatch(by["coef"][0].text):
            return bad("the coefficient is not a whole number")
        cells = {}
        for name in VALUE_COLUMNS:
            got = by.get(name, [])
            if len(got) != 1:
                return bad(f"expected one value under {name}, found {len(got)}")
            text = cell_text(name, got[0].text)
            if not (DECIMAL.fullmatch(text) or text in BLANKS):
                return bad(f"{got[0].text!r} under {name} is not a number")
            cells[name] = text
        out.append(
            {
                "row": number,
                "page": page,
                "cy": line[0].cy,
                "coefficient": int(by["coef"][0].text),
                "cells": cells,
            }
        )
    return out


def _fill(found, bodies, column) -> list | ParseResult:
    """Add the subject name, grade and remark to each row. Returns the finished rows."""
    ordered = sorted(found, key=lambda r: (r["page"], r["cy"]))
    by_page = {}
    for r in ordered:
        by_page.setdefault(r["page"], []).append(r)
    names, grades, remarks = {}, {}, {}
    remark_lines = [
        ln for body in bodies for ln in lines_of([t for t in body if column(t) == "remarks"])
    ]
    pitch = _pitch(remark_lines)
    for body in bodies:
        if not body:
            continue
        rows = by_page.get(body[0].page, [])
        _assign([t for t in body if column(t) == "subject"], rows, names)
        _assign([t for t in body if column(t) == "grade"], rows, grades)
        lines = lines_of([t for t in body if column(t) == "remarks"])
        for block in _blocks(lines, pitch):
            top = block[0][0].cy - height_of(block[0]) / 2 - BLOCK_PAD
            bottom = block[-1][0].cy + height_of(block[-1]) / 2 + BLOCK_PAD
            inside = [r for r in rows if top <= r["cy"] <= bottom]
            pieces = [block]
            if len(inside) > 1:  # one block cannot belong to two rows: give each line its own row
                pieces = [[line] for line in block]
                inside = None
            for piece in pieces:
                owner = _owner(piece, inside, rows, ordered)
                if owner is None:
                    where = piece[0][0].page
                    return fail(ROW_UNREADABLE, "remark text with no subject row before it", where)
                remarks.setdefault(owner["row"], []).append(
                    (piece[0][0].page, [text_of(ln) for ln in piece])
                )
    out = []
    for r in ordered:
        subject = " ".join(text_of(ln) for ln in lines_of(names.get(r["row"], [])))
        if not subject:
            return fail(ROW_UNREADABLE, f"row {r['row']}: no subject name", r["page"], r["row"])
        grade = None
        for t in sorted(grades.get(r["row"], []), key=lambda t: (t.cy, t.x0)):
            if GRADE.fullmatch(t.text):
                grade = t.text
                break
            if t.text in BLANKS:
                break
        cells = r["cells"]
        printed = tuple(cells[c] for c in ("t1", "t2", "t3"))
        pieces = remarks.get(r["row"], [])
        out.append(
            Row(
                row=r["row"],
                page=r["page"],
                subject=subject,
                coefficient=r["coefficient"],
                terms=tuple(None if p in BLANKS else p for p in printed),
                printed_terms=printed,
                annual_score=None if cells["annual"] in BLANKS else cells["annual"],
                weighted_score=None if cells["weighted"] in BLANKS else cells["weighted"],
                grade=grade,
                remark=" ".join(text for _, lines in pieces for text in lines),
                remark_pages=tuple(dict.fromkeys(page for page, _ in pieces)) or (r["page"],),
            )
        )
    return out


def _assign(tokens, rows, into) -> None:
    """Give each token to the row nearest to it in height, if one is within reach."""
    if not rows:
        return
    for t in tokens:
        row = min(rows, key=lambda r: abs(r["cy"] - t.cy))
        if abs(row["cy"] - t.cy) <= REACH:
            into.setdefault(row["row"], []).append(t)


def _pitch(lines) -> float | None:
    """The distance between neighbouring lines of one remark: the smallest gap between any two lines
    on a page. None when no page has a remark of two lines or more to measure it from."""
    gaps = []
    for k in range(1, len(lines)):
        a, b = lines[k - 1], lines[k]
        if a[0].page == b[0].page and b[0].cy - a[0].cy > LINE_TOL:
            gaps.append(b[0].cy - a[0].cy)
    return min(gaps) if gaps else None


def _blocks(lines, pitch) -> list:
    """Remark lines grouped into blocks: lines one pitch apart belong to the same remark."""
    blocks = []
    for line in lines:
        near = (
            blocks
            and pitch is not None
            and line[0].page == blocks[-1][-1][0].page
            and line[0].cy - blocks[-1][-1][0].cy <= pitch * PITCH_GROWTH
        )
        if near:
            blocks[-1].append(line)
        else:
            blocks.append([line])
    return blocks


def _owner(piece, inside, rows, ordered) -> dict | None:
    """The row a block of remark lines belongs to: the one inside it, else the row before it."""
    middle = (piece[0][0].cy + piece[-1][0].cy) / 2
    if inside is None:
        inside = [
            r
            for r in rows
            if piece[0][0].cy - BLOCK_PAD - height_of(piece[0]) / 2
            <= r["cy"]
            <= piece[-1][0].cy + BLOCK_PAD + height_of(piece[-1]) / 2
        ]
    if inside:
        return min(inside, key=lambda r: abs(r["cy"] - middle))
    before = [r for r in ordered if (r["page"], r["cy"]) < (piece[0][0].page, middle)]
    return before[-1] if before else None


def _foot(pages, total, column) -> dict | ParseResult:
    """The ANNUAL AVERAGE sentence, the absences and the class council decision."""
    after = [
        t
        for page in pages[total.page - 1 :]
        for t in page
        if not (t.page == total.page and t.cy <= total.cy + LINE_TOL)
    ]
    scale = next((t for t in after if t.text == "GRADING"), None)
    edge = scale.x0 - LINE_TOL if scale else float("inf")  # the grading scale box sits beside it
    box = " ".join(text_of(ln) for ln in lines_of([t for t in after if t.x0 < edge]))
    average = AVERAGE.search(box)
    if not average:
        return fail(LAYOUT_UNRECOGNISED, "no ANNUAL AVERAGE sentence under TOTAL", total.page)
    line = next((ln for ln in lines_of(after) if any(t.text == "Absences:" for t in ln)), None)
    absences = ABSENCES.search(text_of(line)) if line else None
    if not absences:
        return fail(
            LAYOUT_UNRECOGNISED, "no 'Absences: n | Class council decision: ...' line", total.page
        )
    return {
        "average": average.group("avg"),
        "grade": average.group("grade"),
        "absences": int(absences.group("n")),
        "decision": absences.group("text").strip(),
    }


# --- the file ----------------------------------------------------------------------------------
def parse_report_card(path) -> ParseResult:
    """Read a PDF file. Checks the file first (size, header, page count, text layer)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            start = f.read(len(PDF_HEADER))
    except OSError as e:
        return fail(NOT_A_PDF, f"the file cannot be read ({type(e).__name__})")
    if size > MAX_BYTES:
        return fail(TOO_LARGE, f"{size} bytes is over the limit of {MAX_BYTES}")
    if start != PDF_HEADER:
        return fail(NOT_A_PDF, "the file does not start with %PDF-")
    import pdfplumber

    try:
        with pdfplumber.open(path) as pdf:
            if len(pdf.pages) > MAX_PAGES:
                return fail(TOO_MANY_PAGES, f"{len(pdf.pages)} pages: the limit is {MAX_PAGES}")
            pages = [page.extract_words() for page in pdf.pages]
    except Exception as e:  # a damaged PDF: pdfminer raises many kinds
        return fail(NOT_A_PDF, f"the PDF cannot be opened ({type(e).__name__})")
    return parse_pages(pages)


def show_lines(path, limit=40) -> int:
    """For a card that will not read: the lines of each page, with where each word starts.

    `python -m mdeis.ingest.report_card --lines FILE`. A line is `page y: word@x word@x ...`, the
    words as the reader sees them. This is for people, not for the worker."""
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        pages = [page.extract_words() for page in pdf.pages]
    for page in tokens_of(pages):
        for line in lines_of(page)[:limit]:
            words = " ".join(f"{t.text}@{round(t.x0)}" for t in line[:14])
            more = f" (+{len(line) - 14} more)" if len(line) > 14 else ""
            print(f"p{line[0].page} y={round(line[0].cy):>3}: {words}{more}")
    return 0


def main(argv=None) -> int:
    """`python -m mdeis.ingest.report_card FILE` prints the result as JSON. The worker runs it."""
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 2 and argv[0] == "--lines":
        return show_lines(argv[1])
    if len(argv) != 1:
        print("usage: python -m mdeis.ingest.report_card [--lines] FILE", file=sys.stderr)
        return 2
    print(json.dumps(parse_report_card(argv[0]).to_dict()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
