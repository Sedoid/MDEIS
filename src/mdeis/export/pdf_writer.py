"""A small PDF writer. Python standard library only, no new dependency (owner decision for T-056).

What it does: A4 pages of plain text in the two standard fonts every PDF viewer already has
(Helvetica and Helvetica-Bold, not embedded), with simple word wrap, page breaks and an optional
footer line on every page. What it does not do: pictures, tables, links, other fonts, right-to-left
text.

Text is written in WinAnsi (Windows-1252). A character that has no place in it is written as `?`, so
the mark is visible on the page, and nothing crashes. A control character becomes a space. Runs of
spaces inside a line are kept; a line is broken only at a space.

The same input gives the same bytes. Nothing random goes in: no file ID, no clock. The creation date
is the `created` argument. The content is not compressed, so the bytes do not depend on the zlib
version of the machine, and the text can be read back with any PDF reader.

The letter widths below are the standard Helvetica widths (thousandths of the font size), used only
to decide where a line breaks. A character outside the ASCII table gets one default width, so a line
with such letters may break a little early or late. The viewer draws the true letter shapes.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

PAGE_WIDTH, PAGE_HEIGHT = 595, 842  # A4, in points
MARGIN_X = 56
MARGIN_TOP = 64
MARGIN_BOTTOM = 64
FOOTER_Y = 32
FOOTER_SIZE = 8
REPLACEMENT = b"?"

# style: (font name, size, line height, space before). These are layout sizes, not rules.
STYLES = {
    "title": ("F2", 16, 22, 0),
    "heading": ("F2", 12, 17, 10),
    "text": ("F1", 10, 14, 0),
}

_ASCII = "".join(chr(c) for c in range(32, 127))
_HELVETICA = (
    "278 278 355 556 556 889 667 191 333 333 389 584 278 333 278 278 556 556 556 556 556 556 556 "
    "556 556 556 278 278 584 584 584 556 1015 667 667 722 722 667 611 778 722 278 500 667 556 833 "
    "722 778 667 778 722 667 611 722 667 944 667 667 611 278 278 278 469 556 333 556 556 500 556 "
    "556 278 556 556 222 222 500 222 833 556 556 556 556 333 500 278 556 500 722 500 500 500 334 "
    "260 334 584"
)
_HELVETICA_BOLD = (
    "278 333 474 556 556 889 722 238 333 333 389 584 278 333 278 278 556 556 556 556 556 556 556 "
    "556 556 556 333 333 584 584 584 611 975 722 722 722 722 667 611 778 722 278 556 722 611 833 "
    "722 778 667 778 722 667 611 722 667 944 667 667 611 333 278 333 584 556 333 556 611 556 611 "
    "556 333 611 611 278 278 556 278 889 611 611 611 611 389 556 333 611 556 778 556 556 500 389 "
    "280 389 584"
)


def _table(widths: str) -> dict:
    numbers = [int(n) for n in widths.split()]
    if len(numbers) != len(_ASCII):  # a typing slip in the table above must fail loudly
        raise ValueError("the width table must hold one width for each of the 95 ASCII characters")
    return {ord(c): w for c, w in zip(_ASCII, numbers, strict=True)}


WIDTHS = {"F1": _table(_HELVETICA), "F2": _table(_HELVETICA_BOLD)}
DEFAULT_WIDTH = {"F1": 556, "F2": 611}  # for a character outside the ASCII table
FONT_NAMES = {"F1": "Helvetica", "F2": "Helvetica-Bold"}


def to_winansi(text: str) -> bytes:
    """The text as WinAnsi bytes. A character WinAnsi lacks gives `?`; a control one a space."""
    out = bytearray()
    for char in text:
        if char < " " or char == "\x7f":
            out += b" "
            continue
        try:
            out += char.encode("cp1252")
        except UnicodeEncodeError:
            out += REPLACEMENT
    return bytes(out)


def text_width(data: bytes, font: str, size: int) -> float:
    table, default = WIDTHS[font], DEFAULT_WIDTH[font]
    return sum(table.get(b, default) for b in data) * size / 1000


def _split_long(word: bytes, font: str, size: int, limit: float) -> list:
    """A word wider than a whole line, cut into pieces that each fit."""
    if text_width(word, font, size) <= limit:
        return [word]
    pieces, current = [], b""
    for i in range(len(word)):
        char = word[i : i + 1]
        if current and text_width(current + char, font, size) > limit:
            pieces.append(current)
            current = b""
        current += char
    pieces.append(current)
    return pieces


def wrap(data: bytes, font: str, size: int, limit: float) -> list:
    """The bytes cut into lines no wider than `limit`, at spaces. Empty input is one empty line."""
    lines, current = [], None
    for word in data.split(b" "):
        candidate = word if current is None else current + b" " + word
        if text_width(candidate, font, size) <= limit:
            current = candidate
            continue
        if current is not None:
            lines.append(current)
        pieces = _split_long(word, font, size, limit)
        lines.extend(pieces[:-1])
        current = pieces[-1]
    if current is not None:
        lines.append(current)
    return lines or [b""]


def literal(data: bytes) -> bytes:
    """A PDF literal string. Brackets and backslashes are escaped; anything outside printable ASCII
    is written as an octal escape, so the file itself holds only ASCII."""
    out = bytearray(b"(")
    for b in data:
        if b in b"()\\":
            out += b"\\" + bytes([b])
        elif 32 <= b < 127:
            out.append(b)
        else:
            out += b"\\%03o" % b
    out += b")"
    return bytes(out)


def pdf_date(moment: datetime) -> bytes:
    """D:YYYYMMDDHHmmSSZ, in UTC. A time with no zone is taken to be UTC."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("D:%Y%m%d%H%M%SZ").encode("ascii")


def layout(blocks: Iterable, footer: str | None = None) -> list:
    """The pages: each a list of (font, size, x, y, bytes), then the footer on every page."""
    limit = PAGE_WIDTH - 2 * MARGIN_X
    top = PAGE_HEIGHT - MARGIN_TOP
    pages: list = [[]]
    y = top

    def new_page():
        nonlocal y
        pages.append([])
        y = top

    for style, text in blocks:
        if style not in STYLES:
            raise ValueError(f"unknown style {style!r}")
        font, size, leading, before = STYLES[style]
        if style != "text" and pages[-1]:
            y -= before
        if style == "heading" and y - leading < MARGIN_BOTTOM:
            new_page()  # a heading is never the last thing on a page
        normal = str(text).replace("\r\n", "\n").replace("\r", "\n")
        for paragraph in normal.split("\n"):
            for line in wrap(to_winansi(paragraph), font, size, limit):
                if y < MARGIN_BOTTOM:
                    new_page()
                pages[-1].append((font, size, MARGIN_X, y, line))
                y -= leading
    if footer is not None:
        note = to_winansi(footer)
        for page in pages:
            page.append(("F1", FOOTER_SIZE, MARGIN_X, FOOTER_Y, note))
    return pages


def _content(page: list) -> bytes:
    out = bytearray()
    for font, size, x, y, line in page:
        out += b"BT /%s %d Tf 1 0 0 1 %d %d Tm " % (font.encode(), size, x, y)
        out += literal(line) + b" Tj ET\n"
    return bytes(out)


def write_pdf(
    blocks: Iterable, *, title: str, created: datetime, footer: str | None = None
) -> bytes:
    """One PDF. `blocks` is a list of (style, text) with style "title", "heading" or "text"."""
    pages = layout(blocks, footer)
    objects: list = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # the page tree, filled in below, once the page numbers are known
        b"<< /Type /Font /Subtype /Type1 /BaseFont /%s /Encoding /WinAnsiEncoding >>"
        % FONT_NAMES["F1"].encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /%s /Encoding /WinAnsiEncoding >>"
        % FONT_NAMES["F2"].encode(),
        b"<< /Title %s /Producer (MDEIS) /CreationDate (%s) >>"
        % (literal(title.encode("ascii", "replace")), pdf_date(created)),
    ]
    kids = []
    for index, page in enumerate(pages):
        page_number = 6 + 2 * index
        kids.append(b"%d 0 R" % page_number)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] "
            b"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents %d 0 R >>"
            % (PAGE_WIDTH, PAGE_HEIGHT, page_number + 1)
        )
        stream = _content(page)
        objects.append(
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)
        )
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(kids), len(pages))

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info 5 0 R >>\n" % (len(objects) + 1)
    out += b"startxref\n%d\n%%%%EOF\n" % xref_at
    return bytes(out)
