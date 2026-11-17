"""The quote checks of the build (T-071). Pure code, no database, no model.

A rule the model extracted is kept only when its quote is literally in the section text, and the
value is in the quote. A worked-example test is independent only when its quote is literally in the
document and every number it relies on is in that quote. Expected answers are decided by hand."""

import pytest

from mdeis.measure_build import quotes

SECTION = (
    "**Definition.** Change = the last score minus the first score. The pattern is moved up when\n"
    "the change is 10 points or more. It is moved down when the change is -10 points or less."
)
TABLE = "| Window | The last 3 term scores in one subject | Chooses the scores |"


def test_a_quote_that_is_in_the_text_word_for_word_is_found():
    found = quotes.quote_found("Change = the last score minus the first score.", {"6.4": SECTION})
    assert found == "6.4"


def test_line_breaks_and_spacing_do_not_matter_but_the_words_do():
    broken = "The pattern is moved up when  the change is 10 points or more."
    assert quotes.quote_found(broken, {"6.4": SECTION}) == "6.4"
    assert quotes.quote_found("The pattern is moved up when the change is 12 points or more.",
                              {"6.4": SECTION}) is None  # fmt: skip
    assert quotes.quote_found("the pattern is MOVED up", {"6.4": SECTION}) is None, "capitals count"


def test_the_first_section_that_holds_it_is_named_in_the_order_given():
    texts = {"3.2": TABLE, "6.4": SECTION}
    assert quotes.quote_found("Window | The last 3 term scores", texts) == "3.2"
    assert quotes.quote_found("moved down when the change", texts) == "6.4"


def test_an_empty_or_blank_quote_is_never_found():
    assert quotes.quote_found("", {"6.4": SECTION}) is None
    assert quotes.quote_found("   \n ", {"6.4": SECTION}) is None


@pytest.mark.parametrize(
    "text,number,expected",
    [
        ("change is 10 points", "10", True),
        ("change is 100 points", "10", False),     # inside 100
        ("a score of 1.5", "1", False),            # inside 1.5
        ("3/5 of the scores", "3", False),         # inside a fraction
        ("scores 52, 58 and 63", "58", True),
        ("Change -8, so not moved", "-8", True),
        ("Change -8, so not moved", "8", True),    # the 8 of -8 is still the number 8
        ("section 6.4", "4", False),
    ],
)
def test_a_number_is_in_a_text_only_as_a_separate_number(text, number, expected):
    assert quotes.number_in(text, number) is expected


def test_a_value_must_be_said_in_its_quote_by_its_type():
    quote = "Checklist levels: Starting, Practising, Confident and Independent in order"
    assert quotes.value_in_quote("integer", 10, "is 10 points or more")
    assert not quotes.value_in_quote("integer", 12, "is 10 points or more")
    assert quotes.value_in_quote("rational", "3/5", "at least 3/5 of the checks")
    assert quotes.value_in_quote("rational", "0.6", "a share of 0.6")
    assert not quotes.value_in_quote("rational", "0.6", "a share of 0.7")
    assert quotes.value_in_quote("names", ["Starting", "Confident"], quote)
    assert not quotes.value_in_quote("names", ["Starting", "Expert"], quote)
    assert not quotes.value_in_quote("integer", True, "is 1 point")  # a switch is not a number
    assert not quotes.value_in_quote("unknown_type", 10, "10")


def test_the_numbers_in_a_text_are_listed_in_order():
    assert quotes.numbers_in("were 52, 58 and 63. The change was 63 minus 52, which is 11") == [
        "52", "58", "63", "63", "52", "11",
    ]  # fmt: skip
    assert quotes.numbers_in("Change -8, so not moved") == ["-8"]
    assert quotes.numbers_in("a half is 1/2 and 0.6 too") == ["1/2", "0.6"]
    assert quotes.numbers_in("no numbers here") == []


def test_an_independent_test_needs_its_records_and_its_numbers_in_the_quote():
    quote = "The scores, oldest first, were 52, 58 and 63. The change was 63 minus 52, which is 11"
    expected = {"change": "11", "moved_up": "1"}
    assert quotes.worked_example_problem(quote, [52, 58, 63], expected, {0, 1}) is None
    # a record number the quote does not have
    bad = quotes.worked_example_problem(quote, [52, 58, 64], {"change": "11"}, {0, 1})
    assert "64" in bad
    # an expected number the quote does not have (12 is not structural)
    bad = quotes.worked_example_problem(quote, [52, 58, 63], {"change": "12"}, {0, 1})
    assert "12" in bad
    # only structural numbers expected: nothing in the quote anchors the test
    bad = quotes.worked_example_problem(quote, [52, 58, 63], {"moved_up": "1"}, {0, 1})
    assert "anchor" in bad
