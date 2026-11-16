"""The edit contract: the only thing a model may hand back, and a plain-code validator for it.

A model returns JSON: `{"edits": [{id, type, anchor_quote, replacement, finding_ids, resource_ids,
suggestion}]}`. It never returns the revised text. Code finds each `anchor_quote` in the tutor's
notes, applies the replacement, and leaves every other character alone.

No JSON-schema library is approved, so `parse_edits` is hand-written. `EDIT_SCHEMA` is the same
contract as a dict (for the prompt and for documentation), and a test checks that the two agree. The
limits that belong to the rulebook (how many edits, how long) are not here: they are LC-10 and come
from the template.
"""

import json
import re
from dataclasses import dataclass

# The edit types of this version. Resequencing and adding a prerequisite need a map of topic
# prerequisites that does not exist yet, so they are not offered.
EDIT_TYPES = ("reword", "add_example", "add_check")
ID_PATTERN = r"^e[0-9]{1,3}$"
FINDING_PATTERN = r"^CF-[0-9a-f]{8}$"

EDIT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["edits"],
    "properties": {
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id", "type", "anchor_quote", "replacement", "finding_ids", "resource_ids",
                    "suggestion",
                ],
                "properties": {
                    "id": {"type": "string", "pattern": ID_PATTERN},
                    "type": {"enum": list(EDIT_TYPES)},
                    "anchor_quote": {"type": "string", "minLength": 1},
                    "replacement": {"type": "string"},
                    "finding_ids": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"type": "string", "pattern": FINDING_PATTERN},
                    },
                    # No resource library in this version, so the list must be empty.
                    "resource_ids": {"type": "array", "maxItems": 0},
                    "suggestion": {"type": "string", "minLength": 1},
                },
            },
        }
    },
}
EDIT_KEYS = tuple(EDIT_SCHEMA["properties"]["edits"]["items"]["properties"])


@dataclass(frozen=True)
class Edit:
    id: str
    type: str
    anchor_quote: str
    replacement: str
    finding_ids: tuple
    resource_ids: tuple
    suggestion: str

    def as_mapping(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "anchor_quote": self.anchor_quote,
            "replacement": self.replacement,
            "finding_ids": list(self.finding_ids),
            "resource_ids": list(self.resource_ids),
            "suggestion": self.suggestion,
        }


@dataclass(frozen=True)
class ContractResult:
    edits: tuple
    error: str | None


def _invalid(reason: str) -> ContractResult:
    return ContractResult((), reason)


def _edit(raw, position: int):
    """(Edit, None) or (None, reason)."""
    where = f"edit {position}"
    if not isinstance(raw, dict):
        return None, f"{where} is not an object"
    missing = [k for k in EDIT_KEYS if k not in raw]
    if missing:
        return None, f"{where} lacks {', '.join(missing)}"
    extra = sorted(set(raw) - set(EDIT_KEYS))
    if extra:
        return None, f"{where} has a key that is not allowed: {', '.join(extra)}"
    if not isinstance(raw["id"], str) or not re.fullmatch(ID_PATTERN, raw["id"]):
        return None, f"{where}: id must be e and a number, like e1"
    if raw["type"] not in EDIT_TYPES:
        return None, f"{where}: type must be one of {', '.join(EDIT_TYPES)}"
    for key in ("anchor_quote", "suggestion"):
        if not isinstance(raw[key], str) or not raw[key]:
            return None, f"{where}: {key} must be non-empty text"
    if not isinstance(raw["replacement"], str):
        return None, f"{where}: replacement must be text"
    ids = raw["finding_ids"]
    if not isinstance(ids, list) or not ids:
        return None, f"{where}: finding_ids must be a non-empty list"
    if not all(isinstance(i, str) and re.fullmatch(FINDING_PATTERN, i) for i in ids):
        return None, f"{where}: finding_ids must be CF- and eight lower-case hex digits"
    if len(set(ids)) != len(ids):
        return None, f"{where}: finding_ids lists the same id twice"
    if not isinstance(raw["resource_ids"], list) or raw["resource_ids"]:
        return None, f"{where}: resource_ids must be an empty list in this version"
    return (
        Edit(
            id=raw["id"],
            type=raw["type"],
            anchor_quote=raw["anchor_quote"],
            replacement=raw["replacement"],
            finding_ids=tuple(ids),
            resource_ids=(),
            suggestion=raw["suggestion"],
        ),
        None,
    )


def parse_edits(raw) -> ContractResult:
    """Validate a model's output (JSON text, or an object already parsed). Anything that is not
    valid JSON or does not fit the contract is one invalid result: no edits, and a reason."""
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return _invalid("the output is not valid JSON")
    if not isinstance(raw, dict):
        return _invalid("the output must be a JSON object")
    if set(raw) - {"edits"}:
        return _invalid("the output has a key that is not allowed; only edits is")
    if "edits" not in raw:
        return _invalid("the output lacks edits")
    if not isinstance(raw["edits"], list):
        return _invalid("edits must be a list")
    edits, seen = [], set()
    for position, item in enumerate(raw["edits"], start=1):
        made, reason = _edit(item, position)
        if reason:
            return _invalid(reason)
        if made.id in seen:
            return _invalid(f"two edits have the id {made.id}")
        seen.add(made.id)
        edits.append(made)
    return ContractResult(tuple(edits), None)
