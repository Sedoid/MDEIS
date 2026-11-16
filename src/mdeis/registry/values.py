"""The numbers a school's template holds, by clause ID, for the static check's duplicate rule.

Read from the stored template (`templates.content`). Pure: no database, no file. Each number is
returned as an exact fraction in text, so 0.6 and 3/5 are the same value. Only the structured fields
of a rule are read; the rule's own words (`text`) are not.
"""

from __future__ import annotations

from fractions import Fraction

from ..core.template import SECTION_AREAS

NOT_VALUES = ("id", "text", "method_ref")  # the fields of a rule that are never a value


def template_values(content: dict) -> dict[str, list[str]]:
    """{clause ID: [every number in that rule, as exact text]}. A rule with no number is left
    out."""
    held: dict[str, list[str]] = {}
    for section in SECTION_AREAS:
        for rule in content.get(section) or []:
            if not isinstance(rule, dict) or not isinstance(rule.get("id"), str):
                continue
            found: list[str] = []
            _collect({k: v for k, v in rule.items() if k not in NOT_VALUES}, found)
            if found:
                held[rule["id"]] = found
    return held


def _collect(node, found: list[str]) -> None:
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        try:
            found.append(str(Fraction(str(node))))
        except (ValueError, ZeroDivisionError):
            pass
    elif isinstance(node, dict):
        for value in node.values():
            _collect(value, found)
    elif isinstance(node, list):
        for value in node:
            _collect(value, found)
