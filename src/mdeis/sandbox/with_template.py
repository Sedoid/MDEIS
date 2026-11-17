"""The call path for a generated function that reads the school's values (T-070).

    result = with_template.run(source, code_sha256, records, content, declared_clause_ids,
                               output_kind="number")

The keys the function reads come from its own source (`static_check.params_read`). The values come
from `core.measure_params.params_for`, built from the ACTIVE template (`content` is its stored,
parsed content). The function cannot build, widen or change them: a key its version does not
declare, a clause that is missing or changed, and a value no teacher has confirmed are each refused
with a typed `ParamsRefusal`, and nothing is sent to the runner. The rest is `client.run`,
unchanged: the runner's protocol and limits are the same, and every number still crosses as
exact text.

This is a separate file from `client.py` on purpose: the client stays standard library only, and the
runner image carries nothing of the template.

The run-time tools for approved measures (T-072) reach the sandbox only through this file. It also
holds `force_problem`: whether a measure is still in force under the ACTIVE template. That check
reads the measure parameters section of the template, which hand-written tools and core code never
do (a test keeps them from it), so it lives here with the code that builds the values.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from ..core.measure_params import PARAMS_SECTION, ParamsRefusal, params_for
from ..core.template import CLAUSE_ID, OPTIONAL_SECTION_AREAS, SECTION_AREAS
from . import client
from .client import RunnerRefusal
from .config import load_rules
from .rules import Rules
from .static_check import params_read

__all__ = [
    "ParamsRefusal",
    "RunnerRefusal",
    "clause_rule",
    "force_problem",
    "recorded_types",
    "run",
    "values_for",
]


def values_for(
    source: str,
    content: Mapping,
    declared_clause_ids: Iterable[str],
    *,
    rules: Rules | None = None,
    recorded: Mapping[str, tuple[str, str]] | None = None,
    unconfirmed_ok: bool = False,
) -> dict[str, str]:
    """The values a function reads, by key, built from the template `content`: the keys come from
    its own source and the numbers from the rulebook. Raises ParamsRefusal. `run` builds them with
    this, so what is shown and what is sent are made by the same code."""
    rules = load_rules() if rules is None else rules
    return params_for(
        content,
        params_read(source, rules),
        declared_clause_ids,
        separator=rules.key_separator,
        recorded=recorded,
        unconfirmed_ok=unconfirmed_ok,
    )


def run(
    source: str,
    code_sha256: str,
    records: list,
    content: Mapping,
    declared_clause_ids: Iterable[str],
    *,
    output_kind: str,
    rules: Rules | None = None,
    socket_path: str | None = None,
    limits: dict | None = None,
    recorded: Mapping[str, tuple[str, str]] | None = None,
    runner: Callable | None = None,
    unconfirmed_ok: bool = False,
) -> dict:
    """Run one function with values built from the active template. The answer is the runner's
    `{"output_kind", "output"}` plus `params`: exactly what was sent, for the action log and the
    finding.

    `recorded` is the (value_type, unit) the registry noted for each `measure_params` clause when
    the version was built. `runner` is for tests: a function with the signature of `client.run`.

    `unconfirmed_ok` is for the build and the teacher's demo only: it lets a proposed value that no
    teacher has confirmed be used, so the teacher can see what confirming it would mean. A run for a
    user never sets it (a test keeps it out of the tools), so there an unconfirmed value is refused.
    """
    rules = load_rules() if rules is None else rules
    params = values_for(
        source,
        content,
        declared_clause_ids,
        rules=rules,
        recorded=recorded,
        unconfirmed_ok=unconfirmed_ok,
    )
    send = client.run if runner is None else runner
    result = send(
        source,
        code_sha256,
        records,
        params,
        output_kind=output_kind,
        rules=rules,
        socket_path=socket_path,
        limits=limits,
    )
    return {**result, "params": params}


# --- in force ----------------------------------------------------------------------------------
def clause_rule(content: Mapping, clause_id: str) -> Mapping | None:
    """The rule with this clause ID in a stored template, or None. Every section is searched."""
    for section in (*SECTION_AREAS, *OPTIONAL_SECTION_AREAS):
        for rule in content.get(section) or []:
            if isinstance(rule, Mapping) and rule.get("id") == clause_id:
                return rule
    return None


def force_problem(
    content: Mapping,
    declared_clause_ids: Iterable[str],
    recorded: Mapping[str, tuple[str, str]] | None = None,
) -> tuple[str, str] | None:
    """None when a measure is in force, else (kind, detail) for the first problem found.

    `content` is the ACTIVE template's stored content. Each declared clause must be there. A measure
    parameter (area MP) must also be confirmed by a teacher and, when `recorded` says what its type
    and unit were at build time, still have that type and unit. A changed value alone does not take
    a measure out of force: the value is the school's rule and is read at run time (owner's decision
    of 2026-11-17). Clauses are looked at in sorted order, so the first problem is the same each
    time. The kinds are the ones `params_for` refuses with, so the two agree.
    """
    area = OPTIONAL_SECTION_AREAS[PARAMS_SECTION]
    for clause in sorted(set(declared_clause_ids)):
        rule = clause_rule(content, clause)
        if rule is None:
            return "clause_missing", f"{clause} is not in the active template"
        match = CLAUSE_ID.match(clause)
        if not match or match.group(1) != area:
            continue
        was = (recorded or {}).get(clause)
        if was is not None and (rule.get("value_type"), rule.get("unit")) != tuple(was):
            return "clause_changed", f"{clause} has another type or unit now"
        if rule.get("confirmed_by") is None:
            return "placeholder_value", f"{clause} has not been confirmed by a teacher"
    return None


def recorded_types(*sources) -> dict[str, tuple[str, str]]:
    """{measure parameter clause ID: (value_type, unit)} from the templates or patches given.

    A source is a stored template (a mapping with a measure parameters list), or a patch in either
    of two shapes: a mapping with that list, or the list itself. Anything else is ignored. When two
    sources name the same clause, the first one wins."""
    found: dict[str, tuple[str, str]] = {}
    for source in sources:
        rules = source.get(PARAMS_SECTION) if isinstance(source, Mapping) else source
        if not isinstance(rules, list):
            continue
        for rule in rules:
            if isinstance(rule, Mapping) and isinstance(rule.get("id"), str):
                found.setdefault(rule["id"], (rule.get("value_type"), rule.get("unit")))
    return found
