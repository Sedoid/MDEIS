"""render_report_pdf (T-056, E-44 and the no-added-text rules): the page of a released report.

A pure function, so there is no database and no network here. The expected page is written out by
hand, line by line, in the brief's order. The text is read back with `pdfread.lines` (an independent
reader) for exact checks, and with pdfplumber (a real reader) for the E-44 check.
"""

import ast
import inspect
import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pdfread import lines, pages, plumber_text, squashed

from mdeis.export import report_pdf
from mdeis.export.report_pdf import build_blocks, render_report_pdf

WHEN = datetime(2026, 11, 17, 9, 30, tzinfo=UTC)
HASH_1, HASH_2 = "a1" * 32, "b2" * 32
BODY = (
    "Reading was in the Form 1 range [A-000001].\n\n"
    "Writing has too few scores to say [A-000004]."
)
FINDINGS = [
    {
        "skill": "Reading", "kind": "subject", "status": "ok", "trend": "steady",
        "result_hash": HASH_1, "record_ids": ["A-000001", "A-000002", "A-000003"],
        "clause_ids": ["TR-1", "TR-2"],
        "window": [{"record_id": "A-000001", "year": "2025/2026", "term": 1, "score": "55.00"}],
        "rules": [{"id": "TR-10", "text": "A rule of the rulebook."}],
    },
    {
        "skill": "Writing", "kind": "subject", "status": "ok", "trend": "not_enough_evidence",
        "scores_available": 1, "result_hash": HASH_2, "record_ids": ["A-000004"],
        "clause_ids": ["TR-1"],
        "assumed": [{"what": "stage weight", "stage": "Form 2", "used": "1.00"}],
    },
]
REPORT = {"report_id": "R-000007", "learner_id": "L-0042", "body": BODY, "findings": FINDINGS}


def record(n, subject, year, term, document, page, row):
    return {"id": f"A-{n:06d}", "kind": "record", "subject": subject, "year": year, "term": term,
            "document_id": document, "page": page, "row": row}


SOURCES = [  # in a shuffled order on purpose: the page puts them in its own order
    {"id": "M-6.4", "kind": "section", "document_id": "D-000009", "title": "Reading the scores",
     "page": 3},
    {"id": "TR-10", "kind": "clause", "template_version": "v1", "text": "A rule of the rulebook."},
    record(4, "Writing", "2025/2026", 2, "D-000003", 4, 9),
    {"id": "TR-1", "kind": "clause", "template_version": "v1", "text": "Evidence rule."},
    record(2, "Reading", "2025/2026", 2, "D-000003", 2, 6),
    record(1, "Reading", "2025/2026", 1, "D-000003", 2, 5),
    {"id": "TR-2", "kind": "clause", "template_version": "v1", "text": "Move rule."},
    record(3, "Reading", "2025/2026", 3, "D-000004", 1, 5),
]
PIN = {"template_version": "v1", "methodology_document_id": "D-000009"}
EAST_AFRICA = timezone(timedelta(hours=3))
APPROVER = {"name": "Ada Teacher", "released_at": datetime(2026, 11, 16, 14, 5, tzinfo=EAST_AFRICA)}

EXPECTED = [
    "Report R-000007",
    "Learner number: L-0042",
    "Report",
    "Reading was in the Form 1 range [A-000001].",
    "",
    "Writing has too few scores to say [A-000004].",
    "Findings",
    "Finding 1",
    "Subject or area: Reading",
    f"Result hash: {HASH_1}",
    "Records: A-000001, A-000002, A-000003",
    "Clauses: TR-1, TR-2, TR-10",
    "Finding 2",
    "Subject or area: Writing",
    f"Result hash: {HASH_2}",
    "Records: A-000004",
    "Clauses: TR-1",
    "Sources",
    "A-000001 | Reading | 2025/2026 term 1 | D-000003 | page 2 | row 5",
    "A-000002 | Reading | 2025/2026 term 2 | D-000003 | page 2 | row 6",
    "A-000003 | Reading | 2025/2026 term 3 | D-000004 | page 1 | row 5",
    "A-000004 | Writing | 2025/2026 term 2 | D-000003 | page 4 | row 9",
    "TR-1 | template v1 | Evidence rule.",
    "TR-2 | template v1 | Move rule.",
    "TR-10 | template v1 | A rule of the rulebook.",
    "M-6.4 | D-000009 | page 3",
    "Pin",
    "Template version: v1",
    "Methodology document: D-000009",
    "Methods",
    "M-6.4: Reading the scores",
    "Limitations",
    "Finding 2: not enough evidence (1 scores available)",
    "Finding 2: assumed stage weight for Form 2, used 1.00",
    "Release",
    "Released by Ada Teacher on 2026-11-16 11:05 UTC",
]
BANNED = (
    "weak", "strong", "remedial", "gifted", "slow", "bright", "lazy", "talented", "struggling",
    "rank", "ranked", "ranking", "best", "worst", "top", "bottom", "pass", "fail", "failing",
    "at risk", "behind", "should", "recommend", "recommended", "advise", "pathway", "career",
    "label", "classified", "deserves", "needs help",
)


def render(report=REPORT, sources=SOURCES, pin=PIN, approver=APPROVER, when=WHEN, **kw):
    return render_report_pdf(report, sources, pin, approver, when, **kw)


def whole_words(text, word):
    return re.search(rf"\b{re.escape(word)}\b", text, re.IGNORECASE) is not None


# --------------------------------------------------------------------------- the page, in order
def test_the_page_is_exactly_these_lines_in_the_briefs_order():
    assert lines(render()) == EXPECTED


def test_the_function_takes_the_stored_report_the_sources_the_pin_the_approver_and_a_time():
    names = list(inspect.signature(render_report_pdf).parameters)
    assert names == ["report", "sources", "pin", "approver", "generated_at", "practice_data"]
    assert not {"name", "learner_name", "display_name", "user"} & set(names)


def test_with_nothing_stored_the_page_is_only_headings_and_fixed_lines_and_the_given_values():
    bare = {"report_id": "R-000001", "learner_id": "L-0001", "body": "Body [A-000001].",
            "findings": {}}
    got = lines(render(bare, [], {"template_version": "v1", "methodology_document_id": None},
                       {"name": "Bo", "released_at": "2026-11-16 08:00 UTC"}))
    assert got == [
        "Report R-000001", "Learner number: L-0001", "Report", "Body [A-000001].",
        "Findings", "No findings are stored with this report.",
        "Sources", "No sources are stored with this report.",
        "Pin", "Template version: v1", "Methodology document: none",
        "Methods", "No methodology section is cited.",
        "Limitations", "None recorded in the stored findings.",
        "Release", "Released by Bo on 2026-11-16 08:00 UTC",
    ]


def test_every_line_of_the_page_is_a_heading_a_fixed_line_or_a_stored_value():
    """Strip every stored value out of the page. What is left must be only the module's own fixed
    words. A stored value is anything handed in (the body, the findings, the sources, the pin, the
    approver)."""
    stored = [BODY, "Reading", "Writing", HASH_1, HASH_2, "L-0042", "R-000007", "Ada Teacher",
              "2026-11-16 11:05 UTC", "D-000003", "D-000004", "D-000009", "2025/2026",
              "Evidence rule.", "Move rule.", "A rule of the rulebook.", "Reading the scores",
              "stage weight", "Form 2", "1.00", "v1"]
    text = "\n".join(lines(render()))
    for value in sorted(stored, key=len, reverse=True):
        text = text.replace(value, " ")
    text = re.sub(r"\b(?:A|M)-[0-9][0-9A-Za-z.]*|\b[A-Z]{2}-[0-9]+", " ", text)  # IDs
    text = re.sub(r"[0-9]+", " ", text)  # page, row, term and count numbers
    left = set(re.findall(r"[A-Za-z]+", text))
    fixed = set()
    for name, value in vars(report_pdf).items():
        if name.isupper() and isinstance(value, str):
            fixed |= set(re.findall(r"[A-Za-z]+", re.sub(r"\{[^}]*\}", " ", value)))
    assert left <= fixed, sorted(left - fixed)


def test_no_word_the_exporter_writes_is_a_label_a_rank_or_advice():
    for name, value in vars(report_pdf).items():
        if name.isupper() and isinstance(value, str):
            for word in BANNED:
                assert not whole_words(value, word), (name, word)
    for line in lines(render()):
        for word in BANNED:
            assert not whole_words(line, word), (line, word)


def test_nothing_in_the_page_names_a_person_but_the_releaser():
    text = "\n".join(lines(render()))
    assert "Ada Teacher" in text and "Learner number: L-0042" in text
    assert "display_name" not in text and "Name" not in text


# --------------------------------------------------------------------------- E-44
def test_E44_a_real_reader_finds_the_body_every_id_the_approver_and_the_pin():
    read = squashed(plumber_text(render()))
    assert "Reading was in the Form 1 range [A-000001]." in read
    assert "Writing has too few scores to say [A-000004]." in read
    for record_id in ("A-000001", "A-000002", "A-000003", "A-000004"):
        assert record_id in read
    for clause_id in ("TR-1", "TR-2", "TR-10"):
        assert clause_id in read
    assert "Released by Ada Teacher on 2026-11-16 11:05 UTC" in read
    assert "Template version: v1" in read and "Methodology document: D-000009" in read
    assert "Learner number: L-0042" in read
    assert "D-000003 | page 2 | row 5" in read


def test_every_id_of_every_stored_finding_is_on_the_page():
    stored = set(re.findall(r"\b(?:A-[0-9]{6}|[A-Z]{2}-[0-9]+)\b", repr(FINDINGS)))
    shown = set(re.findall(r"\b(?:A-[0-9]{6}|[A-Z]{2}-[0-9]+)\b", "\n".join(lines(render()))))
    assert stored and stored <= shown, stored - shown


# --------------------------------------------------------------------------- same input, same bytes
def test_the_same_stored_report_and_time_give_identical_bytes():
    assert render() == render()
    assert render(practice_data=True) == render(practice_data=True)


def test_the_order_the_sources_arrive_in_does_not_change_the_bytes():
    assert render(sources=list(reversed(SOURCES))) == render()


def test_a_different_time_changes_only_the_time_in_the_file():
    later = render(when=WHEN + timedelta(hours=1))
    assert later != render()
    assert lines(later) == lines(render())


# --------------------------------------------------------------------------- the body, as stored
def test_the_body_is_shown_as_stored_with_its_blank_lines_and_its_marks_for_what_winansi_lacks():
    body = "First.\n\nSecond Ṅ and \U0001f600 here [A-000001]."
    got = lines(render({**REPORT, "body": body}))
    start = got.index("Report") + 1
    assert got[start: start + 3] == ["First.", "", "Second ? and ? here [A-000001]."]


def test_a_long_body_wraps_keeps_every_word_and_runs_to_a_second_page():
    words = [f"w{n}" for n in range(1500)]
    body = " ".join(words)
    pdf = render({**REPORT, "body": body})
    got = pages(pdf)
    assert len(got) >= 2
    on_page = [ln for p in got for ln in p]
    start = on_page.index("Report") + 1
    end = on_page.index("Findings")
    assert " ".join(on_page[start:end]) == body


def test_a_name_with_letters_winansi_lacks_does_not_crash_the_page():
    got = lines(render(approver={"name": "Ada Ṅ", "released_at": "2026-11-16"}))
    assert "Released by Ada ? on 2026-11-16" in got


# --------------------------------------------------------------------------- the footer
def test_the_footer_is_on_every_page_when_the_deployment_says_practice_data():
    body = "\n".join(f"line {n}" for n in range(150))
    on = pages(render({**REPORT, "body": body}, practice_data=True))
    assert len(on) >= 3 and all(p[-1] == "Practice data only" for p in on)


def test_there_is_no_footer_when_it_does_not():
    assert "Practice data only" not in lines(render())
    assert "Practice data only" not in lines(render(practice_data=False))


# --------------------------------------------------------------------------- the smaller parts
def test_ids_come_out_sorted_by_number_and_listed_once():
    found = report_pdf.ids_in({"a": ["TR-10", "TR-2", "TR-2"], "b": {"c": "A-000010"},
                               "d": "A-000002", "e": "M-6.4", "f": 5, "g": "not an id"})
    assert found == {"records": ["A-000002", "A-000010"], "clauses": ["TR-2", "TR-10"],
                     "sections": ["M-6.4"]}


def test_a_time_is_shown_in_utc_to_the_minute_and_a_text_is_shown_as_it_is():
    assert report_pdf.when(datetime(2026, 1, 2, 3, 4, 59, tzinfo=UTC)) == (
        "2026-01-02 03:04 UTC")
    assert report_pdf.when(datetime(2026, 1, 2, 3, 4)) == "2026-01-02 03:04 UTC"
    assert report_pdf.when("on a Monday") == "on a Monday"


def test_the_limits_come_from_the_stored_findings_and_nothing_else():
    findings = [
        {"status": "blocked", "skill": "Reading"},
        {"trend": "not_enough_evidence"},
        {"trend": "steady", "level": "55.00"},
    ]
    report = {**REPORT, "findings": findings}
    got = lines(render(report))
    start = got.index("Limitations") + 1
    assert got[start: got.index("Release")] == [
        "Finding 1: blocked, no numbers were worked out",
        "Finding 2: not enough evidence",
    ]


def test_measure_versions_are_in_the_pin_line_only_when_given():
    pin = {**PIN, "measure_versions": {"trend": "2", "gap": "1"}}
    assert "Measure versions: gap 1, trend 2" in lines(render(pin=pin))
    assert not [ln for ln in lines(render()) if ln.startswith("Measure versions")]


def test_a_finding_that_is_not_an_object_is_listed_without_ids():
    got = lines(render({**REPORT, "findings": ["just text"]}))
    assert got[got.index("Findings") + 1] == "Finding 1"


def test_blocks_are_a_title_then_headings_and_text():
    blocks = build_blocks(REPORT, SOURCES, PIN, APPROVER)
    assert blocks[0] == ("title", "Report R-000007")
    assert {style for style, _ in blocks} == {"title", "heading", "text"}
    assert [t for s, t in blocks if s == "heading"] == [
        "Report", "Findings", "Sources", "Pin", "Methods", "Limitations", "Release"]


# --------------------------------------------------------------------------- the function is pure
def test_the_module_imports_nothing_that_reaches_a_database_a_network_or_a_model():
    tree = ast.parse(Path(report_pdf.__file__).read_text("utf-8"))
    imported = {
        n.module.split(".")[0] for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module
    } | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert imported <= {"__future__", "re", "datetime"}, imported
    relative = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level}
    assert relative == {"pdf_writer"}


def test_the_function_does_not_change_what_it_was_given():
    import copy

    report, sources = copy.deepcopy(REPORT), copy.deepcopy(SOURCES)
    render(report, sources)
    assert report == REPORT and sources == SOURCES


@pytest.mark.parametrize("missing", ["report_id", "learner_id", "body"])
def test_a_report_without_its_core_fields_is_a_plain_error_not_a_blank_page(missing):
    broken = {k: v for k, v in REPORT.items() if k != missing}
    with pytest.raises(KeyError):
        render(broken)
