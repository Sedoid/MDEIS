"""Reading what a model answered. Pure. An answer that cannot be read is a problem to send back, or,
for the reviewer, a hold: the review never passes a function by default."""

from __future__ import annotations

import json
import re

FENCE = re.compile(r"^```[A-Za-z]*\s*\n(.*)\n```\s*$", re.DOTALL)

REVIEW_UNREADABLE = (
    "The automated review did not answer in the required form, so the version is held for a person."
)
REVIEW_UNREACHED = "The automated review could not be reached, so the version is held for a person."
REVIEW_NO_REASON = "The automated review held it and gave no reason, so a person must read it."


def parse_object(text) -> dict | None:
    """The one JSON object in an answer, or None. One surrounding code fence is tolerated."""
    if not isinstance(text, str):
        return None
    body = text.strip()
    fenced = FENCE.match(body)
    if fenced:
        body = fenced.group(1).strip()
    try:
        value = json.loads(body)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def read_rules(text) -> tuple[list | None, list[str]]:
    """(the rules, problems). The server decides which rules are kept; this only reads the shape."""
    found = parse_object(text)
    if found is None:
        return None, ["the answer must be one JSON object and nothing else"]
    rules = found.get("rules")
    if not isinstance(rules, list):
        return None, ["the JSON object must have a list called rules"]
    return rules, []


def read_proposal(text) -> tuple[dict | None, list[str]]:
    found = parse_object(text)
    if found is None:
        return None, ["the answer must be one JSON object and nothing else"]
    return found, []


def read_review(text) -> tuple[str, list[str]]:
    """(result, reasons). Only `pass` or `hold` with a list of reasons is read as given. Anything
    else is a hold with a plain reason, so a review that did not follow the form never lets a
    function through."""
    found = parse_object(text)
    if found is None:
        return "hold", [REVIEW_UNREADABLE]
    result, reasons = found.get("result"), found.get("reasons")
    listed = isinstance(reasons, list) and all(isinstance(r, str) and r.strip() for r in reasons)
    if result not in ("pass", "hold") or not listed:
        return "hold", [REVIEW_UNREADABLE]
    if result == "hold" and not reasons:
        return "hold", [REVIEW_NO_REASON]
    return result, list(reasons)
