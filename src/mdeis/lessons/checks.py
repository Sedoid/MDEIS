"""check_edits: the deterministic checks on a model's edit list, LC-1 to LC-10.

Every edit is checked against every rule and every failing rule is listed, so the repair prompt (and
the tutor) see the whole reason. A failing edit is dropped with its LC ids and a plain reason; the
rest of the plan goes on. This module only reports. It never sends anything back to a model.

  LC-1  the objective is a locked field: an edit that quotes it, or any line of the notes that holds
        it, is dropped
  LC-2  every edit cites at least one finding that a class tool returned in this run, and that
        finding has status ok; no resource ids in this version
  LC-3  the quoted span is verbatim, found once, inside one section, not in a table or code block,
        and does not overlap another edit (both are dropped)
  LC-4  by type: a reword must change the text; an addition keeps the quoted text and adds after it.
        At plan level, the text outside the edits is rebuilt and compared byte for byte
  LC-5  no learner name or number in the notes, the objective, the replacement or the suggestion
  LC-6  the replacement and the suggestion pass every wording clause of the rulebook
  LC-7  no digit in a suggestion; a number in a replacement must already be in the tutor's notes
  LC-8  an edit rests on a class gap: the below-range bin is shown, with a count at or above the
        rulebook's minimum cell size. Otherwise no evidence-based edit is made
  LC-9  an edit cites evidence for the selected stage, area and term only
  LC-10 size limits from the rulebook: notes length, edit count, edit length

Plan-level failures (a bug, or notes that name a learner) raise `PlanError`: nothing is shown.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import NamedTuple

from .apply import apply_edits
from .evidence import (
    EvidenceError,
    check_finding,
    no_gap_sentence,
    not_enough_evidence_sentence,
)
from .findings import GAP_BIN
from .guard import Roster, guard_notes, scan_text
from .parse import Lesson, find_all, sha256
from .rules import LessonRules

ADDITIONS = ("add_example", "add_check")
# Whether the model may be asked to try again after each failure. A bad shape or bad wording can be
# repaired; evidence, a locked objective, a name or a size limit cannot.
REPAIRABLE = {"LC-1": False, "LC-2": False, "LC-3": True, "LC-4": True, "LC-5": False,
              "LC-6": True, "LC-7": True, "LC-8": False, "LC-9": False, "LC-10": True}


class Selection(NamedTuple):
    stage: str
    area: str
    term: int


class PlanError(ValueError):
    """The whole call stops: the selection is not valid, the notes are over the limit or name a
    learner, or the plan-level rebuild found a difference. Nothing is shown."""


@dataclass(frozen=True)
class EditVerdict:
    edit_id: str
    kept: bool
    lc_ids: tuple
    reasons: tuple
    repairable: bool


@dataclass(frozen=True)
class Notice:
    clause_id: str
    where: str  # "notes": a listed word is in the tutor's own notes. No edit is refused for it.


@dataclass(frozen=True)
class Checked:
    verdicts: tuple
    kept: tuple
    revised: object  # apply.Revised, built from the kept edits
    usable_evidence: bool
    statement: str | None
    statement_kind: str | None  # "insufficient_evidence", "no_gap" or None
    notices: tuple
    objective_unchanged: bool
    outside_unchanged: bool

    @property
    def dropped(self) -> tuple:
        return tuple(v for v in self.verdicts if not v.kept)

    @property
    def passed(self) -> bool:
        return (
            self.usable_evidence
            and self.objective_unchanged
            and self.outside_unchanged
            and len(self.kept) >= 1
        )

    def stored_edits(self) -> dict:
        """What is stored: the kept edits in full, and each dropped edit as an id, its LC ids and a
        plain reason. Never the model's text of a dropped edit."""
        return {
            "kept": [e.as_mapping() for e in self.kept],
            "dropped": [
                {"id": v.edit_id, "lc": list(v.lc_ids), "reason": "; ".join(v.reasons)}
                for v in self.dropped
            ],
        }


# --------------------------------------------------------------------------- small readers
def _digit_runs(text: str) -> set:
    """The numbers written in `text`, digits of any script read as the same number."""
    runs, current = set(), []
    for char in text:
        if char.isdecimal():
            current.append(str(unicodedata.decimal(char)))
        elif current:
            runs.add("".join(current))
            current = []
    if current:
        runs.add("".join(current))
    return runs


def _has_number(text: str) -> bool:
    return any(c.isnumeric() for c in text)


def _squash(text: str) -> str:
    return " ".join(text.casefold().split())


def wording_hits(text: str, rules: LessonRules) -> list:
    """(clause id, kind, listed word or phrase) for every list entry the text holds. Words match as
    whole words, ignoring case. Phrases match after case and spaces are folded, at word edges."""
    hits, squashed = [], _squash(text)
    for clause in rules.wording:
        for word in clause.words:
            if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text, re.IGNORECASE):
                hits.append((clause.clause_id, "word", word))
        for phrase in clause.phrases:
            if re.search(rf"(?<!\w){re.escape(_squash(phrase))}(?!\w)", squashed):
                hits.append((clause.clause_id, "phrase", phrase))
    return hits


def _gap_usable(finding, rules: LessonRules) -> bool:
    """A gap the lesson may rest on: the finding is ok, agrees with the rulebook, and its
    below-range bin is shown with a count at or above the minimum cell size."""
    if finding.status != "ok":
        return False
    try:
        check_finding(finding, rules)
    except EvidenceError:
        return False
    gap = finding.bin(GAP_BIN)
    return bool(gap and gap.shown and gap.count is not None and gap.count >= rules.min_cell)


def _statement(findings, rules: LessonRules, selection: Selection) -> tuple:
    """(kind, sentence) for a selection with no usable gap."""
    here = [
        f for f in findings
        if f.template_version == rules.version
        and (f.stage, f.term) == (selection.stage, selection.term)
        and f.area in (selection.area, None)
    ]
    try:
        for f in here:
            gap = f.bin(GAP_BIN)
            if f.status == "ok" and gap and gap.shown and gap.count == 0:
                return "no_gap", no_gap_sentence(f, rules)
        if here:
            return "insufficient_evidence", not_enough_evidence_sentence(here[0], rules)
    except EvidenceError:
        pass
    return "insufficient_evidence", not_enough_evidence_sentence(None, rules)


# --------------------------------------------------------------------------- one edit
def _anchor_problems(edit, lesson: Lesson) -> tuple:
    """(problems, span). A span is returned only when the anchor is found exactly once."""
    anchor = edit.anchor_quote
    if not anchor.strip():
        return [("LC-3", "the quoted text is blank")], None
    starts = find_all(lesson.text, anchor)
    if not starts:
        if anchor in lesson.objective:
            return [("LC-1", "the quoted text is in the objective, which is locked")], None
        return [("LC-3", "the quoted text was not found in the notes")], None
    problems = []
    locked = any(
        s < lo_end and lo_start < s + len(anchor)
        for s in starts for lo_start, lo_end in lesson.locked
    )
    if locked:
        problems.append(("LC-1", "the quoted text is part of a line that holds the objective, "
                                 "which is locked"))
    if len(starts) > 1:
        problems.append(("LC-3", "the quoted text occurs more than once in the notes"))
        return problems, None
    start, end = starts[0], starts[0] + len(anchor)
    if lesson.section_of(start, end) is None:
        problems.append(("LC-3", "the quoted text runs across two sections"))
    if any(start < p_end and p_start < end for p_start, p_end, _ in lesson.protected):
        problems.append(("LC-3", "the quoted text is in a table or a code block, which this "
                                 "version does not edit"))
    return problems, (start, end)


def _shape_problems(edit) -> list:
    if edit.type in ADDITIONS:
        if not edit.replacement.startswith(edit.anchor_quote) or len(edit.replacement) <= len(
            edit.anchor_quote
        ):
            return [("LC-4", f"{edit.type} must keep the quoted text and add after it")]
    elif edit.replacement == edit.anchor_quote:
        return [("LC-4", "a reword must change the quoted text")]
    return []


def _added_text(edit) -> str:
    """What the model wrote. For an addition that keeps the tutor's text, the tutor's own words in
    the anchor are not scanned."""
    if edit.type in ADDITIONS and edit.replacement.startswith(edit.anchor_quote):
        return edit.replacement[len(edit.anchor_quote):]
    return edit.replacement


def _check_edit(edit, lesson, findings_by_id, rules, selection, roster, usable, allowed_numbers):
    problems, span = _anchor_problems(edit, lesson)

    # LC-2 and LC-9, LC-8
    if edit.resource_ids:
        problems.append(("LC-2", "resource ids are not used in this version"))
    if not edit.finding_ids:
        problems.append(("LC-2", "the edit cites no finding"))
    for finding_id in edit.finding_ids:
        found = findings_by_id.get(finding_id)
        if found is None:
            problems.append(("LC-2", f"{finding_id} is not a finding that a class tool returned "
                                     "in this run"))
            continue
        if found.status != "ok":
            problems.append(
                ("LC-2", f"{finding_id} is not usable evidence (status {found.status})")
            )
        if (found.stage, found.area, found.term) != tuple(selection):
            problems.append(("LC-9", f"{finding_id} is evidence for another stage, area or term "
                                     "than the one selected"))
        elif found.status == "ok" and not _gap_usable(found, rules):
            problems.append(("LC-8", f"{finding_id} shows no class gap at or above the minimum "
                                     "count"))
    if not usable:
        problems.append(("LC-8", "no class gap is shown for this selection, so no evidence-based "
                                 "edit is made"))

    problems += _shape_problems(edit)

    # LC-5 on what the model wrote
    for text in (edit.replacement, edit.suggestion):
        if scan_text(text, roster):
            problems.append(("LC-5", "the edit text names a learner or holds a learner, record "
                                     "or issue number"))
            break

    # LC-6 wording
    hits = [
        (clause, kind, entry)
        for text in (_added_text(edit), edit.suggestion)
        for clause, kind, entry in wording_hits(text, rules)
    ]
    if hits:
        listed = ", ".join(dict.fromkeys(f"{c} lists the {k} {e!r}" for c, k, e in hits))
        problems.append(("LC-6", listed))

    # LC-7 numbers
    if _has_number(edit.suggestion):
        problems.append(("LC-7", "the suggestion holds a digit; class numbers come only from the "
                                 "evidence sentence"))
    if _digit_runs(edit.replacement) - allowed_numbers:
        problems.append(("LC-7", "the replacement holds a number that is not in the tutor's notes"))

    # LC-10 length
    if len(edit.replacement) > rules.max_edit_chars or len(edit.suggestion) > rules.max_edit_chars:
        problems.append(("LC-10", f"the replacement or the suggestion is over the limit in "
                                  f"{rules.limits_clause}"))
    return problems, span


def _verdict(edit, problems) -> EditVerdict:
    """A problem is (LC id, reason), or (LC id, reason, repairable) when it differs from its LC."""
    rows = [(p[0], p[1], p[2] if len(p) > 2 else REPAIRABLE[p[0]]) for p in problems]
    order = sorted(dict.fromkeys(lc for lc, _, _ in rows), key=lambda lc: int(lc.split("-")[1]))
    reasons = tuple("; ".join(r for lc, r, _ in rows if lc == one) for one in order)
    return EditVerdict(
        edit_id=edit.id,
        kept=not rows,
        lc_ids=tuple(order),
        reasons=reasons,
        repairable=bool(rows) and all(ok for _, _, ok in rows),
    )


# --------------------------------------------------------------------------- the plan
def _verify(lesson: Lesson, kept, revised) -> None:
    """Rebuild the text outside the edits from the original and compare it with the revision, byte
    for byte. A difference means the applier is wrong: the whole call stops."""
    objective_hash = sha256(lesson.objective)
    if revised.objective != lesson.objective or revised.objective_sha256 != objective_hash:
        raise PlanError("the objective is not the same after the edits (plan-level check)")
    if lesson.text_sha256 != sha256(lesson.text):
        raise PlanError("the original notes changed while the edits were checked")
    spans = sorted(
        ((find_all(lesson.text, e.anchor_quote)[0], e) for e in kept), key=lambda p: p[0]
    )
    position = cursor = 0
    for start, edit in spans:
        outside = lesson.text[cursor:start]
        if not revised.text.startswith(outside, position):
            raise PlanError("text outside the edits changed (plan-level check)")
        position += len(outside)
        if not revised.text.startswith(edit.replacement, position):
            raise PlanError("an edit is missing from the revised text (plan-level check)")
        position += len(edit.replacement)
        cursor = start + len(edit.anchor_quote)
    if revised.text[position:] != lesson.text[cursor:]:
        raise PlanError("text outside the edits changed (plan-level check)")
    if revised.text_sha256 != sha256(revised.text):
        raise PlanError("the revised text does not match its hash (plan-level check)")


def check_edits(lesson: Lesson, edits, findings, rules: LessonRules, selection, roster) -> Checked:
    edits = list(edits)
    selection = Selection(*selection)
    problem = rules.selection_problem(selection)
    if problem:
        raise PlanError(f"the selection is not valid: {problem}")
    if len(lesson.text) > rules.max_notes_chars:
        raise PlanError(f"the notes are too long: over the limit in {rules.limits_clause}")
    for text in (lesson.text, lesson.objective):
        guarded = guard_notes(text, roster if roster is not None else Roster.empty())
        if not guarded.ok:
            raise PlanError(guarded.message)

    findings = list(findings)
    by_id = {f.finding_id: f for f in findings}
    here = [
        f for f in findings
        if f.template_version == rules.version and (f.stage, f.area, f.term) == tuple(selection)
    ]
    usable = any(_gap_usable(f, rules) for f in here)
    kind, statement = (None, None) if usable else _statement(findings, rules, selection)
    allowed_numbers = _digit_runs(lesson.text) | _digit_runs(lesson.objective)

    problems_of, spans = {}, {}
    for index, edit in enumerate(edits):
        if index >= rules.max_edits:
            problems_of[edit.id] = [
                ("LC-10", f"more edits than the limit in {rules.limits_clause}; this one is "
                          "dropped", False)
            ]
            continue
        problems_of[edit.id], span = _check_edit(
            edit, lesson, by_id, rules, selection, roster if roster is not None else Roster.empty(),
            usable, allowed_numbers,
        )
        if span is not None:
            spans[edit.id] = span

    # LC-3 overlap: only edits that passed every other check can overlap each other.
    clean = [e for e in edits if not problems_of[e.id] and e.id in spans]
    for i, a in enumerate(clean):
        for b in clean[i + 1:]:
            (a0, a1), (b0, b1) = spans[a.id], spans[b.id]
            if a0 < b1 and b0 < a1:
                problems_of[a.id] = [*problems_of[a.id], ("LC-3", f"overlaps edit {b.id}")]
                problems_of[b.id] = [*problems_of[b.id], ("LC-3", f"overlaps edit {a.id}")]
    verdicts = tuple(_verdict(e, problems_of[e.id]) for e in edits)
    kept = tuple(e for e, v in zip(edits, verdicts, strict=True) if v.kept)

    revised = apply_edits(lesson, kept)
    _verify(lesson, kept, revised)

    notices = tuple(
        Notice(clause, "notes")
        for clause in dict.fromkeys(c for c, _, _ in wording_hits(lesson.text, rules))
    )
    return Checked(
        verdicts=verdicts,
        kept=kept,
        revised=revised,
        usable_evidence=usable,
        statement=statement,
        statement_kind=kind,
        notices=notices,
        objective_unchanged=True,
        outside_unchanged=True,
    )
