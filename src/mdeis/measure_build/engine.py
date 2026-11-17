"""The deterministic part of the measure build. Pure: it is given data and returns data. No model,
no database, no file, no clock. The steps that touch the database wrap these functions
(`api.py`); the graph in the orchestrator calls those steps and never these.

    plan            which sections of a methodology are built, which are skipped and why
    settle_rules    which rules the model extracted are kept (quote in the text), and where each
                    value lives (one home per setting: read the template, or propose a clause)
    check_function  the static check, the tests in the sandbox, the sensitivity check

Every list, word, pattern and limit comes from `templates/measure-build-v1.yaml` (`config.py`), the
sandbox rules and the school's template. The same input gives the same output.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from pydantic import ValidationError

from ..core.measure_params import NAMES_JOINER, ParamsRefusal, exact_text, params_for
from ..core.template import (
    CLAUSE_ID,
    MEASURE_UNITS,
    OPTIONAL_SECTION_AREAS,
    SECTION_AREAS,
    MeasureParamRule,
    TemplateError,
    parse_template,
)
from ..ingest import methodology, methodology_gate
from ..registry.values import template_values
from ..sandbox import static_check
from ..sandbox.client import UNAVAILABLE, RunnerRefusal
from ..sandbox.config import load_rules
from ..sandbox.sensitivity import check_sensitivity
from . import quotes
from .config import BuildRules
from .quotes import collapse

PARAMS_AREA = OPTIONAL_SECTION_AREAS["measure_params"]
AREAS = {**SECTION_AREAS, **OPTIONAL_SECTION_AREAS}


@functools.cache
def key_separator() -> str:
    """Between a clause ID and a field name in a key. The sandbox rules hold it, so the static
    check, the params channel and the build agree on one definition."""
    return load_rules().key_separator


@dataclass(frozen=True)
class BuildInputs:
    """What a build is given about one methodology document and the school's active template."""

    document_id: str  # the registry's document id (D-000123)
    document_ref: str  # the document's own id from its title block, else `document_id`
    document_version: str | None  # the version the document states, or None
    sections: tuple  # the document's sections (methodology.Section), in order
    issues: tuple  # the stored diff against the methodology before it (dicts: kind, ref_id)
    first_methodology: bool  # no methodology was in force before this one
    template_version: str  # the active template's version name
    content: dict  # the active template's stored content


def document_info(sections, rules: BuildRules) -> tuple[str | None, str | None]:
    """(the document's own id, its version), read from the first section that states each."""
    found = []
    for pattern in (rules.id_pattern, rules.version_pattern):
        regex = re.compile(pattern, re.IGNORECASE)
        hit = next((m for s in sections if (m := regex.search(s.text or ""))), None)
        found.append(hit.group(1) if hit else None)
    return found[0], found[1]


def make_inputs(
    *, document_id, sections, issues, first_methodology, template_version, content, rules
) -> BuildInputs:
    ref, version = document_info(sections, rules)
    return BuildInputs(
        document_id=document_id,
        document_ref=ref or document_id,
        document_version=version,
        sections=tuple(sections),
        issues=tuple(issues),
        first_methodology=bool(first_methodology),
        template_version=template_version,
        content=content,
    )


# --- which sections --------------------------------------------------------------------------
def references(section_id: str, sections, rules: BuildRules) -> list[str]:
    """The sections that the text of `section_id` points at ("listed in section 3.2"), in document
    order, leaving out itself and any section the document does not have."""
    by_id = {s.id: s for s in sections if s.id}
    if section_id not in by_id:
        return []
    words = "|".join(re.escape(w) for w in sorted(rules.reference_words, key=len, reverse=True))
    number = r"\d+(?:\.\d+)*(?:\.[A-Z])?"
    pointer = re.compile(
        rf"(?<!\w)(?:{words})\s+{number}(?:\s*(?:,|and|or)\s*{number})*", re.IGNORECASE
    )
    named = set()
    for hit in pointer.finditer(by_id[section_id].text):
        named.update(re.findall(number, hit.group(0)))
    return [s.id for s in sections if s.id in named and s.id != section_id]


def _covering(content: dict) -> dict[str, list[str]]:
    """{method_ref: [ids of the hand-written clauses with it]}. A measure_params clause is left
    out: it is not a fixed tool."""
    found: dict[str, list[str]] = {}
    for section in AREAS:
        if section == "measure_params":
            continue
        for rule in content.get(section) or []:
            if isinstance(rule, dict) and rule.get("method_ref") and rule.get("id"):
                found.setdefault(str(rule["method_ref"]), []).append(rule["id"])
    return found


def _area(clause_id: str) -> str | None:
    match = CLAUSE_ID.match(clause_id)
    return match.group(1) if match else None


def plan(inputs: BuildInputs, rules: BuildRules, build_anyway=(), sandbox=None) -> dict:
    """The sections to build, the ones skipped, and the ones that are not measures.

    Sections come from the stored diff: added and changed ones are candidates, removed ones are
    listed. A first methodology has every numbered section added. Only sections the clarity gate
    treats as measures are built. A measure section is skipped when a clause of the active template
    has the same `method_ref` AND the template's methodology version is the document's version;
    `build_anyway` names skipped sections a person wants built all the same.

    The answer also carries what the graph needs to write its prompts and bound its loops, so the
    orchestrator reads no file: `build` (the repair bound, the known settings, the units, the record
    fields and, when `sandbox` is given, the sandbox rules a function must keep).
    """
    sections = [s for s in inputs.sections if s.id]
    by_id = {s.id: s for s in sections}
    changes: dict[str, str] = {}
    removed: list[str] = []
    if inputs.first_methodology:
        changes = {s.id: methodology.ADDED.removeprefix("section_") for s in sections}
    else:
        for issue in inputs.issues:
            kind, ref = issue.get("kind"), issue.get("ref_id")
            if kind in (methodology.ADDED, methodology.CHANGED) and ref in by_id:
                changes[ref] = kind.removeprefix("section_")
            elif kind == methodology.REMOVED and ref not in removed:
                removed.append(ref)
    measures = set(methodology_gate.measure_ids(list(inputs.sections)))
    covering = _covering(inputs.content)
    same_version = (
        inputs.document_version is not None
        and inputs.content.get("methodology_version") == inputs.document_version
    )
    wanted = set(build_anyway or ())
    build, skipped, not_measures, anyway = [], [], [], []
    for s in sections:  # document order
        if s.id not in changes:
            continue
        if s.id not in measures:
            not_measures.append(s.id)
            continue
        clauses = covering.get(s.id, []) if same_version else []
        if clauses and s.id not in wanted:
            skipped.append(_skipped(s, changes[s.id], clauses, inputs, rules))
            continue
        if clauses:
            anyway.append(s.id)
        build.append(_to_build(s, changes[s.id], sections, rules))
    return {
        "status": "ok",
        "document_id": inputs.document_id,
        "document_ref": inputs.document_ref,
        "document_version": inputs.document_version,
        "template_version": inputs.template_version,
        "template_methodology_version": inputs.content.get("methodology_version"),
        "first_methodology": inputs.first_methodology,
        "sections": build,
        "skipped": skipped,
        "not_measures": not_measures,
        "removed": removed,
        "build_anyway": anyway,
        "build": _build_view(rules, sandbox),
    }


def _build_view(rules: BuildRules, sandbox) -> dict:
    return {
        "repair_rounds": rules.repair_rounds,
        "known_settings": [h.setting for h in rules.homes],
        "units": list(MEASURE_UNITS),
        "record_fields": list(rules.record_fields),
        "sandbox": None if sandbox is None else _sandbox_view(sandbox),
    }


def _sandbox_view(sandbox) -> dict:
    return {
        "entry_function": sandbox.entry_function,
        "entry_parameters": list(sandbox.entry_parameters),
        "output_kind_constant": sandbox.output_kind_constant,
        "values_parameter": sandbox.params_name,
        "allowed_modules": {m: list(v) for m, v in sorted(sandbox.modules.items())},
        "allowed_builtins": sorted(sandbox.builtins),
        "allowed_literals": sorted(sandbox.literals),
    }


def _skipped(section, change, clauses, inputs, rules) -> dict:
    area = _area(clauses[0])
    return {
        "section_id": section.id,
        "title": section.title,
        "change": change,
        "clause_ids": list(clauses),
        "tool": rules.fixed_tools.get(area) if area else None,
        "reason": (
            f"template clause {', '.join(clauses)} has method_ref {section.id} and the template "
            f"is for methodology version {inputs.document_version}"
        ),
        "build_anyway": False,
    }


def _to_build(section, change, sections, rules) -> dict:
    refs = references(section.id, sections, rules)
    by_id = {s.id: s for s in sections}
    return {
        "section_id": section.id,
        "title": section.title,
        "change": change,
        "sha256": section.sha256,
        "text": section.text,
        "references": refs,
        "reference_text": {r: by_id[r].text for r in refs},
    }


def section_texts(inputs: BuildInputs, section_id: str, rules: BuildRules) -> dict[str, str]:
    """{section id: text} for the section and the sections it points at, the section first. A
    rule's quote must be literally in one of these."""
    by_id = {s.id: s for s in inputs.sections if s.id}
    if section_id not in by_id:
        return {}
    wanted = [section_id, *references(section_id, inputs.sections, rules)]
    return {i: by_id[i].text for i in wanted}


def document_texts(inputs: BuildInputs) -> dict[str, str]:
    """{section id: text} for every numbered section: where a worked example may be quoted from."""
    return {s.id: s.text for s in inputs.sections if s.id}


def _rule_value(content: dict, section: str, field: str) -> tuple[str, Any] | None:
    """(clause id, value) of the first rule of `section` that holds `field`, or None."""
    for rule in content.get(section) or []:
        if isinstance(rule, dict) and rule.get(field) is not None and rule.get("id"):
            return rule["id"], rule[field]
    return None


# --- settling the rules the model extracted ----------------------------------------------------
VALUE_TYPES = ("integer", "rational", "names")


def _fits(value_type, value) -> bool:
    if value_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if value_type == "rational":
        return isinstance(value, str)
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _exact_text(value) -> str | None:
    """A whole or exact number as exact text ("10", "3/5"), or None if it is not one."""
    try:
        number = Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        return None
    return exact_text(number)


def _drop(dropped: list, rule, reason: str, detail: str = "") -> None:
    name = rule.get("name") if isinstance(rule, dict) else None
    dropped.append({"name": name if isinstance(name, str) else None, "reason": reason,
                    "detail": detail})  # fmt: skip


def _candidate(rule: dict, inputs: BuildInputs, section: str) -> dict:
    return {
        "id": "MP-1",  # a stand-in: the real number is asked for only once the rule is kept
        "name": rule["name"],
        "value_type": rule["value_type"],
        "value": rule["value"],
        "unit": rule["unit"],
        "source_document": inputs.document_ref,
        "method_ref": section,
        "confirmed_by": None,
        "text": collapse(rule["quote"]),
    }


def settle_rules(
    inputs: BuildInputs, rules: BuildRules, section_id: str, extracted, allocate: Callable[[], int]
) -> dict:
    """Keep the extracted rules whose quote is in the text, and settle where each value lives.

    `extracted` is what the model returned: a list of {setting, name, value_type, value, unit,
    quote}. A rule is dropped (with a reason code) unless it is well formed, its quote is literally
    in the section or in a section it points at, the value is said in the quote, and, if it would
    become a clause, the template's own validator accepts it. One home per setting: a setting with a
    home reads the template's field (a different value in the document is a conflict, and nothing
    is read or copied); a setting without one becomes a proposed `measure_params` clause, numbered
    by `allocate()`. Never changes `inputs`.
    """
    texts = section_texts(inputs, section_id, rules)
    if not texts:
        return {"status": "refused", "reason": f"the document has no section {section_id}"}
    reads: dict[str, dict] = {}
    mp_rules: list[dict] = []
    quoted: list[dict] = []
    dropped: list[dict] = []
    conflicts: list[dict] = []
    proposed: dict[str, dict] = {}  # name -> the clause proposed under it
    existing = {
        r["name"]: r
        for r in inputs.content.get("measure_params") or []
        if isinstance(r, dict) and r.get("source_document") == inputs.document_ref
    }
    for position, rule in enumerate(extracted if isinstance(extracted, list) else []):
        if position >= rules.max_rules_per_section:
            _drop(dropped, rule, "too_many_rules")
            continue
        problem = _shape_problem(rule)
        if problem:
            _drop(dropped, rule, problem[0], problem[1])
            continue
        if len(collapse(rule["quote"])) < rules.quote_min_chars:
            _drop(dropped, rule, "quote_too_short")
            continue
        where = quotes.quote_found(rule["quote"], texts)
        if where is None:
            _drop(dropped, rule, "quote_not_in_section")
            continue
        if not quotes.value_in_quote(rule["value_type"], rule["value"], rule["quote"]):
            _drop(dropped, rule, "value_not_in_quote")
            continue
        kept = {"name": rule["name"], "setting": rule.get("setting"),
                "quote": collapse(rule["quote"]), "quote_section": where}  # fmt: skip
        home = rules.home_for(rule.get("setting"), rule["name"])
        template_rule = _rule_value(inputs.content, home.section, home.field) if home else None
        if template_rule is not None:
            clause_id, held = template_rule
            _settle_home(rule, home, clause_id, held, kept, reads, quoted, conflicts, dropped)
            continue
        if rule["name"] in existing:
            _settle_existing(rule, existing[rule["name"]], kept, reads, quoted, conflicts)
            continue
        _settle_proposed(
            rule, inputs, where, kept, allocate, proposed, mp_rules, reads, quoted, conflicts,
            dropped,
        )
    return {
        "status": "setting_conflict" if conflicts else "ok",
        "section_id": section_id,
        "reads": sorted(reads.values(), key=lambda r: r["key"]),
        "mp_rules": mp_rules,
        "rules_quoted": quoted,
        "dropped": dropped,
        "conflicts": conflicts,
    }


def _shape_problem(rule) -> tuple[str, str] | None:
    if not isinstance(rule, dict):
        return "rule_malformed", "the rule is not an object"
    for key in ("name", "unit", "quote"):
        if not isinstance(rule.get(key), str) or not rule[key].strip():
            return "rule_malformed", f"`{key}` is missing or is not text"
    setting = rule.get("setting")
    if setting is not None and not isinstance(setting, str):
        return "rule_malformed", "`setting` is not text"
    if rule.get("value_type") not in VALUE_TYPES:
        return "rule_malformed", "`value_type` is not one of " + ", ".join(VALUE_TYPES)
    if "value" not in rule:
        return "rule_malformed", "`value` is missing"
    if not _fits(rule["value_type"], rule["value"]):
        return "rule_invalid", "the value does not fit its type"
    return None


def _settle_home(rule, home, clause_id, held, kept, reads, quoted, conflicts, dropped) -> None:
    """The setting has a home in the template: read it, or stop on a different value."""
    if rule["value_type"] == "names":
        _drop(dropped, rule, "rule_malformed", "a list of names cannot be a plain quantity")
        return
    document = _exact_text(rule["value"])
    template = _exact_text(held)
    if document is None or template is None:
        _drop(dropped, rule, "rule_invalid", "the value is not an exact number")
        return
    if document != template:
        conflicts.append({
            "setting": home.setting, "name": rule["name"], "clause_id": clause_id,
            "field": home.field, "template_value": template, "document_value": document,
            "unit": home.unit, "quote": kept["quote"], "quote_section": kept["quote_section"],
        })  # fmt: skip
        return
    key = f"{clause_id}{key_separator()}{home.field}"
    reads.setdefault(key, {
        "key": key, "clause_id": clause_id, "name": home.setting, "value": held,
        "value_text": template, "unit": home.unit, "source": "template",
    })  # fmt: skip
    quoted.append({**kept, "key": key, "source": "template"})


def _settle_existing(rule, held, kept, reads, quoted, conflicts) -> None:
    """The active template already holds a clause from this document under this name."""
    same = rule["value_type"] == held.get("value_type") and _same_value(
        rule["value"], held.get("value")
    )
    if not same:
        conflicts.append({
            "setting": rule["name"], "name": rule["name"], "clause_id": held["id"],
            "field": None, "template_value": _shown(held.get("value")),
            "document_value": _shown(rule["value"]), "unit": held.get("unit"),
            "quote": kept["quote"], "quote_section": kept["quote_section"],
        })  # fmt: skip
        return
    key = held["id"]
    reads.setdefault(key, {
        "key": key, "clause_id": key, "name": rule["name"], "value": held["value"],
        "value_text": _param_text(held["value_type"], held["value"]), "unit": held["unit"],
        "source": "existing",
    })  # fmt: skip
    quoted.append({**kept, "key": key, "source": "existing"})


def _settle_proposed(
    rule, inputs, where, kept, allocate, proposed, mp_rules, reads, quoted, conflicts, dropped
) -> None:
    name = rule["name"]
    if name in proposed:
        before = proposed[name]
        same = before["value_type"] == rule["value_type"]
        if same and _same_value(before["value"], rule["value"]):
            _drop(dropped, rule, "duplicate_of_a_rule_kept")
        else:
            conflicts.append({
                "setting": name, "name": name, "clause_id": None, "field": None,
                "template_value": _shown(before["value"]), "document_value": _shown(rule["value"]),
                "unit": rule["unit"], "quote": kept["quote"], "quote_section": where,
            })  # fmt: skip
        return
    try:
        MeasureParamRule.model_validate(_candidate(rule, inputs, where))
    except ValidationError as error:
        first = error.errors()[0]["msg"].removeprefix("Value error, ")
        _drop(dropped, rule, "rule_invalid", first)
        return
    clause = _candidate(rule, inputs, where)
    clause["id"] = f"{PARAMS_AREA}-{allocate()}"
    proposed[name] = clause
    mp_rules.append(clause)
    reads[clause["id"]] = {
        "key": clause["id"], "clause_id": clause["id"], "name": name, "value": rule["value"],
        "value_text": _param_text(rule["value_type"], rule["value"]), "unit": rule["unit"],
        "source": "proposed",
    }  # fmt: skip
    quoted.append({**kept, "key": clause["id"], "source": "proposed"})


def _same_value(a, b) -> bool:
    if isinstance(a, list) or isinstance(b, list):
        return a == b
    ta, tb = _exact_text(a), _exact_text(b)
    return ta is not None and ta == tb


def _shown(value) -> str:
    if isinstance(value, list):
        return NAMES_JOINER.join(value)
    return _exact_text(value) or str(value)


def _param_text(value_type, value) -> str:
    """The exact text a measure receives for a clause value (as `params_for` builds it)."""
    if value_type == "names":
        return NAMES_JOINER.join(value)
    return _exact_text(value) or str(value)


# --- checking a proposed function --------------------------------------------------------------
def with_clauses(content: dict, mp_rules) -> dict:
    """A copy of the template content with the proposed `measure_params` clauses added."""
    merged = copy.deepcopy(content)
    merged["measure_params"] = [*(merged.get("measure_params") or []), *copy.deepcopy(mp_rules)]
    return merged


def banned_in(text: str, content: dict) -> list[str]:
    """The words and phrases of the template's wording rules that `text` uses, as whole words,
    ignoring capitals. An underscore counts as a space, so a name such as `gifted_flag` is read as
    two words."""
    text = text.replace("_", " ")
    found = []
    for rule in content.get("wording") or []:
        for item in [*(rule.get("banned") or []), *(rule.get("banned_phrases") or [])]:
            pattern = r"(?<!\w)" + r"\s+".join(re.escape(w) for w in item.split()) + r"(?!\w)"
            if re.search(pattern, text, re.IGNORECASE):
                found.append(item)
    return found


def _problem(check: str, code: str, detail: str, **more) -> dict:
    return {"check": check, "code": code, "detail": detail, **more}


def _result(problems, *, status="ok", **parts) -> dict:
    return {
        "status": status,
        "passed": status == "ok" and not problems,
        "problems": problems,
        "static": None,
        "tests": [],
        "test_results": [],
        "sensitivity": None,
        "keys": [],
        "clause_ids": [],
        "patch": None,
        "notes": [],
        "output_schema": None,
        **parts,
    }


def _proposal_problem(proposal) -> str | None:
    if not isinstance(proposal, dict):
        return "the proposal is not an object"
    for key in ("measure_name", "description", "source", "output_kind"):
        if not isinstance(proposal.get(key), str) or not proposal[key].strip():
            return f"`{key}` is missing or is not text"
    reads = proposal.get("reads")
    if not (isinstance(reads, list) and reads and all(isinstance(r, str) and r for r in reads)):
        return "`reads` is missing or is not a list of clause IDs"
    if not isinstance(proposal.get("tests"), list):
        return "`tests` is missing or is not a list"
    return None


def _settled_problem(inputs, rules, section_id, settled) -> str | None:
    """Why the settlement handed back is not one `settle_rules` could have made, or None. The
    server does not take it on trust: each proposed clause must say words that are literally in the
    section (or one it points at), carry no confirmation, and name this document and the section
    its quote is in."""
    if not isinstance(settled, dict) or not isinstance(settled.get("reads"), list):
        return "the settlement has no list of reads"
    if not isinstance(settled.get("mp_rules"), list):
        return "the settlement has no list of proposed clauses"
    texts = section_texts(inputs, section_id, rules)
    if not texts:
        return f"the document has no section {section_id}"
    area = OPTIONAL_SECTION_AREAS["measure_params"]
    for clause in settled["mp_rules"]:
        if not isinstance(clause, dict):
            return "a proposed clause is not an object"
        match = CLAUSE_ID.match(str(clause.get("id")))
        if not match or match.group(1) != area:
            return f"{clause.get('id')} is not a measure_params clause number"
        where = quotes.quote_found(clause.get("text"), texts)
        if where is None:
            return f"the words of {clause['id']} are not in the section or one it points at"
        if clause.get("method_ref") != where:
            return f"{clause['id']} names a section other than the one its words are in"
        if clause.get("source_document") != inputs.document_ref:
            return f"{clause['id']} names a document other than this one"
        if clause.get("confirmed_by") is not None:
            return f"{clause['id']} already names someone who confirmed it"
    return None


def check_function(
    inputs: BuildInputs,
    rules: BuildRules,
    sandbox,
    section_id: str,
    settled: dict,
    proposal: dict,
    runner: Callable,
) -> dict:
    """Run every deterministic check on a proposed function. Returns a report; stores nothing.

    In this order, stopping at the first group that finds a problem: the shape of the proposal; the
    wording of its name and description; the output kind; the clauses it declares against the ones
    the build settled; the patch against the template's own validator; the static check (which runs
    nothing); the tests (in the sandbox); the independent tests; the sensitivity check. `runner` has
    the signature of `sandbox.client.run`. A sandbox that cannot be reached is `status: error`.
    The same input gives the same report.
    """
    bad = _proposal_problem(proposal)
    if bad:
        return _result([_problem("proposal", "proposal_malformed", bad)])
    bad = _settled_problem(inputs, rules, section_id, settled)
    if bad:
        return _result([_problem("proposal", "settled_invalid", bad)])
    problems: list[dict] = []
    for field in ("measure_name", "description"):
        for word in banned_in(proposal[field], inputs.content):
            detail = f"{field} uses {word!r}, which the rulebook's wording rules ban"
            problems.append(_problem("wording", "banned_word", detail))
    if proposal["output_kind"] not in rules.output_kinds:
        allowed = ", ".join(rules.output_kinds)
        detail = f"a build returns {allowed}"
        problems.append(_problem("proposal", "output_kind_not_allowed", detail))
    reads = settled.get("reads") or []
    known = {r["clause_id"] for r in reads}
    for clause in proposal["reads"]:
        if clause not in known:
            detail = f"{clause} is not a clause the build settled for this section"
            problems.append(_problem("proposal", "clause_not_settled", detail))
    if problems:
        return _result(problems)

    declared = list(dict.fromkeys(proposal["reads"]))
    used = [m for m in settled.get("mp_rules") or [] if m["id"] in declared]
    merged = with_clauses(inputs.content, used)
    try:
        parse_template(merged)
    except TemplateError as error:
        return _result([_problem("patch", "patch_invalid", str(error))])

    source, kind = proposal["source"], proposal["output_kind"]
    report = static_check.check_source(
        source,
        output_kind=kind,
        rules=sandbox,
        template_values=template_values(merged),
        declared_clause_ids=declared,
    )
    for finding in report.findings:
        detail = f"line {finding.line}: {finding.detail}"
        if finding.clause_ids:
            detail += f" (clauses: {', '.join(finding.clause_ids)})"
        problems.append(
            _problem("static", finding.code, detail, clause_ids=list(finding.clause_ids))
        )
    static = report.to_dict()
    keys = list(static_check.params_read(source, sandbox))
    settled_keys = {r["key"] for r in reads}
    for key in keys:
        if key not in settled_keys:
            detail = f"{key} is not a value the build settled for this section"
            problems.append(_problem("static", "key_not_settled", detail))
    if problems:
        return _result(problems, static=static, keys=keys)

    notes: list[str] = []
    tests = _normalise_tests(proposal["tests"], inputs, rules, sandbox, problems, notes)
    if problems:
        return _result(problems, static=static, keys=keys, notes=notes)
    try:
        values = params_for(
            merged, keys, declared, separator=sandbox.key_separator, unconfirmed_ok=True
        )
    except ParamsRefusal as refusal:
        return _result([_problem("params", refusal.kind, refusal.detail)], static=static, keys=keys)

    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    results, fields = [], set()
    for test in tests:
        try:
            reply = runner(source, digest, test["records"], values, output_kind=kind)
        except RunnerRefusal as refusal:
            if refusal.kind == UNAVAILABLE:
                return _result(
                    [], status="error", kind=UNAVAILABLE, detail=refusal.detail, static=static
                )
            results.append({"name": test["name"], "passed": False})
            problems.append(
                _problem("tests", "test_run_refused", f"the sandbox refused it: {refusal.kind}",
                         test=test["name"])
            )  # fmt: skip
            continue
        output = reply["output"]
        bad = _output_problem(output, rules)
        if bad:
            results.append({"name": test["name"], "passed": False})
            problems.append(_problem("output", "output_not_numbers", bad, test=test["name"]))
            continue
        fields.update(output)
        wrong = _expected_problem(test["expected"], output)
        results.append({"name": test["name"], "passed": wrong is None})
        if wrong:
            problems.append(_problem("tests", "test_failed", wrong, test=test["name"]))
    independent = sum(1 for t in tests if t["source"] == DOCUMENT_EXAMPLE)
    if independent < rules.min_document_examples:
        detail = (
            f"{independent} test(s) come from a worked example quoted from the methodology; "
            f"{rules.min_document_examples} needed"
        )
        problems.append(_problem("tests", "no_independent_test", detail))
    common = {
        "static": static, "keys": keys, "tests": tests, "test_results": results, "notes": notes,
        "clause_ids": sorted({k.split(sandbox.key_separator)[0] for k in keys}),
    }  # fmt: skip
    if problems:
        return _result(problems, **common)

    sensitivity = check_sensitivity(
        source,
        [{"name": t["name"], "records": t["records"]} for t in tests],
        values,
        output_kind=kind,
        runner=runner,
    )
    for key in sensitivity.insensitive:
        detail = f"changing {key} changes no test answer: it is unused or written into the code"
        problems.append(_problem("sensitivity", "parameter_not_sensitive", detail, key=key))
    for name in sensitivity.baseline_failed:
        problems.append(_problem("sensitivity", "test_failed", "failed to run again", test=name))
    mp_read = {k.split(sandbox.key_separator)[0] for k in keys}
    patch = None
    if used:
        patch = {
            "base_template_version": inputs.template_version,
            "measure_params": [m for m in used if m["id"] in mp_read],
        }
    return _result(
        problems,
        **{**common, "sensitivity": sensitivity.to_dict(), "patch": patch,
           "output_schema": {"kind": kind, "fields": sorted(fields)}},
    )  # fmt: skip


def _output_problem(output, rules: BuildRules) -> str | None:
    """Why an output is not exact numbers under keys, or None."""
    if not isinstance(output, dict) or not output:
        return "the function must return an object with at least one key"
    key_ok = re.compile(rules.output_key_pattern)
    for key, value in output.items():
        if not isinstance(key, str) or not key_ok.match(key):
            return f"the key {key!r} is not lower case words joined by underscores"
        if isinstance(value, bool) or _exact_text(value) is None:
            return f"the value of {key} is not an exact number (a yes or no is 1 or 0)"
    return None


def _expected_problem(expected: dict, output: dict) -> str | None:
    for key in sorted(expected):
        if key not in output:
            return f"{key}: expected {expected[key]}, the function returned no such key"
        if Fraction(output[key]) != Fraction(expected[key]):
            return f"{key}: expected {expected[key]}, got {_exact_text(output[key])}"
    return None


DOCUMENT_EXAMPLE = "document_example"
BUILD_TEST = "build"
MODEL_SOURCES = (BUILD_TEST, DOCUMENT_EXAMPLE)  # the only sources the model may claim


def _exact_value(value) -> str | None:
    """Exact text of an int or exact-number text; a float, a bool or anything else is None."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    return _exact_text(value)


def _normalise_tests(tests, inputs, rules, sandbox, problems: list, notes: list) -> list[dict]:
    """The tests as stored: exact text everywhere, a worked-example test kept as one only when its
    quote is literally in the document and its numbers are in the quote (else it is demoted to a
    build test and the reason is noted)."""
    if not tests:
        problems.append(_problem("tests", "test_invalid", "there are no tests"))
        return []
    out, seen = [], set()
    key_ok = re.compile(rules.output_key_pattern)
    texts = document_texts(inputs)
    for position, test in enumerate(tests):
        name = test.get("name") if isinstance(test, dict) else None
        label = name if isinstance(name, str) and name else f"test {position}"

        def invalid(detail: str, label=label) -> None:
            problems.append(_problem("tests", "test_invalid", detail, test=label))

        if not isinstance(test, dict) or not isinstance(name, str) or not name.strip():
            invalid("a test needs a name")
            continue
        if name in seen:
            invalid("two tests have this name")
            continue
        seen.add(name)
        if test.get("source") not in MODEL_SOURCES:
            invalid("the source of a test is build or document_example")
            continue
        records = _records(test.get("records"), rules)
        if records is None:
            fields = ", ".join(rules.record_fields)
            invalid(f"records is a non-empty list of objects with the fields {fields}, as "
                    "exact numbers")
            continue
        expected = test.get("expected")
        if not (isinstance(expected, dict) and expected):
            invalid("expected is a non-empty object of exact numbers")
            continue
        normal = {k: _exact_value(v) for k, v in expected.items()}
        if any(v is None or not key_ok.match(k) for k, v in normal.items()):
            invalid("expected holds exact numbers under keys of lower case words")
            continue
        entry = {"name": name, "source": BUILD_TEST, "records": records, "expected": normal}
        if test["source"] == DOCUMENT_EXAMPLE:
            reason, where, quote = _document_example(
                test.get("quote"), records, normal, texts, sandbox
            )
            if reason:
                notes.append(f"{name}: counted as a build test: {reason}")
            else:
                entry.update(source=DOCUMENT_EXAMPLE, quote=quote, quote_section=where)
        out.append(entry)
    return out


def _records(records, rules: BuildRules) -> list[dict] | None:
    if not (isinstance(records, list) and records):
        return None
    fields = set(rules.record_fields)
    out = []
    for record in records:
        if not isinstance(record, dict) or set(record) != fields:
            return None
        normal = {k: _exact_value(v) for k, v in record.items()}
        if any(v is None for v in normal.values()):
            return None
        out.append(normal)
    return out


def _document_example(quote, records, expected, texts, sandbox):
    """(reason it is not independent or None, the section the quote is in, the quote)."""
    where = quotes.quote_found(quote, texts)
    if where is None:
        return "its quote is not in the document", None, None
    numbers = [v for record in records for v in record.values()]
    problem = quotes.worked_example_problem(quote, numbers, expected, sandbox.literals)
    return problem, where, collapse(quote)
