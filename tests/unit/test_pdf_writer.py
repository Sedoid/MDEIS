"""The small PDF writer (T-056): standard library only, standard fonts, same input same bytes.

No database, no network. Text is read back with `pdfread.lines` (a reader written for this test, not
the writer's own code) and, where a real reader is wanted, with pdfplumber.

Expected values are worked out by hand from the layout sizes in the writer's docstring:
  * a line is 483 points wide (A4 595 minus 56 on each side);
  * at 10 points, one "W" is 9.44 points wide, one "0" is 5.56, a space is 2.78;
  * the first baseline is 778 (842 - 64), each text line steps down 14, and a line may be drawn
    while its baseline is at least 64.
"""

import ast
import io
import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pdfplumber
import pytest
from pdfread import lines, pages, plumber_text, squashed

from mdeis.export import pdf_writer
from mdeis.export.pdf_writer import write_pdf

WHEN = datetime(2026, 11, 17, 9, 30, 15, tzinfo=UTC)
SRC = Path(pdf_writer.__file__)


def make(blocks, footer=None, created=WHEN, title="Report R-000001"):
    return write_pdf(blocks, title=title, created=created, footer=footer)


def one_text(text, **kw):
    return make([("text", text)], **kw)


# --------------------------------------------------------------------------- the file's shape
def test_it_is_a_pdf_with_a_header_a_trailer_and_a_cross_reference_table_that_points_true():
    pdf = one_text("hello")
    assert pdf.startswith(b"%PDF-1.4\n") and pdf.endswith(b"%%EOF\n")
    start = int(re.search(rb"startxref\n([0-9]+)\n%%EOF\n$", pdf).group(1))
    assert pdf[start:].startswith(b"xref\n0 ")
    table = pdf[start:].split(b"\n")
    count = int(table[1].split()[1])
    entries = table[2: 2 + count]
    assert entries[0] == b"0000000000 65535 f "
    for number, entry in enumerate(entries[1:], start=1):
        assert len(entry) + 1 == 20, "each entry is exactly 20 bytes with its line end"
        offset = int(entry[:10])
        assert pdf[offset:].startswith(b"%d 0 obj\n" % number), number


def test_the_stream_lengths_are_true():
    pdf = make([("text", "one"), ("heading", "two"), ("text", "three")], footer="foot")
    found = re.findall(rb"/Length ([0-9]+) >>\nstream\n(.*?)\nendstream", pdf, re.S)
    assert found
    for length, body in found:
        assert int(length) == len(body)


def test_the_fonts_are_the_two_standard_ones_and_are_not_embedded():
    pdf = one_text("hello")
    assert b"/BaseFont /Helvetica " in pdf and b"/BaseFont /Helvetica-Bold " in pdf
    assert b"/Encoding /WinAnsiEncoding" in pdf
    assert b"FontFile" not in pdf and b"/Filter" not in pdf, "nothing embedded, nothing compressed"


def test_the_page_is_a4():
    assert b"/MediaBox [0 0 595 842]" in one_text("x")


def test_the_file_holds_only_ascii_after_the_header_comment():
    pdf = make([("text", "café € Ṅ ok")])
    body = pdf.split(b"\n", 2)[2]
    assert all(b < 128 for b in body)


# --------------------------------------------------------------------------- same input, same bytes
def test_the_same_input_gives_the_same_bytes():
    blocks = [("title", "Report R-000001"), ("heading", "Findings"), ("text", "body " * 300)]
    assert make(blocks, footer="Practice data only") == make(blocks, footer="Practice data only")


def test_only_the_time_argument_changes_the_file_when_the_time_changes():
    a = one_text("same")
    b = one_text("same", created=WHEN + timedelta(minutes=1))
    assert a != b
    assert b"D:20261117093015Z" in a and b"D:20261117093115Z" in b
    assert a.replace(b"093015Z", b"XXXXXXX") == b.replace(b"093115Z", b"XXXXXXX")


def test_a_time_in_another_zone_is_written_in_utc_and_a_bare_time_is_taken_as_utc():
    local = WHEN.astimezone(timezone(timedelta(hours=3)))
    assert make([("text", "x")], created=local) == one_text("x")
    assert make([("text", "x")], created=WHEN.replace(tzinfo=None)) == one_text("x")


def test_the_writer_reads_no_clock_and_no_randomness():
    tree = ast.parse(SRC.read_text("utf-8"))
    imported = {
        a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    } | {
        n.module.split(".")[0] for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module
    }
    assert imported <= {"__future__", "collections", "datetime"}, imported
    calls = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not calls & {"now", "utcnow", "today", "time", "urandom", "random"}, calls


# --------------------------------------------------------------------------- the text
def test_text_is_written_one_line_per_line_in_order():
    pdf = make([("title", "T"), ("text", "first\nsecond"), ("heading", "H"), ("text", "third")])
    assert lines(pdf) == ["T", "first", "second", "H", "third"]


def test_brackets_and_backslashes_are_escaped_and_read_back_whole():
    awkward = r"a (b) \ c \(d\) ) ( end"
    pdf = one_text(awkward)
    assert lines(pdf) == [awkward]
    assert rb"\(b\)" in pdf and rb"\\" in pdf
    assert squashed(plumber_text(pdf)) == awkward


def test_a_letter_that_winansi_has_is_kept():
    text = "café naïve €5 “quoted”"
    pdf = one_text(text)
    assert lines(pdf) == [text]
    assert b"\\351" in pdf, "e-acute is byte 0xE9, written as an octal escape"


def test_a_letter_it_does_not_have_is_a_visible_mark_and_nothing_crashes():
    pdf = one_text("Ok Ṅ and \U0001f600 and Ж end")
    assert lines(pdf) == ["Ok ? and ? and ? end"]
    assert "?" in plumber_text(pdf)


def test_control_characters_become_spaces_and_a_line_end_is_a_line_end():
    pdf = one_text("a\x00b\x07c\td\x7fe\r\nnext\rlast")
    assert lines(pdf) == ["a b c d e", "next", "last"]


def test_empty_text_is_one_empty_line_and_a_blank_line_is_kept():
    assert lines(one_text("")) == [""]
    assert lines(one_text("a\n\nb")) == ["a", "", "b"]


def test_runs_of_spaces_inside_a_line_are_kept():
    assert lines(one_text("a   b  c")) == ["a   b  c"]


def test_an_unknown_style_is_refused():
    with pytest.raises(ValueError):
        make([("shout", "x")])


def test_the_title_in_the_metadata_is_plain_ascii():
    pdf = make([("text", "x")], title="Report Ṅ (R-000001)")
    assert b"/Title (Report ? \\(R-000001\\))" in pdf


# --------------------------------------------------------------------------- word wrap
def test_words_wrap_at_the_line_width():
    # "00000" is 5 x 5.56 = 27.8 wide; with a space (2.78) k words need 30.58k - 2.78 <= 483, so
    # k = 15. 31 words make lines of 15, 15 and 1.
    words = ["00000"] * 31
    got = lines(one_text(" ".join(words)))
    assert [len(line.split()) for line in got] == [15, 15, 1]
    assert " ".join(got) == " ".join(words)


def test_a_word_wider_than_a_line_is_cut_into_pieces_that_fit():
    # One "W" is 9.44 wide, so 483 / 9.44 = 51.2 and at most 51 fit: 200 make 51, 51, 51, 47.
    got = lines(one_text("W" * 200))
    assert [len(line) for line in got] == [51, 51, 51, 47]
    assert "".join(got) == "W" * 200


def test_bold_letters_are_wider_than_plain_ones():
    # "i" is 222 plain (2.22 at 10 points) and 278 bold (2.78 at 10 points): at 12 points bold a
    # line holds fewer. 483 / (0.278 * 12) = 144.8, so 144 fit in a heading.
    got = lines(make([("heading", "i" * 300)]))
    assert [len(line) for line in got] == [144, 144, 12]


def test_the_wrap_keeps_every_word_in_order_for_ordinary_text():
    text = " ".join(f"word{n}" for n in range(400))
    got = lines(one_text(text))
    assert len(got) > 5
    assert " ".join(got) == text


# --------------------------------------------------------------------------- pages
def test_a_page_holds_52_lines_and_the_53rd_starts_the_next_page():
    # Baselines run 778, 764, ... and a line is drawn while its baseline is >= 64: (778 - 64) / 14
    # = 51, so baselines 0 to 51 steps down, which is 52 lines.
    text = "\n".join(f"line {n}" for n in range(53))
    got = pages(one_text(text))
    assert [len(p) for p in got] == [52, 1]
    assert got[1] == ["line 52"]


def test_every_page_is_listed_in_the_page_tree():
    text = "\n".join(f"line {n}" for n in range(120))
    pdf = one_text(text)
    assert len(pages(pdf)) == 3
    assert b"/Count 3" in pdf
    assert pdf.count(b"/Type /Page ") == 3


def test_a_heading_is_never_the_last_thing_on_a_page():
    # 50 lines leave the next baseline at 78. A heading adds 10 before and needs 17 more for the
    # line after it: 78 - 10 - 17 = 51 < 64, so the heading starts the next page.
    blocks = [("text", "\n".join(f"line {n}" for n in range(50))), ("heading", "H"),
              ("text", "after")]
    got = pages(make(blocks))
    assert len(got) == 2
    assert got[0][-1] == "line 49"
    assert got[1] == ["H", "after"]


def test_a_heading_with_room_stays_on_the_page():
    blocks = [("text", "\n".join(f"line {n}" for n in range(10))), ("heading", "H"),
              ("text", "after")]
    got = pages(make(blocks))
    assert len(got) == 1 and got[0][-2:] == ["H", "after"]


def test_the_footer_is_on_every_page_and_only_when_asked():
    text = "\n".join(f"line {n}" for n in range(120))
    with_foot = pages(one_text(text, footer="Practice data only"))
    assert len(with_foot) == 3 and all(p[-1] == "Practice data only" for p in with_foot)
    assert "Practice data only" not in lines(one_text(text))


def test_a_real_pdf_reader_opens_it_and_finds_the_pages_and_the_text():
    text = "\n".join(f"line {n}" for n in range(60))
    pdf = make([("title", "Report R-000001"), ("text", text)], footer="Practice data only")
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        assert len(document.pages) == 2
        assert (document.pages[0].width, document.pages[0].height) == (595, 842)
    read = plumber_text(pdf)
    assert "Report R-000001" in read and "line 59" in read
    assert read.count("Practice data only") == 2
