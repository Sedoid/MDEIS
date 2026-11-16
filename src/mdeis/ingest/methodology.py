"""Read the methodology document. Ordinary code, no model.

The methodology is MDEIS's own evaluation framework, a numbered document. This module turns it
into numbered sections with their page, compares it with the version in force and with the
sections the school's rules rest on, and keeps the old version in force until a named person
acknowledges a change.

Section numbering follows the document's own (`4`, `6.4`, `4.S3`, `6.3.B`):

* a top-level heading `n. Title` is a section only if `n` is the next number in sequence (this
  stops a numbered list item from being read as a section);
* `n.m Title` is a sub-section of section `n`;
* `Step n: Title` inside section 4 is `4.Sn`;
* `A. Title` to `F. Title` under a sub-section is `n.m.A`.

In Markdown the heading marks (`##`, `###`, `####`) say which kind a line is. In a PDF the size
and the words do, relative to the document's own body text (not absolute sizes, and not a font
name): a line clearly larger than the body whose text is `n. Title` is a section, `n.m Title` or
`Step n: Title` a sub-section or step. A line that starts `A.` to `F.` and is bold is a lettered
measure (only where a font name says bold). Numbered list items are body size, so they stay text.
A table that a PDF draws with borders is read as one Markdown-style line per row
(`| cell | cell |`), because the PDF's own text order mixes the cells of a row.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass

from mdeis.db import diary

from . import methodology_gate

# --- limits ------------------------------------------------------------------------------------
MAX_BYTES = 20_000_000
MAX_PAGES = 80
TEXT_TYPES = (".md", ".markdown", ".txt")

# --- how a PDF's headings are told from its text (layout details, not school rules) -----------
# A heading line is at least this many times the body size. The body size is the size most
# characters are set in. In the two sample documents the smallest heading is 1.14 (a 12.8 pt
# sub-heading over 11.2 pt text) and 1.18 (13 pt over 11 pt) times the body, and table text is
# smaller than the body, so one tenth above is clear of both.
HEADING_MIN_RATIO = 1.1
# Two line sizes this close (points) are the same size: a PDF writer rounds sizes differently.
SIZE_TOL = 0.6
HEADING_WRAP_GAP = 6.0  # a heading line this close (points) under another heading line continues it
# A word counts as inside a table when its centre is within the table's box, give or take this many
# points: a PDF writer lets a word's box poke a little past the cell border.
TABLE_EDGE_TOL = 1.0

ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff"))  # to be removed
BULLET = "●"

MARKDOWN_HEADING = re.compile(r"^(#{1,4}) (.+)$")
SECTION_HEAD = re.compile(r"(\d+)\. (.+)")
SUB_HEAD = re.compile(r"(\d+)\.(\d+) (.+)")
STEP_HEAD = re.compile(r"Step (\d+): (.+)")
MEASURE_HEAD = re.compile(r"([A-F])\. (.+)")
NEW_MARKER = re.compile(r"(\d+\.\d* |Step \d)")
LIST_ITEM = re.compile(r"(- |\d+\. )")

# --- failure kinds and issue kinds ----------------------------
NOT_A_PDF = "not_a_pdf"
NO_TEXT_LAYER = "no_text_layer"
TOO_LARGE = "too_large"
TOO_MANY_PAGES = "too_many_pages"
UNSUPPORTED_TYPE = "unsupported_type"
NO_MARKERS = "no_section_markers"
DUPLICATE_MARKER = "duplicate_section_marker"
ADDED = "section_added"
REMOVED = "section_removed"
CHANGED = "section_changed"
REF_MISSING = "template_ref_missing"
REF_CHANGED = "referenced_section_changed"
UNCLEAR = "methodology_unclear"  # failure_kind of a document the clarity gate turned back


@dataclass(frozen=True)
class Failure:
    kind: str
    detail: str


@dataclass(frozen=True)
class Section:
    id: str | None  # None for the title block before section 1
    title: str
    level: int  # 0 title block, 1 section, 2 sub-section or step, 3 lettered measure
    page: int
    text: str
    sha256: str


@dataclass(frozen=True)
class Parsed:
    sections: tuple
    pages: int
    issues: tuple = ()  # problems with the document's own numbering: ({kind, ref_id, detail, page})

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> Parsed:
        return Parsed(
            sections=tuple(Section(**s) for s in data["sections"]),
            pages=data["pages"],
            issues=tuple(data["issues"]),
        )


def normalise(text: str) -> str:
    """Unicode NFC, zero-width characters removed, bullets as '- ', spaces collapsed per line."""
    text = unicodedata.normalize("NFC", text).translate(ZERO_WIDTH)
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(BULLET):
            line = "- " + line.lstrip(BULLET + " ").strip()
        lines.append(" ".join(line.split()))
    return "\n".join(lines).strip()


def fingerprint(text: str) -> str:
    """The hash of a section's words: it does not change with line breaks or spacing."""
    return hashlib.sha256(" ".join(normalise(text).split()).encode("utf-8")).hexdigest()


# "Step n:" lines are sections only inside this top-level section of the document (its process
# chapter). It is the document's own numbering, not a school rule.
STEP_PARENT = "4"

# --- building sections from headings and text --------------------------------------------------
class _Builder:
    def __init__(self):
        self.top = 0
        self.sub = None
        self.sections = []  # dicts: id, title, level, page, lines
        self.issues = []
        self.current = {"id": None, "title": "", "level": 0, "page": 1, "lines": []}
        self.seen = set()

    def _start(self, section_id, title, level, page) -> None:
        if section_id in self.seen:
            self.issues.append(
                {
                    "kind": DUPLICATE_MARKER,
                    "ref_id": section_id,
                    "detail": f"section {section_id} appears more than once",
                    "page": page,
                }
            )
        self.seen.add(section_id)
        self.sections.append(self.current)
        self.current = {"id": section_id, "title": title, "level": level, "page": page, "lines": []}

    def heading(self, level: int, text: str, page: int) -> bool:
        """Offer a heading. True if it is a section marker (and so starts a section)."""
        if level == 2:
            m = SECTION_HEAD.fullmatch(text)
            if m and int(m.group(1)) == self.top + 1:
                self.top, self.sub = int(m.group(1)), None
                self._start(str(self.top), m.group(2).strip(), 1, page)
                return True
        elif level == 3:
            m = SUB_HEAD.fullmatch(text)
            if m and int(m.group(1)) == self.top:
                self.sub = f"{m.group(1)}.{m.group(2)}"
                self._start(self.sub, m.group(3).strip(), 2, page)
                return True
            m = STEP_HEAD.fullmatch(text)
            if m and self.top == int(STEP_PARENT):
                self._start(f"{STEP_PARENT}.S{m.group(1)}", m.group(2).strip(), 2, page)
                return True
        elif level == 4:
            m = MEASURE_HEAD.fullmatch(text)
            if m and self.sub:
                self._start(f"{self.sub}.{m.group(1)}", m.group(2).strip(), 3, page)
                return True
        return False

    def text(self, line: str) -> None:
        self.current["lines"].append(line)

    def finish(self, pages: int) -> Parsed:
        self.sections.append(self.current)
        out = []
        for s in self.sections:
            body = normalise("\n".join(s["lines"]))
            if s["id"] is None and not body:
                continue  # no title block when the document starts with section 1
            out.append(Section(s["id"], s["title"], s["level"], s["page"], body, fingerprint(body)))
        numbered = any(s.id for s in out)
        issues = list(self.issues)
        if not numbered:
            issues.append(
                {
                    "kind": NO_MARKERS,
                    "ref_id": None,
                    "detail": "no numbered sections were found, so the document is one block",
                    "page": 1,
                }
            )
        return Parsed(tuple(out), pages, tuple(issues))


def parse_markdown(text: str) -> Parsed:
    builder = _Builder()
    for line in text.splitlines():
        m = MARKDOWN_HEADING.match(line.strip())
        if m:
            if builder.heading(len(m.group(1)), normalise(m.group(2)), 1):
                continue
            builder.text(m.group(2))  # a heading that is not a section marker is just text
            continue
        builder.text(line)
    return builder.finish(1)


def body_size(pages) -> float:
    """The size most characters are set in: the body text. 0.0 for a document with no text.

    On a tie the smaller size wins, so the answer does not depend on the order of the lines.
    """
    counts: dict = {}
    for lines in pages:
        for line in lines:
            text = normalise(line["text"])
            if text:
                counts[line["size"]] = counts.get(line["size"], 0) + len(text)
    if not counts:
        return 0.0
    return max(counts, key=lambda size: (counts[size], -size))


def parse_pdf_lines(pages) -> Parsed:
    """`pages`: for each page, its lines as dicts (text, bold, size, top, bottom)."""
    builder = _Builder()
    body = body_size(pages)
    last = None  # the previous line, with its heading level and size if it was a heading
    for number, lines in enumerate(pages, start=1):
        for line in lines:
            text = normalise(line["text"])
            if not text:
                continue
            level = _heading_level(line, text, body)
            wraps = (
                last is not None
                and last["page"] == number
                and last["level"] in (2, 3)
                and _is_heading_size(line["size"], body)
                and abs(line["size"] - last["size"]) <= SIZE_TOL
                and line["top"] - last["bottom"] < HEADING_WRAP_GAP
                and not NEW_MARKER.match(text)
            )
            if wraps:
                builder.current["title"] += " " + text
                last = {**last, "bottom": line["bottom"]}
                continue
            if level and builder.heading(level, text, number):
                last = {
                    "page": number,
                    "level": level,
                    "bottom": line["bottom"],
                    "size": line["size"],
                    "table": False,
                }
                continue
            in_table = bool(line.get("table"))
            if (
                text.startswith(BULLET)
                or LIST_ITEM.match(text)
                or last is None
                or last["level"]
                or in_table != last["table"]  # a table is set apart from the text around it
            ):
                builder.text("")
            builder.text(text)
            last = {
                "page": number,
                "level": None,
                "bottom": line["bottom"],
                "size": line["size"],
                "table": in_table,
            }
    return builder.finish(len(pages))


def _is_heading_size(size: float, body: float) -> bool:
    return body > 0 and size >= body * HEADING_MIN_RATIO


def _heading_level(line, text, body) -> int | None:
    """2 for a section, 3 for a sub-section or step, 4 for a lettered measure, else None.

    A section or sub-section is a line clearly larger than the body whose words fit the pattern.
    The pattern says which kind it is; the sequence rule in `_Builder.heading` stops a list item.
    A lettered measure is body size, so only the font name (bold) tells it from a list item.
    """
    if line.get("table"):
        return None  # a table row starts with a bar, so no pattern fits it; say so anyway
    if _is_heading_size(line["size"], body):
        if SECTION_HEAD.fullmatch(text):
            return 2
        if SUB_HEAD.fullmatch(text) or STEP_HEAD.fullmatch(text):
            return 3
        return None
    if line["bold"] and MEASURE_HEAD.fullmatch(text):
        return 4
    return None


def read_pdf_lines(path) -> list | Failure:
    """The lines of every page with their font, by pdfplumber. Or the reason it cannot be read."""
    import pdfplumber

    try:
        with pdfplumber.open(path) as pdf:
            if len(pdf.pages) > MAX_PAGES:
                return Failure(TOO_MANY_PAGES, f"{len(pdf.pages)} pages: the limit is {MAX_PAGES}")
            pages = _join_split_rows([_page_lines(p) for p in pdf.pages])
    except Exception as e:  # a damaged PDF: pdfminer raises many kinds
        return Failure(NOT_A_PDF, f"the PDF cannot be opened ({type(e).__name__})")
    if not any(pages):
        return Failure(NO_TEXT_LAYER, "the PDF has no text layer; scans are not supported")
    return pages


def _page_lines(page) -> list:
    """The lines of one page, top to bottom: text lines, and a table as one line per row.

    Where pdfplumber finds a table with drawn borders, its words are taken out of the page's text
    and each row is put back as `| cell | cell |` (inner line breaks in a cell become a space, an
    empty cell stays empty), in the place of the table. Where it finds none, the lines are the
    words grouped by height, as they always were.
    """
    words = page.extract_words(extra_attrs=["fontname", "size"], use_text_flow=True)
    tables = page.find_tables()
    boxes = [t.bbox for t in tables]
    loose, held = [], [[] for _ in boxes]
    for w in words:
        home = next((i for i, box in enumerate(boxes) if _in_box(w, box)), None)
        (loose if home is None else held[home]).append(w)
    lines = _text_lines(loose)
    rows = []
    for table, inside in zip(tables, held, strict=True):
        rows += _table_lines(table, inside)
    rows.sort(key=lambda row: row["top"])
    merged, i = [], 0
    for line in lines:  # text lines keep their order; a row goes before the first line below it
        while i < len(rows) and rows[i]["top"] <= line["top"]:
            merged.append(rows[i])
            i += 1
        merged.append(line)
    return merged + rows[i:]


def _text_lines(words) -> list:
    groups = {}
    for w in words:
        groups.setdefault(round(w["top"] / 2), []).append(w)
    out = []
    for key in sorted(groups):
        ws = sorted(groups[key], key=lambda w: w["x0"])
        out.append(
            {
                "text": " ".join(w["text"] for w in ws),
                "bold": "Bold" in ws[0]["fontname"],
                "size": round(ws[0]["size"], 1),
                "top": ws[0]["top"],
                "bottom": max(w["bottom"] for w in ws),
            }
        )
    return out


def _in_box(word, box) -> bool:
    x0, top, x1, bottom = box
    centre = (word["x0"] + word["x1"]) / 2
    inside_across = x0 - TABLE_EDGE_TOL <= centre <= x1 + TABLE_EDGE_TOL
    return inside_across and top - TABLE_EDGE_TOL <= word["top"] <= bottom + TABLE_EDGE_TOL


def _table_lines(table, inside) -> list:
    """One line per table row. The size is the one most characters of the table are set in.

    A row keeps its place (its own box) even when rows above it were empty and left out.
    """
    if not inside:
        return []
    counts: dict = {}
    for w in inside:
        size = round(w["size"], 1)
        counts[size] = counts.get(size, 0) + len(w["text"])
    size = max(counts, key=lambda s: (counts[s], -s))
    cells, kept = _clean_cells(table.extract())
    spans = _row_spans(table, len(cells))
    out = []
    for index, (row, (top, bottom)) in enumerate(zip(cells, spans, strict=True)):
        if not any(row):
            continue  # a row with nothing in it (after the empty columns went) is not a row
        out.append(
            {
                "text": _row_text(row),
                "bold": False,
                "size": size,
                "top": top,
                "bottom": bottom,
                "table": True,
                "cells": row,  # kept so a row cut by a page break can be joined (below)
                "columns": _row_columns(table, index, kept),
            }
        )
    return out


def _row_text(cells) -> str:
    return "| " + " | ".join(cells) + " |"


def _clean_cells(cells) -> tuple:
    """The cells of one table as text, and the indexes of the columns kept.

    No None, one space between words, no column that is empty in every row. A drawn table can
    come back with more columns than it shows (pdfplumber found five for a table of three, with
    an empty edge on each side). A column that is empty in every row is dropped; a column empty
    in only some rows is kept. Short rows are padded. The order is kept.
    """
    rows = [[" ".join((cell or "").split()) for cell in row] for row in cells]
    width = max((len(row) for row in rows), default=0)
    rows = [row + [""] * (width - len(row)) for row in rows]
    keep = [j for j in range(width) if any(row[j] for row in rows)]
    return [[row[j] for j in keep] for row in rows], keep


def _row_columns(table, index, kept):
    """(left, right) of each kept column of one row, from the row's cell boxes. None if unknown."""
    try:
        boxes = table.rows[index].cells
        return [None if boxes[j] is None else (boxes[j][0], boxes[j][2]) for j in kept]
    except (AttributeError, IndexError, TypeError, ValueError):
        return None


def _row_spans(table, count) -> list:
    """(top, bottom) of each row. If the table does not give its rows' boxes, the table's own."""
    try:
        boxes = [row.bbox for row in table.rows]
    except (AttributeError, ValueError, TypeError):  # no rows, or a row with no cell
        boxes = []
    if len(boxes) == count and all(box is not None for box in boxes):
        return [(box[1], box[3]) for box in boxes]
    return [(table.bbox[1], table.bbox[3])] * count


def _join_split_rows(pages) -> list:
    """Join the end of a table row that a page break cut off to the row it belongs to.

    Deliberately narrow. It acts only when the first thing on a page is a table row and the last
    thing on the page before is a table row too, and the first row is the end of a row, not a row of
    its own: no cell of it falls in the first column of the row above (where its number is), every
    cell of it lies in a column of that row, and no two share one. The row above then holds the
    joined text (cell by cell, one space between two non-empty texts) and the first row is removed.
    Anything else is left as it is. Where the rows give no cell boxes, the same columns and an empty
    first cell are required instead.
    """
    for before, page in zip(pages, pages[1:], strict=False):
        if not before or not page:
            continue
        last, first = before[-1], page[0]
        if not (last.get("table") and first.get("table")):
            continue
        joined = _joined_cells(last, first)
        if joined is not None:
            last["cells"], last["text"] = joined, _row_text(joined)
            del page[0]
    return pages


def _joined_cells(last, first):
    """The cells of `last` with the cells of `first` added, or None if `first` is not its end."""
    above, cells = last["cells"], first["cells"]
    here = [j for j, text in enumerate(cells) if text]
    if not here:
        return None
    if last["columns"] is None or first["columns"] is None:
        if len(cells) == len(above) and not cells[0]:
            return [_join_texts(a, b) for a, b in zip(above, cells, strict=True)]
        return None
    target = {j: _column_of(first["columns"][j], last["columns"]) for j in here}
    if None in target.values() or target[here[0]] == 0:
        return None
    if len(set(target.values())) < len(target):
        return None
    joined = list(above)
    for j, k in target.items():
        joined[k] = _join_texts(above[k], cells[j])
    return joined


def _column_of(span, columns):
    """The index of the column the most of `span` lies in, or None if it lies in none."""
    best, most = None, 0.0
    for k, column in enumerate(columns):
        if span is None or column is None:
            continue
        overlap = min(span[1], column[1]) - max(span[0], column[0])
        if overlap > most:
            best, most = k, overlap
    return best


def _join_texts(a, b) -> str:
    return " ".join(part for part in (a, b) if part)


def parse_methodology(path) -> Parsed | Failure:
    """Read a methodology file: a PDF with a text layer, or Markdown."""
    import os

    try:
        size = os.path.getsize(path)
    except OSError as e:
        return Failure(NOT_A_PDF, f"the file cannot be read ({type(e).__name__})")
    if size > MAX_BYTES:
        return Failure(TOO_LARGE, f"{size} bytes is over the limit of {MAX_BYTES}")
    name = str(path).lower()
    if name.endswith(TEXT_TYPES):
        with open(path, encoding="utf-8") as f:
            return parse_markdown(f.read())
    if name.endswith(".pdf"):
        with open(path, "rb") as f:
            if f.read(5) != b"%PDF-":
                return Failure(NOT_A_PDF, "the file does not start with %PDF-")
        lines = read_pdf_lines(path)
        return lines if isinstance(lines, Failure) else parse_pdf_lines(lines)
    return Failure(UNSUPPORTED_TYPE, "a methodology document is a PDF or a Markdown file")


# --- comparing ---------------------------------------------------------------------------------
def _issue(kind, ref_id, detail, page=None) -> dict:
    return {"kind": kind, "ref_id": ref_id, "detail": detail, "page": page}


def diff_sections(previous, new) -> list:
    """What was added, removed or changed from `previous` to `new` (two lists of Section)."""
    before = {s.id: s for s in previous if s.id}
    after = {s.id: s for s in new if s.id}
    out = []
    for s in new:
        if s.id and s.id not in before:
            out.append(_issue(ADDED, s.id, f"{s.id} {s.title}", s.page))
    for s in previous:
        if s.id and s.id not in after:
            out.append(_issue(REMOVED, s.id, f"{s.id} {s.title}", s.page))
    for s in new:
        if s.id and s.id in before and before[s.id].sha256 != s.sha256:
            out.append(_issue(CHANGED, s.id, f"{s.id} {s.title}", s.page))
    return out


def check_template_refs(sections, refs: dict, moved: set) -> list:
    """Which rules rest on a section that is not here, or that changed or was removed.

    `refs` is {rule id: the section it implements} from the rules' `method_ref`. `moved` holds the
    ids of the changed and removed sections. A rule rests on a section when its reference is that
    section or sits inside it.
    """
    ids = {s.id for s in sections if s.id}
    out = []
    for rule in sorted(refs):
        ref = refs[rule]
        if ref not in ids:
            detail = f"{rule} rests on section {ref}, which is not here"
            out.append(_issue(REF_MISSING, rule, detail))
        for section in sorted(moved):
            if ref == section or ref.startswith(section + "."):
                detail = f"{rule} rests on section {ref}; section {section} changed or was removed"
                out.append({**_issue(REF_CHANGED, rule, detail), "section": section})
    return out


# --- storing -----------------------------------------------------------------------------------
def active_sections(conn, excluding: str | None = None) -> tuple:
    """(document id, sections) of the methodology in force, or (None, [])."""
    row = conn.execute(
        "SELECT document_id FROM documents WHERE kind = 'methodology' AND active "
        "AND document_id IS DISTINCT FROM %s",
        (excluding,),
    ).fetchone()
    if row is None:
        return None, []
    return row[0], sections_of(conn, row[0])


def sections_of(conn, document_id: str) -> list:
    rows = conn.execute(
        "SELECT section_id, title, page, text, text_sha256 FROM method_chunks "
        "WHERE document_id = %s ORDER BY ordinal",
        (document_id,),
    ).fetchall()
    return [
        Section(section_id, title or "", _level(section_id), page or 1, text, digest)
        for section_id, title, page, text, digest in rows
    ]


def _level(section_id) -> int:
    if not section_id:
        return 0
    return 1 + section_id.count(".")


def template_refs(conn) -> dict:
    """{rule id: section} from the `method_ref` of the rules of the active rulebook version."""
    rows = conn.execute(
        "SELECT c.clause_id, c.method_ref FROM clauses c JOIN templates t USING (version_name) "
        "WHERE t.active AND c.method_ref IS NOT NULL AND c.method_ref <> ''"
    ).fetchall()
    return {clause_id: str(ref) for clause_id, ref in rows}


def store_methodology(conn, document_id: str, parsed: Parsed) -> dict:
    """Write the sections and the findings, and set the status, on `conn` (the caller commits).

    The clarity gate (`methodology_gate`, rules in `templates/gate-v1.yaml`) runs first. Any fault:
    the document is `failed` with failure_kind `methodology_unclear`, the faults are kept as
    `document_issues`, it is not in force, and there is no comparison. The previous methodology
    stays in force.

    No fault, and no finding from the comparison: the document is `done` and becomes the
    methodology in force. Any finding: it is `needs_review` and is not in force; the previous one
    stays until `acknowledge`.
    """
    previous_id, previous = active_sections(conn, excluding=document_id)
    for ordinal, s in enumerate(parsed.sections, start=1):
        conn.execute(
            "INSERT INTO method_chunks (document_id, section_id, title, page, ordinal, text, "
            "text_sha256) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (document_id, s.id, s.title or None, s.page, ordinal, s.text, s.sha256),
        )
    refs = template_refs(conn)
    faults = methodology_gate.check(parsed.sections, refs)
    if faults:
        return _reject(conn, document_id, parsed, faults, previous_id)
    issues = list(parsed.issues)
    moved = set()
    if previous_id:
        found = diff_sections(previous, parsed.sections)
        issues += found
        moved = {i["ref_id"] for i in found if i["kind"] in (REMOVED, CHANGED)}
    issues += check_template_refs(parsed.sections, refs, moved)
    for i in issues:
        conn.execute(
            "INSERT INTO document_issues (document_id, kind, ref_id, page, detail) "
            "VALUES (%s, %s, %s, %s, %s)",
            (document_id, i["kind"], i["ref_id"], i["page"], i["detail"]),
        )
    in_force = not issues
    if in_force and previous_id:
        conn.execute("UPDATE documents SET active = false WHERE document_id = %s", (previous_id,))
    status = "done" if in_force else "needs_review"
    conn.execute(
        "UPDATE documents SET status = %s, active = %s, finished_at = now() WHERE document_id = %s",
        (status, in_force, document_id),
    )
    return {
        "status": status,
        "active": in_force,
        "sections": len(parsed.sections),
        "issues": len(issues),
        "previous": previous_id,
    }


def _reject(conn, document_id: str, parsed: Parsed, faults: list, previous_id) -> dict:
    """The gate found faults: keep them against the document and fail it. Nothing else runs."""
    pages = {s.id: s.page for s in parsed.sections if s.id}
    for fault in faults:
        conn.execute(
            "INSERT INTO document_issues (document_id, kind, ref_id, page, detail) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                document_id,
                fault.code,
                fault.section,
                pages.get(fault.section),
                methodology_gate.describe(fault),
            ),
        )
    detail = f"{len(faults)} fault(s) found; each names its section. Fix them and upload it again."
    conn.execute(
        "UPDATE documents SET status = 'failed', failure_kind = %s, failure_detail = %s, "
        "active = false, finished_at = now() WHERE document_id = %s",
        (UNCLEAR, detail, document_id),
    )
    return {
        "status": "failed",
        "failure_kind": UNCLEAR,
        "active": False,
        "sections": len(parsed.sections),
        "issues": len(faults),
        "previous": previous_id,
    }


class AckRefused(Exception):
    """The acknowledgement was refused. The reason has been written to the diary."""


def acknowledge(conn, document_id: str, by: str, note: str = "", url: str | None = None) -> dict:
    """A named person puts a methodology that needed review in force. Logged, even when refused.

    Runs on `conn` and does not commit. Only a document in `needs_review` can be acknowledged.
    """
    name = (by or "").strip()
    inputs = {"document_id": document_id, "by": name, "note": note}
    reason = None
    row = conn.execute(
        "SELECT kind, status FROM documents WHERE document_id = %s", (document_id,)
    ).fetchone()
    if not name:
        reason = "an acknowledgement needs the name of the person who made it"
    elif row is None:
        reason = f"there is no document {document_id}"
    elif row[0] != "methodology":
        reason = f"{document_id} is not a methodology document"
    elif row[1] != "needs_review":
        reason = f"{document_id} is {row[1]}, not waiting for review"
    if reason:
        diary.write(
            "ack_methodology",
            inputs=inputs,
            outputs={"reason": reason},
            outcome="refused",
            approver_name=name or None,
            url=url,
        )
        raise AckRefused(reason)
    conn.execute(
        "UPDATE documents SET active = false WHERE kind = 'methodology' AND active "
        "AND document_id <> %s",
        (document_id,),
    )
    conn.execute(
        "UPDATE documents SET status = 'done', active = true, acknowledged_by = %s, "
        "acknowledged_at = now() WHERE document_id = %s",
        (name, document_id),
    )
    event = diary.write(
        "ack_methodology",
        inputs=inputs,
        outputs={"document_id": document_id, "status": "done", "active": True},
        outcome="ok",
        approver_id=name,
        approver_name=name,
        url=url,
    )
    return {"document_id": document_id, "acknowledged_by": name, "logged_event_id": event}


def gate_main(argv: list) -> int:
    """Print the faults the clarity gate finds in a file, as JSON.

    `--gate FILE [--template RULEBOOK.yaml]`. The template, if given, supplies the `method_ref`
    list that GT-7 checks. Exit 1 if the file cannot be read.
    """
    template = None
    if len(argv) == 3 and argv[1] == "--template":
        argv, template = argv[:1], argv[2]
    if len(argv) != 1:
        print("usage: python -m mdeis.ingest.methodology --gate FILE [--template FILE]",
              file=sys.stderr)
        return 2
    result = parse_methodology(argv[0])
    if isinstance(result, Failure):
        print(json.dumps({"failure": asdict(result)}))
        return 1
    refs = {}
    if template:
        from mdeis.core.template import load_template

        refs = {c.id: c.method_ref for c in load_template(template).clauses() if c.method_ref}
    faults = methodology_gate.check(result.sections, refs)
    print(json.dumps({"faults": [f.to_dict() for f in faults]}))
    return 0


def main(argv=None) -> int:
    """Print what a file holds as JSON: `python -m mdeis.ingest.methodology FILE`."""
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--gate"]:
        return gate_main(argv[1:])
    if len(argv) != 1:
        print("usage: python -m mdeis.ingest.methodology FILE", file=sys.stderr)
        return 2
    result = parse_methodology(argv[0])
    if isinstance(result, Failure):
        print(json.dumps({"failure": asdict(result)}))
    else:
        print(json.dumps({"parsed": result.to_dict()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
