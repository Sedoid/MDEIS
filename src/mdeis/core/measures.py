"""The pure part of running an approved generated measure (T-072). No database, no file, no clock.

A generated measure is a function that was written, checked, tested and confirmed by a teacher, and
then stored in the registry. These are the fixed rules around it at run time. The function itself
only computes: records and the school's values in, exact numbers out.

Whether a measure is still in force under the active template (its clauses present, confirmed, and
of the same type and unit) is `force_problem` in `mdeis.sandbox.with_template`, beside the call path
that builds the values. It is not here: no hand-written core code reads the measure parameters
section, and a test keeps it so.

  runner_records       the records a measure is given: one learner, exact text, the rulebook's
                       scale, oldest first, blank terms left out
  check_number_output  what a measure's answer must look like before any of it is returned
  number_view          how a number is shown: two places, and the exact form beside it
  banned_hits          banned words in a text a model will read (a name, a description)

Nothing here holds a school value. The scale and the banned words come from the rulebook that is
passed in.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from fractions import Fraction

from .numbers import exact_text, two_places
from .trend import on_scale, when

# The kinds a measure may return that the tools can show today. The first version returns numbers
# only; the other kinds in the sandbox rules (label, series, table) wait for a renderer each.
# This is what the tools can draw and print, not a school rule.
SUPPORTED_KINDS: tuple[str, ...] = ("number",)

FIELD_NAME = re.compile(r"[a-z][a-z0-9_]*")  # lower case words joined by underscores
EXACT_NUMBER = re.compile(r"-?[0-9]+(?:/[0-9]+|\.[0-9]+)?")  # "5", "-5", "61/4", "0.60"
WORDS = re.compile(r"[^A-Za-z0-9]+")
RECORDS_KEY = "record_ids"  # the one optional field that is a list of text, not a number


class OutputRefusal(ValueError):
    """A measure's answer that cannot be returned. `code` is short and fixed; the message may say
    more, but a tool shows only the code, so nothing the function returned is repeated."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


# --- what goes in ------------------------------------------------------------------------------
def runner_records(records: list, rules, subject: str | None = None) -> list[dict]:
    """The records a measure is given: only scored terms (a blank term is not recorded, and is never
    a zero), on the rulebook's scale as exact text, oldest first. With `subject`, that subject only.

    The order is the year, the term, the subject and the record number, so the same records always
    reach the function in the same order. No record of another learner is ever in `records`: the
    caller reads one learner."""
    chosen = [
        r
        for r in records
        if r["score"] is not None and (subject is None or r["subject"] == subject)
    ]
    chosen.sort(key=lambda r: (*when(r), r["subject"], r["record_id"]))
    return [
        {
            "record_id": r["record_id"],
            "year": r["year"],
            "term": r["term"],
            "stage": r["stage"],
            "subject": r["subject"],
            "score": exact_text(on_scale(r, rules)),
        }
        for r in chosen
    ]


# --- what comes out ----------------------------------------------------------------------------
def _exact(text, what: str) -> Fraction:
    if not isinstance(text, str) or not EXACT_NUMBER.fullmatch(text):
        raise OutputRefusal("not_exact", f"{what} is not an exact number written as text")
    try:
        return Fraction(text)
    except (ValueError, ZeroDivisionError):
        raise OutputRefusal("not_exact", f"{what} is not an exact number") from None


def check_number_output(
    output, sent_ids: Iterable[str], schema: Mapping | None = None, banned: Iterable[str] = ()
) -> dict:
    """What a number measure answered, checked. Returns

        {"numbers": {name: Fraction}, "record_ids": [..] or None}

    The answer is an object of exact numbers written as text, under field names that are lower case
    words joined by underscores and carry no banned word. At least one number is needed. A yes-or-no
    is 1 or 0: a true or false is not a number and is refused. When the version's `output_schema`
    lists `fields`, no other field is allowed. The one field that is not a number is `record_ids`,
    which may name the records the function used, each one among those it was sent; left out, the
    caller says it used all of them.

    Raises OutputRefusal with a short code. Nothing is rounded, repaired or guessed.
    """
    if not isinstance(output, dict):
        raise OutputRefusal("not_an_object", "the answer is not an object")
    sent = set(sent_ids)
    barred = [w.lower() for w in banned]
    for name in sorted(output):
        if not isinstance(name, str) or not FIELD_NAME.fullmatch(name):
            raise OutputRefusal("field_name", "a field name is not lower case words")
        hit = [w for w in WORDS.split(name.lower()) if w in barred]
        if hit:
            raise OutputRefusal("banned_word", f"a field name holds the banned word {hit[0]}")
    allowed = (schema or {}).get("fields")
    if isinstance(allowed, list):
        extra = sorted(set(output) - set(allowed) - {RECORDS_KEY})
        if extra:
            raise OutputRefusal("unknown_field", f"fields the version does not declare: {extra}")
    numbers = {name: _exact(output[name], name) for name in sorted(output) if name != RECORDS_KEY}
    if not numbers:
        raise OutputRefusal("no_numbers", "the answer holds no number")
    return {"numbers": numbers, "record_ids": _used(output, sent)}


def _used(output: dict, sent: set) -> list[str] | None:
    """The records the function says it used: text only, each one among those it was sent.

    None when the function names none, which the caller reads as all the records it was given.
    """
    if RECORDS_KEY not in output:
        return None
    named = output[RECORDS_KEY]
    if not isinstance(named, list) or not all(isinstance(r, str) for r in named):
        raise OutputRefusal("bad_record_ids", "record_ids is not a list of text")
    unknown = sorted(set(named) - sent)
    if unknown:
        raise OutputRefusal("unknown_record", "the answer names a record it was not given")
    return sorted(set(named))


def number_view(value: Fraction) -> dict:
    """A number as it is shown: to two places, as the other tools show a score, and exact."""
    return {"value": two_places(value), "exact": exact_text(value)}


# --- wording -----------------------------------------------------------------------------------
def banned_hits(text: str, banned: Iterable[str]) -> list[str]:
    """The banned words in a text, in the order of the list, each once, lower case. A word is a run
    of letters and digits, so `weak_flag` holds `weak` and `weakness` does not."""
    present = {w for w in WORDS.split(text.lower()) if w}
    found: list[str] = []
    for word in banned:
        low = word.lower()
        if low in present and low not in found:
            found.append(low)
    return found
