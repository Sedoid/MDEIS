"""The class findings the lesson checks read, and how one is made from a class result.

A finding is one learning area of one class result (stage, term), as the class tool returns it:
counts per bin (a bin may be hidden), the group size, the clause ids, and a finding id (`CF-` plus
8 hex). It holds counts only. A key that is not listed here is refused, so a per-learner list or a
name cannot ride along.

The shape follows the class tool's result (T-059). The bin that means "below the expected range" is
named once, here, and the lesson rules refuse a template with no level of that name.
"""

import re
from dataclasses import dataclass

FINDING_ID = re.compile(r"^CF-[0-9a-f]{8}$")
STATUSES = ("ok", "no_usable_evidence", "suppressed")
# The class tool's bin for "below the expected range". It is also the name of a template level.
GAP_BIN = "below_expected_range"
ALLOWED_KEYS = {
    "finding_id", "status", "stage", "area", "term", "template_version", "group_size", "bins",
    "clause_ids", "academic_year",
}
BIN_KEYS = {"bin", "shown", "count", "level_clause"}


class FindingError(ValueError):
    """A finding is not in the shape the class tool makes."""


@dataclass(frozen=True)
class Bin:
    name: str
    shown: bool
    count: int | None  # None exactly when the bin is hidden
    level_clause: str | None = None


@dataclass(frozen=True)
class Finding:
    finding_id: str
    status: str
    stage: str | None
    area: str | None
    term: int | None
    template_version: str
    group_size: int | None
    bins: tuple | None  # tuple of Bin, or None when there are no counts to show
    clause_ids: tuple
    academic_year: str | None = None

    def bin(self, name: str) -> Bin | None:
        for b in self.bins or ():
            if b.name == name:
                return b
        return None


def _whole(value, what: str, allow_none=False) -> int | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FindingError(f"{what} must be a whole number, not negative")
    return value


def _text(value, what: str, allow_none=False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value.strip():
        raise FindingError(f"{what} must be text")
    return value


def _bin(raw) -> Bin:
    if not isinstance(raw, dict) or set(raw) - BIN_KEYS:
        raise FindingError("a bin is {bin, shown, count} and may name its level clause")
    shown = raw.get("shown")
    if not isinstance(shown, bool):
        raise FindingError("a bin must say whether it is shown (true or false)")
    count = raw.get("count")
    if shown:
        count = _whole(count, "a shown count")
        if count is None:
            raise FindingError("a shown bin needs a count")
    elif count is not None:
        raise FindingError("a hidden bin has no count")
    return Bin(_text(raw.get("bin"), "a bin name"), shown, count,
               _text(raw.get("level_clause"), "a level clause", allow_none=True))


def finding_from_mapping(raw) -> Finding:
    if not isinstance(raw, dict):
        raise FindingError("a finding must be an object")
    unknown = set(raw) - ALLOWED_KEYS
    if unknown:
        raise FindingError("a finding holds counts only; it has a key that is not allowed")
    finding_id = raw.get("finding_id")
    if not isinstance(finding_id, str) or not FINDING_ID.match(finding_id):
        raise FindingError("a finding id is CF- and eight hex digits")
    status = raw.get("status")
    if status not in STATUSES:
        raise FindingError(f"status must be one of {', '.join(STATUSES)}")
    clause_ids = raw.get("clause_ids")
    if not isinstance(clause_ids, (list, tuple)) or not all(isinstance(c, str) for c in clause_ids):
        raise FindingError("clause_ids must be a list of clause ids")
    bins = raw.get("bins")
    if bins is not None:
        if not isinstance(bins, (list, tuple)):
            raise FindingError("bins must be a list")
        bins = tuple(_bin(b) for b in bins)
    term = raw.get("term")
    if term is not None:
        term = _whole(term, "term")
    return Finding(
        finding_id=finding_id,
        status=status,
        stage=_text(raw.get("stage"), "stage", allow_none=True),
        area=_text(raw.get("area"), "area", allow_none=True),
        term=term,
        template_version=_text(raw.get("template_version"), "template_version"),
        group_size=_whole(raw.get("group_size"), "group_size", allow_none=True),
        bins=bins,
        clause_ids=tuple(clause_ids),
        academic_year=_text(raw.get("academic_year"), "academic_year", allow_none=True),
    )


def findings_from_class_result(result: dict) -> list:
    """One finding per area of a class result, with the stage, term, group size and version of the
    result. A suppressed result (the whole group is under the minimum) is one finding with no area
    and no counts, so it can still be cited."""
    if not isinstance(result, dict):
        raise FindingError("a class result must be an object")
    top = {
        "stage": result.get("stage"),
        "term": result.get("term"),
        "template_version": result.get("template_version"),
        "academic_year": result.get("academic_year"),
    }
    top = {k: v for k, v in top.items() if v is not None}
    top_clauses = list(result.get("clause_ids") or [])
    if result.get("status") == "suppressed" or not result.get("areas"):
        return [
            finding_from_mapping(
                {
                    **top,
                    "finding_id": result.get("finding_id"),
                    "status": "suppressed" if result.get("status") == "suppressed"
                    else "no_usable_evidence",
                    "clause_ids": top_clauses,
                }
            )
        ]
    found = []
    for area in result["areas"]:
        clauses = list(dict.fromkeys([*(area.get("clause_ids") or []), *top_clauses]))
        found.append(
            finding_from_mapping(
                {
                    **top,
                    "finding_id": area.get("finding_id"),
                    "status": area.get("status"),
                    "area": area.get("area"),
                    "group_size": result.get("group_size"),
                    "bins": area.get("bins"),
                    "clause_ids": clauses,
                }
            )
        )
    return found
