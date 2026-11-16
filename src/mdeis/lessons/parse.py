"""Read a tutor's lesson notes: sections, the locked objective, and the spans no edit may touch.

Offsets are on the string exactly as submitted. Nothing is normalised: line endings, spaces and
Unicode stay as they came, so a later comparison of the text outside an edit is byte for byte.

Sections (the rule, stated once):
  * if the text has a heading line (one to six `#`, then a space or tab, at the start of a line,
    outside a code fence), a section starts at each heading line and runs to the next one. Text
    before the first heading is a section of its own (kind `preamble`, index 0);
  * if it has none, a section is a block of lines between blank lines, and keeps the blank lines
    that follow it, so the sections always cover the whole text with no gap.

Spans no edit may touch:
  * locked: every line that holds the objective (compared with spaces collapsed and case ignored);
  * protected: Markdown table lines (a line that starts with `|`) and code fences (the fence lines
    and what lies between them). Editing inside a table cell or a code block is not supported.
"""

import hashlib
import re
from dataclasses import dataclass

HEADING = re.compile(r"#{1,6}[ \t]")
FENCES = ("```", "~~~")


class LessonError(ValueError):
    """The notes or the objective cannot be read (empty)."""


@dataclass(frozen=True)
class Section:
    index: int
    start: int
    end: int
    heading: str | None
    kind: str  # "preamble", "heading" or "block"


@dataclass(frozen=True)
class Lesson:
    text: str
    objective: str
    sections: tuple
    locked: tuple  # ((start, end), ...) whole lines that hold the objective
    protected: tuple  # ((start, end, kind), ...) kind is "table" or "code"
    text_sha256: str
    objective_sha256: str

    def section_of(self, start: int, end: int) -> int | None:
        """The index of the one section that holds the span, or None when it is not inside one."""
        for section in self.sections:
            if section.start <= start and end <= section.end:
                return section.index
        return None


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def find_all(text: str, sub: str) -> list:
    """Every start of `sub` in `text`, counting matches that overlap each other."""
    found, at = [], text.find(sub)
    while at != -1:
        found.append(at)
        at = text.find(sub, at + 1)
    return found


def _lines(text: str) -> list:
    """(start, end, content) for each line. `end` includes the newline; `content` does not."""
    parts = text.split("\n")
    out, start = [], 0
    for i, content in enumerate(parts):
        last = i == len(parts) - 1
        if last and content == "":
            break
        end = start + len(content) + (0 if last else 1)
        out.append((start, end, content))
        start = end
    return out


def _fence_state(lines: list) -> tuple:
    """(set of line indexes inside or on a fence, list of (start, end) fence spans)."""
    inside, spans, opened = set(), [], None
    for i, (start, end, content) in enumerate(lines):
        if content.lstrip().startswith(FENCES):
            inside.add(i)
            if opened is None:
                opened = start
            else:
                spans.append((opened, end))
                opened = None
        elif opened is not None:
            inside.add(i)
    if opened is not None:
        spans.append((opened, lines[-1][1]))
    return inside, spans


def _tables(lines: list, inside: set) -> list:
    spans, run = [], None
    for i, (start, end, content) in enumerate(lines):
        row = i not in inside and content.lstrip().startswith("|")
        if row:
            run = (run[0], end) if run else (start, end)
        elif run:
            spans.append(run)
            run = None
    if run:
        spans.append(run)
    return spans


def _sections(text: str, lines: list, inside: set) -> tuple:
    heads = [
        (start, content.rstrip("\r"))
        for i, (start, _, content) in enumerate(lines)
        if i not in inside and HEADING.match(content)
    ]
    if heads:
        cuts = [(start, "heading", title) for start, title in heads]
        if heads[0][0] > 0:
            cuts.insert(0, (0, "preamble", None))
    else:
        cuts, in_blank = [(0, "block", None)], False
        for start, _, content in lines:
            blank = content.strip() == ""
            if blank:
                in_blank = True
            elif in_blank:
                cuts.append((start, "block", None))
                in_blank = False
    sections = []
    for n, (start, kind, title) in enumerate(cuts):
        end = cuts[n + 1][0] if n + 1 < len(cuts) else len(text)
        sections.append(Section(n, start, end, title, kind))
    return tuple(sections)


def _locked(text: str, objective: str, lines: list) -> tuple:
    pattern = re.compile(r"\s+".join(re.escape(w) for w in objective.split()), re.IGNORECASE)
    spans = []
    for match in pattern.finditer(text):
        first = next(s for s, e, _ in lines if e > match.start())
        last = next(e for s, e, _ in lines if e >= match.end() and s < match.end())
        if spans and first < spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], last))
        else:
            spans.append((first, last))
    return tuple(spans)


def parse_lesson(text: str, objective: str) -> Lesson:
    if not isinstance(text, str) or not text.strip():
        raise LessonError("the lesson notes are empty")
    if not isinstance(objective, str) or not objective.strip():
        raise LessonError("the objective is empty")
    lines = _lines(text)
    inside, fences = _fence_state(lines)
    protected = sorted(
        [(s, e, "code") for s, e in fences] + [(s, e, "table") for s, e in _tables(lines, inside)]
    )
    return Lesson(
        text=text,
        objective=objective,
        sections=_sections(text, lines, inside),
        locked=_locked(text, objective, lines),
        protected=tuple(protected),
        text_sha256=sha256(text),
        objective_sha256=sha256(objective),
    )
