"""Reads text back out of a PDF, for the tests of the PDF writer and the report export (T-056).

`lines` is a tiny reader that knows only the format our own writer makes (one `Tj` string per line,
uncompressed). It is written here, apart from the code under test, so the tests do not trust the
writer to read its own work back. `plumber_text` reads the same bytes with pdfplumber, a real PDF
reader, as the brief asks.
"""

import io
import re

_STREAM = re.compile(rb"stream\n(.*?)\nendstream", re.S)
_SHOW = re.compile(rb"\(((?:\\.|[^\\)])*)\) Tj", re.S)
_ESCAPE = re.compile(rb"\\([0-7]{3}|.)", re.S)


def _unescape(raw: bytes) -> bytes:
    def one(match):
        found = match.group(1)
        if len(found) == 3 and found.isdigit():
            return bytes([int(found, 8)])
        return found  # \( \) \\ stand for the character after the backslash

    return _ESCAPE.sub(one, raw)


def pages(pdf: bytes) -> list:
    """One list of text lines per page, in the order they were drawn."""
    return [
        [_unescape(raw).decode("cp1252") for raw in _SHOW.findall(stream)]
        for stream in _STREAM.findall(pdf)
    ]


def lines(pdf: bytes) -> list:
    return [line for page in pages(pdf) for line in page]


def plumber_text(pdf: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf)) as document:
        return "\n".join((page.extract_text() or "") for page in document.pages)


def squashed(text: str) -> str:
    """The text with every run of white space as one space, so a line break does not matter."""
    return " ".join(text.split())
