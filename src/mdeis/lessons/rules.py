"""The lesson rules, read from a rulebook version's content.

Every limit, word list, level statement and minimum the lesson checks use comes from here, and here
it comes from the rulebook (the `templates.content` of one version). This module holds no school
value. It finds each rule by the field it carries, so a renumbered rulebook still works, and it
keeps the clause id of each rule so results can cite it.

A rulebook that does not carry the lesson rules (a `lessons` section with its three limits, a
`wording` clause with `banned_phrases`, a `min_cell_size`, a level for the gap bin) is refused
with a message that names what is missing. The lesson tools then refuse on that rulebook.
"""

from dataclasses import dataclass

from ..core.rules import RulebookProblem
from .findings import GAP_BIN

LIMIT_FIELDS = ("max_notes_chars", "max_edits", "max_edit_chars")


class LessonsUnavailable(RulebookProblem):
    """The active rulebook lacks a rule the lesson tools need. The message says which."""


@dataclass(frozen=True)
class Level:
    clause_id: str
    name: str
    statement: str


@dataclass(frozen=True)
class WordingList:
    clause_id: str
    words: tuple
    phrases: tuple


@dataclass(frozen=True)
class LessonRules:
    version: str
    sha256: str
    foundation_areas: tuple  # read for the form's area list; no v1 check uses it (no prerequisites)
    terms_per_year: int
    range_clause: dict  # (stage, area) -> clause id
    levels: tuple  # Level, in the template's order
    wording: tuple  # WordingList, one per wording clause that lists words or phrases
    max_notes_chars: int
    max_edits: int
    max_edit_chars: int
    limits_clause: str
    min_group: int
    group_clause: str
    group_text: str
    min_cell: int
    cell_clause: str

    def level_named(self, name: str) -> Level | None:
        return next((lv for lv in self.levels if lv.name == name), None)

    def level_with_clause(self, clause_id: str) -> Level | None:
        return next((lv for lv in self.levels if lv.clause_id == clause_id), None)

    def selection_problem(self, selection) -> str | None:
        """None when the stage, area and term are ones this rulebook knows, else a plain reason."""
        stage, area, term = selection
        stages = {s for s, _ in self.range_clause}
        areas = {a for _, a in self.range_clause}
        if stage not in stages:
            return f"{stage!r} is not a stage in {self.version}"
        if area not in areas or (stage, area) not in self.range_clause:
            return f"{area!r} is not a learning area for {stage} in {self.version}"
        whole = isinstance(term, int) and not isinstance(term, bool)
        if not whole or not 1 <= term <= self.terms_per_year:
            return f"the term must be a whole number from 1 to {self.terms_per_year}"
        return None


def _with(rules, field):
    """The first rule in a list that carries `field` (a value that is not None)."""
    return next((r for r in rules or [] if r.get(field) is not None), None)


def lesson_rules_from_content(content: dict, version: str, sha256: str) -> LessonRules:
    lessons = [r for r in content.get("lessons") or [] if isinstance(r, dict)]
    if not lessons:
        raise LessonsUnavailable(f"the rulebook {version} has no lessons section (LS), so the "
                                 "lesson tools are off for it")
    for field in LIMIT_FIELDS:
        if _with(lessons, field) is None:
            raise LessonsUnavailable(f"the rulebook {version} has no lessons rule with {field}")
    limits = {f: int(_with(lessons, f)[f]) for f in LIMIT_FIELDS}
    limits_rule = _with(lessons, LIMIT_FIELDS[0])

    wording = []
    for rule in content.get("wording") or []:
        words = tuple(rule.get("banned") or ())
        phrases = tuple(rule.get("banned_phrases") or ())
        if words or phrases:
            wording.append(WordingList(rule["id"], words, phrases))
    if not any(w.phrases for w in wording):
        raise LessonsUnavailable(f"the rulebook {version} has no wording rule with banned_phrases")

    group = _with(content.get("group_minimum"), "min_group_size")
    if group is None:
        raise LessonsUnavailable(f"the rulebook {version} has no group_minimum rule with "
                                 "min_group_size")
    cell = _with(content.get("group_minimum"), "min_cell_size")
    if cell is None:
        raise LessonsUnavailable(f"the rulebook {version} has no group_minimum rule with "
                                 "min_cell_size")
    levels = tuple(
        Level(r["id"], r["name"], r["statement"]) for r in content.get("levels") or []
    )
    if not any(lv.name == GAP_BIN for lv in levels):
        raise LessonsUnavailable(f"the rulebook {version} has no level named {GAP_BIN}")
    terms = _with(content.get("calculations"), "terms_per_year")
    if terms is None:
        raise LessonsUnavailable(f"the rulebook {version} has no calculations rule with "
                                 "terms_per_year")
    return LessonRules(
        version=version,
        sha256=sha256,
        foundation_areas=tuple(content.get("foundation_areas") or ()),
        terms_per_year=int(terms["terms_per_year"]),
        range_clause={
            (r["stage"], r["area"]): r["id"] for r in content.get("stage_expectations") or []
        },
        levels=levels,
        wording=tuple(wording),
        max_notes_chars=limits["max_notes_chars"],
        max_edits=limits["max_edits"],
        max_edit_chars=limits["max_edit_chars"],
        limits_clause=limits_rule["id"],
        min_group=int(group["min_group_size"]),
        group_clause=group["id"],
        group_text=group["text"],
        min_cell=int(cell["min_cell_size"]),
        cell_clause=cell["id"],
    )


def load_lesson_rules(conn, version=None) -> LessonRules:
    """The lesson rules of `version`, or of the active version when none is named."""
    if version is None:
        row = conn.execute(
            "SELECT version_name, sha256, content FROM templates WHERE active"
        ).fetchone()
        if row is None:
            raise RulebookProblem("no rulebook version is active: load one first")
    else:
        row = conn.execute(
            "SELECT version_name, sha256, content FROM templates WHERE version_name = %s",
            (version,),
        ).fetchone()
        if row is None:
            raise RulebookProblem(f"no rulebook version named {version} is loaded")
    name, digest, content = row
    return lesson_rules_from_content(content, name, digest)
