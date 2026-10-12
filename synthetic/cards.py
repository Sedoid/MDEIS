"""Print the practice report cards as PDFs that look like the school's real one (layout OCC-1).

Copied from the real sample (`data/synthetic/samples/amara_nfor_form1_2022-2023.pdf`):
the school name and title line, a header box (name, date of birth, gender, ID number, class,
academic year), a table of 14 subjects (coefficient, three term scores, annual score, annual score
x coefficient, grade with its word, teacher's remarks that wrap over several lines and sometimes
carry on to the next page), TOTAL, the ANNUAL AVERAGE sentence, the GRADING SCALE text, a conduct
paragraph, absences and the class council decision, and three signature lines.

Differences from the real file, on purpose: the font is the PDF built-in Times (no font file needed,
so the output is the same on every machine), and the grading scale uses a plain hyphen where the
real card has an en dash.

Each card also gets a truth sheet (the values the card prints, each with its page and row), so a
parser can be checked field by field (E-12). Same card in, same bytes out.
"""

from datetime import UTC, datetime
from fractions import Fraction

from fpdf import FPDF

from .maths import two_places

PAGE_W, PAGE_H = 612, 792
LEFT, RIGHT = 36, 576
BOTTOM = 742  # the last y a table row may reach
NAVY = (31, 56, 100)
GREY = (125, 125, 125)
# column widths: subject, coef, term 1, term 2, term 3, annual, annual x C, grade, remarks
WIDTHS = [96, 34, 42, 42, 42, 58, 58, 56, 112]
XS = [LEFT + sum(WIDTHS[:i]) for i in range(len(WIDTHS) + 1)]
LINE = 13  # height of one line of remarks
REMARK_SIZE = 10
PAD = 6
MIN_ROW = 30
UNREADABLE_ROW = 7  # the row that prints four numbers instead of five in unreadable_row.pdf
MIN_SPLIT_LINES = 3  # never leave fewer than this many remark lines at the foot of a page
FIXED_DATE = datetime(2026, 10, 12, 9, 0, 0, tzinfo=UTC)
MONTHS = [
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
]  # fmt: skip
GRADE_WORDS = {  # the words the real card prints beside each grade letter
    "A+": "Excellent", "A": "Very Good", "B": "Good", "C": "Fairly Good", "D": "Average",
    "E": "Weak",
}  # fmt: skip
SCHOOL_TITLE = "OUTLIERS COMPREHENSIVE COLLEGE"


def word(grade):
    return GRADE_WORDS.get(grade, "")


def wrap(pdf, text, width):
    """Break text into lines no wider than `width` at the current font."""
    lines, current = [], ""
    for w in text.split():
        trial = f"{current} {w}".strip()
        if pdf.get_string_width(trial) <= width or not current:
            current = trial
        else:
            lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines or [""]


def scale_text(bands, scale_max):
    """'18-20 A+ Excellent · 16-17.99 A Very Good ... below 10 E Weak' from the template's bands."""
    parts, upper = [], two_places(scale_max).rstrip("0").rstrip(".")
    for k, (grade, low) in enumerate(bands):
        low_text = two_places(low).rstrip("0").rstrip(".")
        if k == len(bands) - 1:
            parts.append(f"below {upper_of(bands, k)} {grade} {word(grade)}")
        else:
            parts.append(f"{low_text}-{upper} {grade} {word(grade)}")
        upper = two_places(low - Fraction(1, 100))
    return " · ".join(parts)


def upper_of(bands, k):
    return two_places(bands[k - 1][1]).rstrip("0").rstrip(".")


def nice_date(iso):
    y, m, d = iso.split("-")
    return f"{int(d)} {MONTHS[int(m) - 1]} {y}"


class Writer:
    """Draws one card and records where everything went."""

    def __init__(self, card, rules, variant=None):
        self.card, self.rules, self.variant = card, rules, variant
        pdf = FPDF(unit="pt", format="Letter")
        pdf.set_auto_page_break(False)
        pdf.set_compression(False)  # plain streams: deterministic, and text is easy to extract
        pdf.set_creation_date(FIXED_DATE)
        pdf.set_producer("MDEIS synthetic card generator")
        pdf.set_creator("MDEIS synthetic card generator")
        pdf.set_title(f"Report card {card['card_id']} (synthetic)")
        self.pdf = pdf
        self.y = 0
        self.rows = []

    # ---- page furniture
    def new_page(self):
        p = self.pdf
        p.add_page()
        p.set_draw_color(*NAVY)
        p.set_line_width(1.4)
        p.rect(24, 24, PAGE_W - 48, PAGE_H - 48)
        p.set_line_width(0.4)
        p.rect(27, 27, PAGE_W - 54, PAGE_H - 54)
        self.y = 44

    def text_at(self, x, y, text, size=9, style="", color=(0, 0, 0), align="L", width=0):
        p = self.pdf
        p.set_font("Times", style, size)
        p.set_text_color(*color)
        if align == "C":
            x = x + (width - p.get_string_width(text)) / 2
        p.text(x, y, text)

    # ---- first page
    def header(self):
        p, c = self.pdf, self.card
        p.set_char_spacing(1.2)
        self.text_at(LEFT, 70, SCHOOL_TITLE, 19, "B", NAVY, "C", RIGHT - LEFT)
        p.set_char_spacing(0)
        title_year = c["year"]
        if (
            self.variant == "year_mismatch"
        ):  # the title says one year, the Academic year field another
            start = int(c["year"][:4]) - 1
            title_year = f"{start}/{start + 1}"
        title = f"ANNUAL REPORT CARD · {c['stage'].upper()} · ACADEMIC YEAR {title_year}"
        if self.variant != "wrong_layout":  # wrong layout: no title line at all
            self.text_at(LEFT, 96, title, 11, "", (0, 0, 0), "C", RIGHT - LEFT)
        top = 112
        p.set_draw_color(0, 0, 0)
        p.set_line_width(0.7)
        p.rect(LEFT, top, RIGHT - LEFT, 76)
        start = int(c["year"][:4])
        age = start - int(c["dob"][:4])
        gender = "Female" if c["gender"] == "F" else "Male"
        cols = [LEFT + 8, LEFT + 190, LEFT + 366]
        self.field(cols[0], top + 18, "Name: ", c["name"], bold_value=True, width=170)
        self.field(
            cols[1], top + 18, "Date of birth: ", f"{nice_date(c['dob'])} (age {age})", width=165
        )
        self.field(cols[2], top + 18, "Gender: ", gender, width=160)
        # the real card wraps the ID number under its label; keep that
        if self.variant != "no_id_number":
            self.text_at(cols[0], top + 52, "ID number:", 9.5, "", (60, 60, 60))
            self.text_at(cols[0], top + 64, c["learner_id"], 9.5, "")
        self.field(cols[1], top + 52, "Class: ", c["class_name"], width=165)
        self.field(cols[2], top + 52, "Academic year: ", c["year"], width=160)
        self.y = top + 76 + 20

    def field(self, x, y, label, value, bold_value=False, width=160):
        p = self.pdf
        self.text_at(x, y, label, 9.5, "", (60, 60, 60))
        used = p.get_string_width(label)
        p.set_font("Times", "B" if bold_value else "", 9.5)
        room = width - used
        lines = wrap(p, value, room) if p.get_string_width(value) > room else [value]
        if len(lines) > 1:  # wrapped like the real card: first words after the label, rest below
            first, rest = lines[0], " ".join(lines[1:])
            self.text_at(x + used, y, first, 9.5, "B" if bold_value else "")
            self.text_at(x, y + 12, rest, 9.5, "B" if bold_value else "")
        else:
            self.text_at(x + used, y, value, 9.5, "B" if bold_value else "")

    # ---- table
    def table_header(self):
        p = self.pdf
        y, h = self.y, 58
        p.set_draw_color(*NAVY)
        p.set_line_width(0.9)
        for i, w in enumerate(WIDTHS):
            if i in (2, 3, 4):
                continue
            p.rect(XS[i], y, w, h)
        p.rect(XS[2], y, sum(WIDTHS[2:5]), 24)
        for i in (2, 3, 4):
            p.rect(XS[i], y + 24, WIDTHS[i], h - 24)
        labels = {
            0: ["Subject"],
            1: ["Coef.", "(C)"],
            5: ["Annual", "score /20", "(T1 + T2 +", "T3) ÷ 3"],
            6: ["Annual", "score × C"],
            7: ["Grade"],
            8: ["Teacher's", "remarks"],
        }
        for i, ls in labels.items():
            top = y + (h - 10 * len(ls)) / 2 + 8
            for k, line in enumerate(ls):
                self.text_at(XS[i], top + k * 10, line, 8, "B", GREY, "C", WIDTHS[i])
        self.text_at(XS[2], y + 15, "Term scores /20", 8, "B", GREY, "C", sum(WIDTHS[2:5]))
        for k, i in enumerate((2, 3, 4)):
            self.text_at(XS[i], y + 44, f"Term {k + 1}", 7.5, "B", GREY, "C", WIDTHS[i])
        self.y = y + h

    def draw_row(self, number, s):
        p = self.pdf
        p.set_font("Times", "", REMARK_SIZE)
        remark_lines = wrap(p, s["remark"], WIDTHS[8] - 12)
        p.set_font("Times", "", 9)
        name_lines = wrap(p, s["name"], WIDTHS[0] - 8)
        full = max(len(remark_lines) * LINE + 2 * PAD, len(name_lines) * 12 + 2 * PAD, MIN_ROW)
        room = BOTTOM - self.y
        pages = []
        if full > room:
            fit = int((room - 2 * PAD) // LINE)
            if fit >= MIN_SPLIT_LINES and fit < len(remark_lines):
                first, rest = remark_lines[:fit], remark_lines[fit:]
                self.paint_row(number, s, name_lines, first, True)
                pages.append(self.pdf.page_no())
                self.new_page()
                self.paint_row(number, s, [], rest, False)
                pages.append(self.pdf.page_no())
                return pages
            self.new_page()
        self.paint_row(number, s, name_lines, remark_lines, True)
        return [self.pdf.page_no()]

    def paint_row(self, number, s, name_lines, remark_lines, first):
        p = self.pdf
        h = max(len(remark_lines) * LINE + 2 * PAD, len(name_lines) * 12 + 2 * PAD, 2 * PAD + LINE)
        if first and h < MIN_ROW:
            h = MIN_ROW
        y = self.y
        p.set_draw_color(*NAVY)
        p.set_line_width(0.6)
        for i, w in enumerate(WIDTHS):
            p.rect(XS[i], y, w, h)
        for k, line in enumerate(remark_lines):
            self.text_at(XS[8] + 6, y + PAD + 9 + k * LINE, line, REMARK_SIZE)
        if first:
            mid = y + h / 2
            for k, line in enumerate(name_lines):
                base_line = (
                    mid - 6 * len(name_lines) + 9 + k * 12
                )  # a one-line name shares the numbers' line
                self.text_at(XS[0] + 4, base_line, line, 9)
            base = mid + 3
            missing = "-"
            terms = [missing if t is None else two_places(t) for t in s["terms"]]
            annual = missing if s["printed_annual"] is None else two_places(s["printed_annual"])
            weighted = (
                missing if s["printed_weighted"] is None else two_places(s["printed_weighted"])
            )
            values = [str(int(s["coef"])), *terms]
            for i, v in enumerate(values, start=1):
                self.text_at(XS[i], base, v, 9.5, "", (0, 0, 0), "C", WIDTHS[i])
            self.text_at(XS[5], base, annual, 9.5, "B", (0, 0, 0), "C", WIDTHS[5])
            if not (self.variant == "unreadable_row" and number == UNREADABLE_ROW):
                self.text_at(XS[6], base, weighted, 9.5, "", (0, 0, 0), "C", WIDTHS[6])
            grade = s["printed_grade"] or missing
            self.text_at(XS[7], base - 5, grade, 9.5, "", (0, 0, 0), "C", WIDTHS[7])
            if grade != missing:
                p.set_font("Times", "", 6.5)
                for k, line in enumerate(wrap(p, word(grade), WIDTHS[7] - 6)):
                    self.text_at(XS[7], base + 4 + k * 8, line, 6.5, "", (0, 0, 0), "C", WIDTHS[7])
        self.y = y + h

    # ---- foot of the card
    def foot(self):
        p, c, r = self.pdf, self.card, self.rules
        if self.y + 235 > BOTTOM + 20:
            self.new_page()
        y = self.y
        p.set_draw_color(*NAVY)
        p.set_line_width(0.9)
        p.rect(XS[0], y, sum(WIDTHS[:5]), 24)
        p.rect(XS[5], y, WIDTHS[5], 24)
        p.rect(XS[6], y, WIDTHS[6], 24)
        self.text_at(XS[0] + 4, y + 15, "TOTAL", 9.5, "B")
        self.text_at(
            XS[1], y + 15, str(int(c["printed_total_coef"])), 9.5, "B", (0,) * 3, "C", WIDTHS[1]
        )
        total = two_places(c["printed_total_weighted"])
        self.text_at(XS[6], y + 15, total, 9.5, "B", (0,) * 3, "C", WIDTHS[6])
        y += 44
        left_w, gap = 262, 10
        right_x = LEFT + left_w + gap
        p.set_draw_color(0, 0, 0)
        p.set_line_width(0.6)
        p.rect(LEFT, y, left_w, 74)
        p.rect(right_x, y, RIGHT - right_x, 74)
        grade = c["printed_grade"]
        avg = two_places(c["printed_average"])
        self.text_at(LEFT + 6, y + 13, "ANNUAL AVERAGE", 8.5, "B", NAVY)
        sentence = (
            f"Total of (annual score × C) ÷ total coefficients = {total} ÷ "
            f"{int(c['printed_total_coef'])} = {avg} / 20  Grade: {grade} ({word(grade)})"
        )
        p.set_font("Times", "", 8.5)
        for k, line in enumerate(wrap(p, sentence, left_w - 12)):
            self.text_at(LEFT + 6, y + 27 + k * 11, line, 8.5)
        self.text_at(right_x + 6, y + 13, "GRADING SCALE", 8.5, "B", NAVY)
        p.set_font("Times", "", 8.5)
        for k, line in enumerate(
            wrap(p, scale_text(r.grade_bands, r.scale_max), RIGHT - right_x - 12)
        ):
            self.text_at(right_x + 6, y + 27 + k * 11, line, 8.5)
        y += 92
        self.text_at(LEFT, y, "CONDUCT AND BEHAVIOUR (HEAD OF CLASS COUNCIL)", 9, "B", NAVY)
        p.set_font("Times", "", 9)
        for k, line in enumerate(wrap(p, c["conduct"], RIGHT - LEFT - 6)):
            self.text_at(LEFT + 3, y + 14 + k * 12, line, 9)
            last = y + 14 + k * 12
        y = last + 26
        self.text_at(
            LEFT, y, f"Absences: {c['absences']} | Class council decision: {c['decision']}", 9.5
        )
        y += 44
        for x, label in (
            (LEFT, "Head of Class Council:"),
            (LEFT + 190, "Principal:"),
            (LEFT + 380, "Parent/Guardian:"),
        ):
            self.text_at(x, y - 14, label, 9, "B")
            self.pdf.set_draw_color(0, 0, 0)
            self.pdf.line(x, y, x + 150, y)
        self.y = y


def render_card(card, rules, variant=None):
    """Draw the card. Returns (pdf bytes, truth sheet). `variant` makes a deliberately bad card."""
    w = Writer(card, rules, variant)
    w.new_page()
    w.header()
    w.table_header()
    rows = []
    for number, s in enumerate(card["subjects"], start=1):
        pages = w.draw_row(number, s)
        rows.append((number, s, pages))
    w.foot()
    return bytes(w.pdf.output()), truth_sheet(card, rows, w.pdf.page_no())


def truth_sheet(card, rows, page_count):
    """What a correct reader must find on this card. Every value as printed."""

    def text(x):
        return None if x is None else two_places(x)

    sheet_rows, records = [], []
    for number, s, pages in rows:
        grade = s["printed_grade"]
        sheet_rows.append(
            {
                "row": number,
                "subject": s["name"],
                "coefficient": int(s["coef"]),
                "terms": [text(t) for t in s["terms"]],
                "annual_score": text(s["printed_annual"]),
                "weighted_score": text(s["printed_weighted"]),
                "grade": grade,
                "grade_word": word(grade) if grade else None,
                "remark": s["remark"],
                "page": pages[0],
                "remark_pages": pages,
            }
        )
        for term, score in enumerate(s["terms"], start=1):
            records.append(
                {
                    "subject": s["name"],
                    "term": term,
                    "raw_score": text(score),
                    "missing": score is None,
                    "denominator": int(card["denominator"]),
                    "coefficient": int(s["coef"]),
                    "year": card["year"],
                    "stage": card["stage"],
                    "page": pages[0],
                    "row": number,
                }
            )
    return {
        "card_id": card["card_id"],
        "card_version": card["card_version"],
        "learner_ref": card["learner_id"],
        "name": card["name"],
        "date_of_birth": card["dob"],
        "gender": "Female" if card["gender"] == "F" else "Male",
        "class": card["class_name"],
        "stage": card["stage"],
        "academic_year": card["year"],
        "title_year": card["year"],
        "pages": page_count,
        "rows": sheet_rows,
        "total_coefficients": int(card["printed_total_coef"]),
        "total_weighted": text(card["printed_total_weighted"]),
        "annual_average": text(card["printed_average"]),
        "grade": card["printed_grade"],
        "grade_word": word(card["printed_grade"]),
        "conduct": card["conduct"],
        "absences": card["absences"],
        "council_decision": card["decision"],
        "records": records,
    }
