"""The values a generated measure reads, built from the ACTIVE template. Pure: it reads the
template content it is given and no file, no database, no clock.

A generated function never holds a school number. It reads `params["MP-1"]` (a `measure_params`
rule) or `params["TR-1.window_terms"]` (a number field of a hand-written rule). This module builds
that `params` object, as the sandbox protocol needs it: a map of text to text, every number exact.

    params_for(content, keys, declared_clause_ids, separator=".")  ->  {"MP-1": "10", ...}

A key is the clause ID, with the separator and a field name added for a hand-written rule. A
function can read only the keys its registry version declares: a key whose clause is not in
`declared_clause_ids` is refused. The values are exact text: a whole number "10", a fraction "3/5".
A list of names is one text, the names joined by a line break, in the template's order. No float
is ever made.

The wrapper refuses, with a typed reason, and never guesses a value:
  key_malformed       the key is not CLAUSE or CLAUSE<separator>field, as its kind needs
  key_not_declared    the key's clause is not one the version declares
  clause_missing      the active template has no such rule, or the rule has no such field
  clause_changed      the rule's type or unit differs from the one recorded at build time
  placeholder_value   the rule's `confirmed_by` is null: no teacher has confirmed the value
  value_not_readable  the field is not a number (a list of bands, a word, a switch)

The separator comes from the sandbox rules (`templates/sandbox-v1.yaml`), so the static check and
this module agree on one definition and this module holds no copy of it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from fractions import Fraction

from .template import CLAUSE_ID, NAMES_TYPE, OPTIONAL_SECTION_AREAS, SECTION_AREAS

PARAMS_SECTION = "measure_params"
NAMES_JOINER = "\n"  # names cross the sandbox as one text; a name never holds a line break
REFUSALS = (
    "key_malformed",
    "key_not_declared",
    "clause_missing",
    "clause_changed",
    "placeholder_value",
    "value_not_readable",
)


class ParamsRefusal(ValueError):
    """The values cannot be built. `kind` is one of REFUSALS; `detail` names the key, never a
    record."""

    def __init__(self, kind: str, detail: str):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def exact_text(value: Fraction) -> str:
    """A fraction in lowest terms as "n/d", or "n" when it is whole."""
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def params_for(
    content: Mapping,
    keys: Iterable[str],
    declared_clause_ids: Iterable[str],
    *,
    separator: str,
    recorded: Mapping[str, tuple[str, str]] | None = None,
    unconfirmed_ok: bool = False,
) -> dict[str, str]:
    """The exact text for each key, from the template `content` (the stored, parsed template).

    `recorded` maps a `measure_params` clause ID to the (value_type, unit) the registry noted when
    the version was built. When given, a rule whose type or unit differs is `clause_changed`.
    Raises ParamsRefusal. Keys are handled in sorted order, so the first refusal is the same each
    time.

    `unconfirmed_ok` is for the build only: it tests a proposed function against the values the
    methodology states, before any teacher has confirmed them. A run for a user never sets it, so a
    value with `confirmed_by` null is refused there (`placeholder_value`)."""
    declared = frozenset(declared_clause_ids)
    area = OPTIONAL_SECTION_AREAS[PARAMS_SECTION]
    built: dict[str, str] = {}
    for key in sorted(set(keys)):
        clause, has_field, field = key.partition(separator)
        match = CLAUSE_ID.match(clause)
        if not match:
            raise ParamsRefusal("key_malformed", f"{key!r} does not start with a clause ID")
        if match.group(1) == area:
            if has_field:
                raise ParamsRefusal("key_malformed", f"{key!r}: a measure parameter has no field")
        elif not has_field or not field:
            raise ParamsRefusal("key_malformed", f"{key!r} needs a field name after the clause ID")
        if clause not in declared:
            raise ParamsRefusal("key_not_declared", f"{key!r}: {clause} is not declared")
        if match.group(1) == area:
            built[key] = _parameter(content, clause, recorded, unconfirmed_ok)
        else:
            built[key] = _field(content, clause, field)
    return built


def _parameter(content: Mapping, clause: str, recorded, unconfirmed_ok: bool = False) -> str:
    rule = next((r for r in content.get(PARAMS_SECTION) or [] if _is(r, clause)), None)
    if rule is None:
        raise ParamsRefusal("clause_missing", f"{clause} is not in the active template")
    if recorded is not None and clause in recorded:
        if (rule.get("value_type"), rule.get("unit")) != tuple(recorded[clause]):
            raise ParamsRefusal("clause_changed", f"{clause} has another type or unit now")
    if rule.get("confirmed_by") is None and not unconfirmed_ok:
        raise ParamsRefusal("placeholder_value", f"{clause} has not been confirmed by a teacher")
    kind, value = rule.get("value_type"), rule.get("value")
    if kind == "integer" and isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if kind == "rational" and isinstance(value, str):
        try:
            return exact_text(Fraction(value))
        except (ValueError, ZeroDivisionError):
            pass
    if kind == NAMES_TYPE and isinstance(value, list) and all(isinstance(v, str) for v in value):
        return NAMES_JOINER.join(value)
    raise ParamsRefusal("value_not_readable", f"{clause} does not hold a value of its type")


def _field(content: Mapping, clause: str, field: str) -> str:
    for section in (*SECTION_AREAS, *OPTIONAL_SECTION_AREAS):
        if section == PARAMS_SECTION:
            continue
        for rule in content.get(section) or []:
            if _is(rule, clause):
                return _number(rule, clause, field)
    raise ParamsRefusal("clause_missing", f"{clause} is not in the active template")


def _number(rule: Mapping, clause: str, field: str) -> str:
    if field not in rule or rule[field] is None:
        raise ParamsRefusal("clause_missing", f"{clause} has no field {field}")
    value = rule[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParamsRefusal("value_not_readable", f"{clause}.{field} is not a single number")
    try:
        return exact_text(Fraction(str(value)))
    except (ValueError, ZeroDivisionError):
        detail = f"{clause}.{field} is not an exact number"
        raise ParamsRefusal("value_not_readable", detail) from None


def _is(rule, clause: str) -> bool:
    return isinstance(rule, Mapping) and rule.get("id") == clause
