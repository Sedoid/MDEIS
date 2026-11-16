"""parse_lesson: sections, locked objective, protected spans (T-060, slice 1). Pure, no database."""

import hashlib

import pytest
from lessonkit import A_ASK, LESSON, OBJECTIVE, lesson

from mdeis.lessons.parse import LessonError, find_all, parse_lesson

HEADINGS = ["## Starter", "## Main activity", "## Plenary"]


def pieces(parsed):
    return [parsed.text[s.start : s.end] for s in parsed.sections]


def test_headings_start_sections_and_the_text_before_the_first_heading_is_section_zero():
    parsed = lesson()
    assert [s.kind for s in parsed.sections] == ["preamble", "heading", "heading", "heading"]
    assert [s.heading for s in parsed.sections] == [None, *HEADINGS]
    assert parsed.sections[0].index == 0
    assert pieces(parsed)[0] == "Objective: Learners explain the main idea of a short text.\n\n"
    assert pieces(parsed)[1].startswith("## Starter\nRead the short passage")


def test_the_sections_cover_the_whole_text_with_no_gap_and_no_overlap():
    parsed = lesson()
    assert parsed.sections[0].start == 0
    assert parsed.sections[-1].end == len(parsed.text)
    for before, after in zip(parsed.sections, parsed.sections[1:], strict=False):
        assert before.end == after.start
    assert "".join(pieces(parsed)) == LESSON


def test_a_text_that_starts_with_a_heading_has_no_preamble_section():
    parsed = parse_lesson("# One\nfirst\n\n# Two\nsecond\n", "the aim")
    assert [s.kind for s in parsed.sections] == ["heading", "heading"]
    assert [s.index for s in parsed.sections] == [0, 1]
    assert pieces(parsed) == ["# One\nfirst\n\n", "# Two\nsecond\n"]


def test_without_headings_a_section_is_a_block_between_blank_lines_and_keeps_its_blank_lines():
    text = "First block line one.\nFirst block line two.\n\n\nSecond block.\n \nThird block.\n"
    parsed = parse_lesson(text, "the aim")
    assert [s.kind for s in parsed.sections] == ["block", "block", "block"]
    assert pieces(parsed) == [
        "First block line one.\nFirst block line two.\n\n\n",
        "Second block.\n \n",
        "Third block.\n",
    ]


@pytest.mark.parametrize(
    "line",
    ["#hashtag", "####### seven hashes", " # indented", "Not a # heading", "#"],
)
def test_only_one_to_six_hashes_then_a_space_at_the_line_start_is_a_heading(line):
    parsed = parse_lesson(f"Intro\n{line}\nbody\n", "the aim")
    assert all(s.kind != "heading" for s in parsed.sections)


@pytest.mark.parametrize("hashes", ["#", "##", "###", "####", "#####", "######"])
def test_each_heading_level_from_one_to_six_starts_a_section(hashes):
    parsed = parse_lesson(f"Intro\n{hashes} Title\nbody\n", "the aim")
    assert [s.kind for s in parsed.sections] == ["preamble", "heading"]


def test_a_hash_line_inside_a_code_fence_is_not_a_heading():
    text = "Intro\n```\n# not a heading\n```\nafter\n\n## Real\nbody\n"
    parsed = parse_lesson(text, "the aim")
    assert [s.heading for s in parsed.sections] == [None, "## Real"]


def test_line_endings_and_unicode_are_left_exactly_as_submitted():
    text = "Aim line\r\n\r\n## Étape\r\nLisez le texte à voix haute.\r\n"
    parsed = parse_lesson(text, "the aim")
    assert parsed.text == text
    assert [s.kind for s in parsed.sections] == ["preamble", "heading"]
    assert "".join(pieces(parsed)) == text


def test_the_lines_that_hold_the_objective_are_locked():
    parsed = lesson()
    line = "Objective: Learners explain the main idea of a short text.\n"
    assert parsed.locked == ((0, len(line)),)


def test_a_repeat_of_the_objective_is_found_across_a_line_break_and_in_any_case():
    repeat = "Say it: learners EXPLAIN the main\nidea of a short text. Then go on.\n"
    text = f"Aim\n\n## A\n{repeat}\n## B\nplain\n"
    parsed = parse_lesson(text, OBJECTIVE)
    assert len(parsed.locked) == 1
    start, end = parsed.locked[0]
    assert text[start:end] == repeat


def test_every_repeat_of_the_objective_is_locked():
    text = f"{OBJECTIVE}\n\n## A\nOnce more: {OBJECTIVE}\n\n## B\nplain\n"
    assert len(parse_lesson(text, OBJECTIVE).locked) == 2


def test_an_objective_that_is_not_in_the_notes_locks_nothing():
    assert parse_lesson("## A\nplain notes\n", OBJECTIVE).locked == ()


def test_a_table_and_a_code_fence_are_protected_spans():
    text = "Intro\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\nafter\n```\ncode line\n```\nend\n"
    parsed = parse_lesson(text, "the aim")
    kinds = [(k, text[s:e]) for s, e, k in parsed.protected]
    assert kinds == [
        ("table", "| a | b |\n| - | - |\n| 1 | 2 |\n"),
        ("code", "```\ncode line\n```\n"),
    ]


def test_an_unclosed_fence_protects_the_rest_of_the_text():
    text = "Intro\n```\ncode\nmore code\n"
    parsed = parse_lesson(text, "the aim")
    assert [(k, text[s:e]) for s, e, k in parsed.protected] == [("code", "```\ncode\nmore code\n")]


def test_the_section_of_a_span_is_known_only_when_the_span_sits_inside_one_section():
    parsed = lesson()
    start = LESSON.index(A_ASK)
    assert parsed.section_of(start, start + len(A_ASK)) == 1
    across = LESSON.index("## Main activity")
    assert parsed.section_of(across - 5, across + 5) is None


def test_the_hashes_are_the_sha256_of_the_text_and_the_objective_as_given():
    parsed = lesson()
    assert parsed.text_sha256 == hashlib.sha256(LESSON.encode("utf-8")).hexdigest()
    assert parsed.objective_sha256 == hashlib.sha256(OBJECTIVE.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    ("text", "objective"), [("", "aim"), ("  \n", "aim"), ("notes", ""), ("notes", " \n")]
)
def test_empty_notes_or_an_empty_objective_is_refused_plainly(text, objective):
    with pytest.raises(LessonError):
        parse_lesson(text, objective)


def test_parse_does_not_change_or_strip_the_text():
    text = "  leading space\n\n## A\nbody   \n\n\n"
    assert parse_lesson(text, "aim").text == text


def test_find_all_counts_overlapping_matches():
    assert find_all("aaa", "aa") == [0, 1]
    assert find_all("abcabc", "abc") == [0, 3]
    assert find_all("abc", "x") == []
