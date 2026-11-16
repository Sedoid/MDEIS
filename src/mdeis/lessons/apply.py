"""Apply an edit list to the tutor's notes. Deterministic: the same lesson and edits always give the
same text and the same diff, whatever order the edits are given in.

Each edit names a verbatim span of the notes (`anchor_quote`). It must be found exactly once. The
span is replaced and every other character is copied across untouched. An anchor that is missing,
repeated, or that overlaps another edit is an error here; `checks.py` finds those cases first and
drops the edit, so this function only ever sees edits that passed.
"""

import difflib
from dataclasses import dataclass

from .parse import Lesson, find_all, sha256


class ApplyError(ValueError):
    """An edit cannot be applied: its anchor is not found, is not unique, or overlaps another."""


@dataclass(frozen=True)
class Change:
    edit_id: str
    type: str
    start: int  # where the anchor sits in the original
    end: int
    before: str
    after: str
    new_start: int  # where the replacement sits in the revised text
    new_end: int


@dataclass(frozen=True)
class DiffPart:
    op: str  # equal, replace, delete or insert (difflib's opcodes)
    old: tuple  # lines of the original, newlines kept
    new: tuple  # lines of the revision, newlines kept


@dataclass(frozen=True)
class Revised:
    text: str
    objective: str
    objective_sha256: str
    text_sha256: str
    changes: tuple
    diff: tuple


def split_lines(text: str) -> list:
    """Lines with their newline kept; only `\\n` ends a line, so the lines join back to the text."""
    parts = text.split("\n")
    lines = [p + "\n" for p in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def diff_lines(old: str, new: str) -> tuple:
    a, b = split_lines(old), split_lines(new)
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return tuple(
        DiffPart(op, tuple(a[i1:i2]), tuple(b[j1:j2]))
        for op, i1, i2, j1, j2 in matcher.get_opcodes()
    )


def locate(lesson: Lesson, edit) -> tuple:
    """(start, end) of the edit's anchor, which must occur exactly once."""
    found = find_all(lesson.text, edit.anchor_quote)
    if not found:
        raise ApplyError(f"the anchor of {edit.id} was not found in the notes")
    if len(found) > 1:
        raise ApplyError(f"the anchor of {edit.id} occurs more than once in the notes")
    return found[0], found[0] + len(edit.anchor_quote)


def apply_edits(lesson: Lesson, edits) -> Revised:
    placed = sorted(((*locate(lesson, e), e) for e in edits), key=lambda p: (p[0], p[1], p[2].id))
    out, changes, cursor, shift = [], [], 0, 0
    for start, end, edit in placed:
        if start < cursor:
            raise ApplyError(f"{edit.id} overlaps another edit")
        out.append(lesson.text[cursor:start])
        out.append(edit.replacement)
        new_start = start + shift
        changes.append(
            Change(
                edit_id=edit.id,
                type=edit.type,
                start=start,
                end=end,
                before=lesson.text[start:end],
                after=edit.replacement,
                new_start=new_start,
                new_end=new_start + len(edit.replacement),
            )
        )
        shift += len(edit.replacement) - (end - start)
        cursor = end
    out.append(lesson.text[cursor:])
    text = "".join(out)
    return Revised(
        text=text,
        objective=lesson.objective,
        objective_sha256=lesson.objective_sha256,
        text_sha256=sha256(text),
        changes=tuple(changes),
        diff=diff_lines(lesson.text, text),
    )
