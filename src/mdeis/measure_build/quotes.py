"""The quote checks of the build. Pure: no model, no database, no file.

A rule the model extracted is kept only when its quote is literally in the section text. A test
that claims to come from a worked example is independent of the model only when its quote is
literally in the document and the numbers it relies on are in that quote. The model's word is
never taken for either.

"Literally" means the same words in the same order and the same capitals. Line breaks and runs of
spaces are the one thing ignored (a PDF and a Markdown table break lines in different places).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping
from fractions import Fraction

# A number inside a text: an optional minus, whole digits, and a fraction or decimal part. Not a
# part of a longer word or of a longer number.
NUMBER = re.compile(r"(?<![\w.,/])-?\d+(?:/\d+|\.\d+)?(?!\w)")


def collapse(text: str) -> str:
    """The text with every run of white space made one space, and no space at the ends."""
    return " ".join(text.split())


def quote_found(quote, texts: Mapping[str, str]) -> str | None:
    """The id of the first section of `texts` (in the order given) that holds `quote` word for
    word, or None. An empty or blank quote is never found."""
    if not isinstance(quote, str) or not quote.strip():
        return None
    wanted = collapse(quote)
    for section_id, text in texts.items():
        if wanted in collapse(text):
            return section_id
    return None


def number_in(text: str, number: str) -> bool:
    """The number appears in the text as a separate number: not inside 100, 1.5 or 3/5."""
    pattern = r"(?<![0-9.,/])" + re.escape(number) + r"(?![0-9]|[.,/][0-9])"
    return re.search(pattern, text) is not None


def numbers_in(text: str) -> list[str]:
    """Every number in the text, in order, as written."""
    return [m.group(0) for m in NUMBER.finditer(text)]


def value_in_quote(value_type, value, quote: str) -> bool:
    """The value is said in the quote: a whole number or an exact number as a separate number, and
    every name of a list of names."""
    if value_type == "integer":
        return (
            isinstance(value, int) and not isinstance(value, bool) and number_in(quote, str(value))
        )
    if value_type == "rational":
        return isinstance(value, str) and number_in(quote, value)
    if value_type == "names":
        return isinstance(value, list) and bool(value) and all(
            isinstance(v, str) and v in quote for v in value
        )
    return False


def _exact(number) -> Fraction | None:
    try:
        return Fraction(str(number))
    except (ValueError, ZeroDivisionError):
        return None


def worked_example_problem(
    quote: str, record_numbers: Iterable, expected: Mapping, structural: Collection[int]
) -> str | None:
    """Why a worked-example test is not independent of the model, or None if it is.

    Every number in the records must be in the quote. Every expected number that is not one of the
    `structural` whole numbers (the 0 and 1 of a yes-or-no) must be in the quote too, and at least
    one such number must be there to anchor the test. `expected` maps an output key to exact text.
    """
    for number in record_numbers:
        if not number_in(quote, str(number)):
            return f"the record number {number} is not in the quote"
    anchored = 0
    for key in sorted(expected):
        value = _exact(expected[key])
        if value is None:
            return f"the expected value of {key} is not an exact number"
        if value.denominator == 1 and int(value) in structural:
            continue
        shown = str(value.numerator)
        if value.denominator != 1:
            shown = f"{value.numerator}/{value.denominator}"
        if not number_in(quote, shown):
            return f"the expected number {shown} ({key}) is not in the quote"
        anchored += 1
    if not anchored:
        return "no expected number is in the quote to anchor the test"
    return None
