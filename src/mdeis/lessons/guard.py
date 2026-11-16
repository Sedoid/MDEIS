"""The notes guard: notes that name a learner are refused. They are never cleaned.

Cleaning would change the tutor's text without saying so, and could leave "Mr X's group" behind. So
a refusal says only that the notes name a learner, with the kind (`name` or `id`) and the position
of each hit. It never holds the matched text or any roster name, so it is safe to log and to show.

What it looks for:
  * a learner, record, issue or report number (the id forms the database makes);
  * every word of every learner's display name on the roster, in any order, any case, accents
    ignored, as whole words (a one-letter part is an initial and is skipped);
  * each school reference on the roster, ignoring case.
Honest limit: the roster holds only names that were read from a report card. The guard cannot find a
nickname, a misspelling, or a child of another school. A word that is also a given name ("Grace",
"Joy") is refused too, because the safe error here is to refuse.
"""

import re
import unicodedata
from dataclasses import dataclass, field

# The id forms mdeis_id() makes: learner L- (4 digits or more), record A-, issue I-, report R-
# (6 digits or more). A schema detail, as in the database's own id checks.
ID_FORM = re.compile(r"\b(?:L-[0-9]{4,}|A-[0-9]{6,}|I-[0-9]{6,}|R-[0-9]{6,})\b")
WORD = re.compile(r"[^\W\d_]+")
MIN_NAME_PART = 2  # a part of a name shorter than this is an initial, not a name


@dataclass(frozen=True)
class Roster:
    """The school's learner names and school references, in memory only. Not printed."""

    names: tuple = field(default=(), repr=False)
    refs: tuple = field(default=(), repr=False)

    @classmethod
    def empty(cls) -> "Roster":
        return cls((), ())

    def name_parts(self) -> frozenset:
        parts = set()
        for name in self.names:
            for word in WORD.findall(name or ""):
                folded = _fold(word)
                if len(folded) >= MIN_NAME_PART:
                    parts.add(folded)
        return frozenset(parts)


@dataclass(frozen=True)
class GuardFinding:
    kind: str  # "name" or "id"
    start: int
    end: int


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    text: str | None  # the notes, byte for byte, when ok; None when refused
    reason: str | None  # None, "empty", "too_long" or "learner_name_or_id"
    findings: tuple
    length: int

    @property
    def message(self) -> str:
        if self.reason == "empty":
            return "the notes are empty"
        if self.reason == "too_long":
            return f"the notes are {self.length} characters, which is over the limit"
        if self.reason == "learner_name_or_id":
            return (
                f"the notes name a learner or hold a learner, record or issue number "
                f"({len(self.findings)} places); they are refused, not cleaned"
            )
        return "the notes passed"


def _fold(word: str) -> str:
    decomposed = unicodedata.normalize("NFKD", word)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.casefold()


def scan_text(text: str, roster: Roster | None) -> tuple:
    """Every name or id in `text`, by position and kind, sorted. No matched text is returned."""
    found = [GuardFinding("id", m.start(), m.end()) for m in ID_FORM.finditer(text)]
    if roster is not None:
        parts = roster.name_parts()
        for m in WORD.finditer(text):
            if _fold(m.group()) in parts:
                found.append(GuardFinding("name", m.start(), m.end()))
        for ref in roster.refs:
            if ref:
                for m in re.finditer(re.escape(ref), text, re.IGNORECASE):
                    found.append(GuardFinding("id", m.start(), m.end()))
    return tuple(sorted(found, key=lambda f: (f.start, f.end, f.kind)))


def guard_notes(text, roster: Roster | None, *, max_chars: int | None = None) -> GuardResult:
    if not isinstance(text, str) or not text.strip():
        return GuardResult(False, None, "empty", (), 0 if not isinstance(text, str) else len(text))
    if max_chars is not None and len(text) > max_chars:
        return GuardResult(False, None, "too_long", (), len(text))
    findings = scan_text(text, roster)
    if findings:
        return GuardResult(False, None, "learner_name_or_id", findings, len(text))
    return GuardResult(True, text, None, (), len(text))
