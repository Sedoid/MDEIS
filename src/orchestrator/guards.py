"""Checks that run in code before the model or a tool is reached. The model cannot argue with them.

* `refusal_for`: a request to rank or compare learners, or to suggest a pathway or a career, is
  refused with a reason and the model is not called.
  The patterns are data in `prompts/refusals.json`, not code. It is a first filter: the system
  prompt and the citation check are the others.
* `release_blocked`: `release_report` runs in a later turn than the draft, after the approver has
  read it and asked for it. A report drafted in this run cannot be released in this run, and the
  latest message from the approver must ask for a release.
* `scrub`: the approver token is removed from anything bound for the model.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PROMPTS = Path(__file__).parent / "prompts"
REPORT_ID = re.compile(r"R-\d{6,}")
RELEASE_WORDS = re.compile(
    r"\b(release|approve|approved|publish|send it|sign off)\b", re.IGNORECASE
)
REMOVED = "[removed]"


def _refusals() -> list:
    return json.loads((PROMPTS / "refusals.json").read_text(encoding="utf-8"))


def refusal_for(text: str) -> str | None:
    """The plain-words reason this request is refused, or None when it is not."""
    for item in _refusals():
        if any(re.search(p, text, re.IGNORECASE) for p in item["patterns"]):
            return item["reason"]
    return None


def release_blocked(arguments: dict, last_user_text: str, drafted_in_run: set) -> str | None:
    """Why a `release_report` call must not run now, or None when it may be sent to the server."""
    report_id = str(arguments.get("report_id", ""))
    if report_id in drafted_in_run:
        return (
            "this draft was written in the same turn. The approver reads it first and asks for "
            "the release in a later message"
        )
    asks = RELEASE_WORDS.search(last_user_text) or report_id in REPORT_ID.findall(last_user_text)
    if not asks:
        return "the approver has not asked for this release in their latest message"
    return None


def scrub(text: str, secret: str | None) -> str:
    """`text` with the secret taken out."""
    if secret and isinstance(text, str):
        return text.replace(secret, REMOVED)
    return text
