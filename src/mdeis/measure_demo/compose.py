"""The pure part of the teacher demo (T-073). No database, no sandbox, no network, no clock.

What lives here:

  try_records       the teacher's synthetic scores, checked, as the records the function is given
  expected_numbers  the teacher's own expected answer, checked against the measure's fields
  compare           an expected answer set against what the function returned, field by field
  example_rows      the worked examples of the methodology, with what the function returned
  case_rows         the teacher's own cases already stored
  clause_rows       the rules and values the measure reads, each with its state
  approval_gaps     what the registry still needs before a confirmation (named in plain words)
  held_item         one version held by the automated review, for the approver's list
  demo_view         everything above in one answer the page shows

Every number shown is passed in as a result of the sandbox run (a Fraction, or exact text from the
registry). Nothing here works a number out, and nothing holds a school value: the subjects, the
stages and the scale come from the rulebook that the caller reads.

Wording. The text here describes data. It never labels a learner and never ranks or compares
learners with one another.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from fractions import Fraction

from ..core.measures import EXACT_NUMBER, FIELD_NAME, number_view
from ..core.numbers import exact_text
from ..core.template import CLAUSE_ID, OPTIONAL_SECTION_AREAS, SECTION_AREAS
from ..registry import transitions as t

# What a "try your own" and a saved case may hold. The body is closed: any other field is refused,
# so no field can carry a learner, a name, a school, a person or a status.
TRY_FIELDS = ("stage", "entries")
CASE_FIELDS = ("stage", "entries", "expected", "agreed")
ENTRY_FIELDS = ("subject", "term", "score")

# The synthetic records a try makes. The record number and the year are labels, not data: the
# function reads the scores.
RECORD_PREFIX = "TRY-"
RECORD_YEAR = "demo"
SCORE_TEXT = re.compile(r"[0-9]+(?:\.[0-9]+|/[0-9]+)?")  # plain text: 52, 52.5, 61/4
CLAUSE_START = re.compile(r"[A-Z]{2}-[1-9][0-9]*")  # the clause a value key starts with
PARAMS_SECTION = "measure_params"
BUILD_RUN = "BUILD-"  # the start of a run number the build command gives (orchestrator build_cli)

PLACEHOLDER = "placeholder"  # a proposed value no teacher has confirmed
CONFIRMED = "confirmed"
IN_RULEBOOK = "in_rulebook"  # a rule of the school's rulebook that is not a measure parameter
MISSING = "missing"
MEASURE_PARAMETER = "measure_parameter"
RULEBOOK = "rulebook"
NOT_RUN = "not_run"

SYNTHETIC_NOTICE = (
    "Synthetic scores only. Type invented numbers, never the scores of a real learner. "
    "There is no place here for a name or an ID."
)
SENSITIVITY_CHECKED = "Checked by the build, which does not store a version that fails the check."
SENSITIVITY_UNKNOWN = "Not recorded: this version did not come from the build."
NOTE_NOT_RUN = "A worked example could not be run here, so it is not shown as checked."
NOTE_MISMATCH = (
    "A worked example does not match what the document says. Read it before you decide."
)

GAP_TEXT = {
    "not_at_demo": "This version is not waiting for a teacher's decision.",
    "tests_not_passed": "The tests have not all passed.",
    "no_independent_test": (
        "No test comes from outside the build. A worked example from the methodology or a case "
        "of your own is needed."
    ),
    "static_not_passed": "The static check did not pass.",
    "review_holding": (
        "The automated review is holding this version and no developer has cleared the hold."
    ),
}
WORDING_GAP = (
    "The name, the description or a review reason uses wording the rulebook bans: {words}."
)

REFUSAL_TEXT = {
    "bad_body": "The request must be one object with a stage and a list of scores.",
    "unknown_field": "The request may hold only these fields: {fields}.",
    "unknown_stage": "Choose a stage from the list.",
    "no_scores": "Enter at least one score.",
    "bad_entry": "Each row needs a subject, a term and a score, and nothing else.",
    "unknown_subject": "Choose a subject from the list.",
    "bad_term": "A term is a whole number from 1.",
    "bad_score": "A score is plain text such as 52, 52.5 or 61/4.",
    "score_off_scale": "A score must be from 0 to {scale}, the scale of the rulebook.",
    "duplicate_entry": "Each subject and term can be entered once.",
    "one_subject_needed": "This measure works on one subject at a time. Use one subject.",
    "bad_expected": "Your expected answer is exact numbers for fields this measure returns.",
}


class TryRefused(ValueError):
    """A try or a case that cannot be run. `code` is short and fixed. `detail` is plain words and
    never repeats what was typed."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _refuse(code: str, **given) -> TryRefused:
    return TryRefused(code, REFUSAL_TEXT[code].format(**given))


# --- the teacher's synthetic scores ------------------------------------------------------------
def _score(value, scale_max: Fraction) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise _refuse("bad_score")
    if isinstance(value, str) and not SCORE_TEXT.fullmatch(value):
        raise _refuse("bad_score")
    try:
        number = Fraction(value)
    except (ValueError, ZeroDivisionError):
        raise _refuse("bad_score") from None
    if number < 0 or number > scale_max:
        raise _refuse("score_off_scale", scale=exact_text(scale_max))
    return number


def try_records(
    body,
    *,
    fields: tuple,
    subjects: Iterable[str],
    stages: Iterable[str],
    scale_max: Fraction,
    scope: str,
) -> list[dict]:
    """The records a measure is given for a try, or TryRefused.

    `body` is `{"stage": ..., "entries": [{"subject", "term", "score"}, ...]}`. It may hold only the
    fields in `fields`. The stage and each subject must be ones the rulebook lists, a term is a
    whole number from 1, a score is plain text or a whole number from 0 to the rulebook's scale, and
    a subject and term are entered once. For a measure that works on one subject (`learner_skill`)
    every row is for the same subject. The records come out in term order, then subject, in the
    shape the sandbox is given at run time, with the score as exact text and a made-up record
    number. Nothing in them names a learner.
    """
    if not isinstance(body, dict):
        raise _refuse("bad_body")
    if set(body) - set(fields):
        raise _refuse("unknown_field", fields=", ".join(fields))
    if "stage" not in body or "entries" not in body:
        raise _refuse("bad_body")
    stage = body["stage"]
    if not isinstance(stage, str) or stage not in set(stages):
        raise _refuse("unknown_stage")
    entries = body["entries"]
    if not isinstance(entries, list) or not entries:
        raise _refuse("no_scores")
    known = set(subjects)
    seen: set[tuple] = set()
    chosen: list[tuple] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != set(ENTRY_FIELDS):
            raise _refuse("bad_entry")
        subject, term = entry["subject"], entry["term"]
        if not isinstance(subject, str) or subject not in known:
            raise _refuse("unknown_subject")
        if isinstance(term, bool) or not isinstance(term, int) or term < 1:
            raise _refuse("bad_term")
        score = _score(entry["score"], scale_max)
        if (subject, term) in seen:
            raise _refuse("duplicate_entry")
        seen.add((subject, term))
        chosen.append((term, subject, score))
    if scope == "learner_skill" and len({s for _, s, _ in chosen}) != 1:
        raise _refuse("one_subject_needed")
    chosen.sort(key=lambda row: (row[0], row[1]))
    return [
        {
            "record_id": f"{RECORD_PREFIX}{position}",
            "year": RECORD_YEAR,
            "term": term,
            "stage": stage,
            "subject": subject,
            "score": exact_text(score),
        }
        for position, (term, subject, score) in enumerate(chosen, start=1)
    ]


def expected_numbers(expected, declared_fields: Iterable[str]) -> dict[str, str]:
    """The teacher's expected answer as exact text, or TryRefused. Each key must be a field the
    measure declares, each value exact text or a whole number. Nothing is rounded."""
    allowed = set(declared_fields)
    if not isinstance(expected, dict) or not expected:
        raise _refuse("bad_expected")
    made: dict[str, str] = {}
    for key in sorted(expected, key=str):
        value = expected[key]
        if not isinstance(key, str) or not FIELD_NAME.fullmatch(key) or key not in allowed:
            raise _refuse("bad_expected")
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise _refuse("bad_expected")
        if isinstance(value, str) and not EXACT_NUMBER.fullmatch(value):
            raise _refuse("bad_expected")
        try:
            made[key] = exact_text(Fraction(value))
        except (ValueError, ZeroDivisionError):
            raise _refuse("bad_expected") from None
    return made


def answer_view(numbers: Mapping[str, Fraction]) -> dict[str, dict]:
    """Each number of an answer to two places with its exact form beside it, fields in order."""
    return {name: number_view(numbers[name]) for name in sorted(numbers)}


def compare(expected: Mapping[str, str], numbers: Mapping[str, Fraction]) -> dict:
    """An expected answer (exact text) set against what the function returned, field by field.
    A field the function did not return is a mismatch, not an error."""
    fields = []
    for name in sorted(expected):
        wanted = Fraction(expected[name])
        got = numbers.get(name)
        fields.append(
            {
                "field": name,
                "expected": number_view(wanted),
                "returned": None if got is None else number_view(got),
                "match": got is not None and got == wanted,
            }
        )
    return {"match": all(f["match"] for f in fields), "fields": fields}


# --- the tests, as the teacher sees them --------------------------------------------------------
def _inputs(test: Mapping) -> list[str]:
    return [str(r.get("score")) for r in test.get("records") or [] if isinstance(r, Mapping)]


def _expected_view(test: Mapping) -> dict[str, dict]:
    return {k: number_view(Fraction(v)) for k, v in sorted((test.get("expected") or {}).items())}


def example_rows(tests: Iterable[Mapping], answers: Mapping[str, Mapping]) -> list[dict]:
    """The worked examples (tests whose expected numbers come from a quote in the methodology).

    `answers` maps a test name to `{"numbers": {field: Fraction}}` (what the function returned when
    it was run for the demo) or `{"problem": code}` (it could not be run). A match is `True`, a
    mismatch `False`, and an example that could not be run `None`."""
    rows = []
    for test in tests:
        if test.get("source") != "document_example":
            continue
        answer = answers.get(test["name"]) or {"problem": NOT_RUN}
        row = {
            "name": test["name"],
            "quote": test.get("quote"),
            "quote_section": test.get("quote_section"),
            "inputs": _inputs(test),
            "expected": _expected_view(test),
            "returned": None,
            "match": None,
            "fields": [],
            "problem": None,
        }
        if "numbers" in answer:
            checked = compare(test.get("expected") or {}, answer["numbers"])
            row.update(
                returned=answer_view(answer["numbers"]),
                match=checked["match"],
                fields=checked["fields"],
            )
        else:
            row["problem"] = answer.get("problem") or NOT_RUN
        rows.append(row)
    return rows


def case_rows(tests: Iterable[Mapping], test_result: Mapping | None) -> list[dict]:
    """The cases a teacher has stored for this version, with whether each passed."""
    results = {r["name"]: r["passed"] for r in (test_result or {}).get("results") or []}
    return [
        {
            "name": test["name"],
            "inputs": _inputs(test),
            "expected": _expected_view(test),
            "passed": results.get(test["name"]),
            "basis": test.get("basis"),
        }
        for test in tests
        if test.get("source") == "teacher"
    ]


def held_item(row: Mapping) -> dict:
    """One held version as the approver's list shows it: what is needed to recognise it and the
    reasons the automated review gave, as plain sentences. Nothing about a person, and no number."""
    reasons = row.get("review_reasons") or []
    return {
        "measure_id": row["measure_id"],
        "version": row["version"],
        "name": row["name"],
        "method_ref": row["method_ref"],
        "description": row["description"],
        "reasons": [r for r in reasons if isinstance(r, str) and r.strip()],
    }


def test_summary(tests: Iterable[Mapping], result: Mapping | None) -> dict:
    """How many tests there are by where they came from, and the recorded outcome."""
    listed = list(tests)
    by_source: dict[str, int] = {}
    for test in listed:
        by_source[test["source"]] = by_source.get(test["source"], 0) + 1
    recorded = result or {}
    return {
        "passed": bool(listed) and recorded.get("passed") is True,
        "ran": recorded.get("ran", 0),
        "failed": list(recorded.get("failed") or []),
        "by_source": by_source,
    }


# --- the rules and values the measure reads -----------------------------------------------------
def _find_rule(content: Mapping, clause_id: str):
    for section in (*SECTION_AREAS, *OPTIONAL_SECTION_AREAS):
        for rule in content.get(section) or []:
            if isinstance(rule, Mapping) and rule.get("id") == clause_id:
                return rule
    return None


def _value_text(rule: Mapping) -> str:
    value = rule.get("value")
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def clause_rows(
    clause_ids: Iterable[str],
    content: Mapping,
    patch_clauses: Iterable[Mapping],
    values_used: Mapping[str, str],
) -> list[dict]:
    """One row for each clause the measure declares, in the order declared.

    A measure parameter is shown as a placeholder until a teacher has confirmed it: a value the
    build proposed from the methodology, which sits in a patch and not yet in the rulebook. A rule
    of the rulebook that is not a measure parameter is shown as one. The rulebook wins when it
    already holds the clause. `values_used` is what the function was given, by key."""
    area = OPTIONAL_SECTION_AREAS[PARAMS_SECTION]
    proposed = {r.get("id"): r for r in patch_clauses if isinstance(r, Mapping)}
    rows = []
    for clause_id in clause_ids:
        match = CLAUSE_ID.match(clause_id)
        is_parameter = bool(match) and match.group(1) == area
        rule = _find_rule(content, clause_id)
        if rule is None and is_parameter:
            rule = proposed.get(clause_id)
        reads = [
            {"key": key, "value": value}
            for key, value in sorted(values_used.items())
            if (found := CLAUSE_START.match(key)) and found.group(0) == clause_id
        ]
        row = {
            "clause_id": clause_id,
            "kind": MEASURE_PARAMETER if is_parameter else RULEBOOK,
            "name": None,
            "state": MISSING,
            "text": None,
            "value": None,
            "unit": None,
            "method_ref": None,
            "source_document": None,
            "confirmed_by": None,
            "reads": reads,
        }
        if rule is not None:
            row["text"] = rule.get("text")
            row["name"] = rule.get("name")
            row["method_ref"] = rule.get("method_ref")
            if is_parameter:
                confirmed = rule.get("confirmed_by") or None
                row.update(
                    state=CONFIRMED if confirmed else PLACEHOLDER,
                    value=_value_text(rule),
                    unit=rule.get("unit"),
                    source_document=rule.get("source_document"),
                    confirmed_by=confirmed,
                )
            else:
                row["state"] = IN_RULEBOOK
        rows.append(row)
    return rows


# --- what is still missing ----------------------------------------------------------------------
def approval_gaps(facts: Mapping, wording_hits: Iterable[str]) -> list[dict]:
    """What the registry still needs before this version can be confirmed, in a fixed order. `facts`
    holds the answers of the database's own functions for the version: its status, whether the tests
    all passed, whether one comes from outside the build, whether the static check passed and
    whether the review is clear (a pass, or a hold a developer cleared). Banned wording in what a
    model wrote is a gap too: a measure with such wording is never shown to the chat."""
    gaps = []
    if facts.get("status") != t.AWAITING_DEMO:
        gaps.append("not_at_demo")
    if not facts.get("tests_passed"):
        gaps.append("tests_not_passed")
    if not facts.get("independent_test"):
        gaps.append("no_independent_test")
    if not facts.get("static_passed"):
        gaps.append("static_not_passed")
    if not facts.get("review_cleared"):
        gaps.append("review_holding")
    made = [{"code": code, "text": GAP_TEXT[code]} for code in gaps]
    hits = list(wording_hits)
    if hits:
        text = WORDING_GAP.format(words=", ".join(hits))
        made.append({"code": "banned_wording", "text": text})
    return made


# --- the whole view -----------------------------------------------------------------------------
def _static(report: Mapping | None) -> dict:
    found = (report or {}).get("findings") or []
    return {
        "passed": (report or {}).get("passed") is True,
        "findings": [f"{f.get('code')} (line {f.get('line')}): {f.get('detail')}" for f in found],
    }


def demo_view(
    *,
    version: Mapping,
    quotes: list,
    clauses: list,
    patch: Mapping | None,
    skipped: list,
    build_anyway: list,
    examples: list,
    cases: list,
    facts: Mapping,
    wording: Iterable[str],
    form: Mapping,
) -> dict:
    """What a teacher sees for one version waiting at the demo. Plain data: the page puts every
    string in with textContent. It holds no source code and no name of a learner."""
    gaps = approval_gaps(facts, wording)
    built = str(version.get("built_by_run") or "").startswith(BUILD_RUN)
    notes = []
    if any(e.get("problem") for e in examples):
        notes.append(NOTE_NOT_RUN)
    if any(e.get("match") is False for e in examples):
        notes.append(NOTE_MISMATCH)
    return {
        "status": "ok",
        "measure_id": version["measure_id"],
        "version": version["version"],
        "version_status": version["status"],
        "name": version["name"],
        "description": version["description"],
        "method_ref": version["method_ref"],
        "input_scope": version["input_scope"],
        "output_kind": version["output_kind"],
        "output_fields": sorted((version.get("output_schema") or {}).get("fields") or []),
        "origin": {
            "methodology_document_id": version["methodology_document_id"],
            "section_sha256": version["section_sha256"],
            "code_sha256": version["code_sha256"],
            "template_version": version["template_version"],
            "quotes": quotes,
        },
        "clauses": clauses,
        "patch": None if patch is None else dict(patch),
        "skipped": skipped,
        "build_anyway": build_anyway,
        "review": {
            "result": version.get("review_result"),
            "reasons": list(version.get("review_reasons") or []),
            "cleared_by": version.get("hold_cleared_name"),
        },
        "checks": {
            "static": _static(version.get("static_check")),
            "tests": test_summary(version.get("tests") or [], version.get("test_result")),
            "sensitivity": {
                "passed": True if built else None,
                "how": SENSITIVITY_CHECKED if built else SENSITIVITY_UNKNOWN,
            },
        },
        "examples": examples,
        "cases": cases,
        "try": {**form, "notice": SYNTHETIC_NOTICE},
        "approval": {"can_confirm": not gaps, "gaps": gaps},
        "notes": notes,
    }
