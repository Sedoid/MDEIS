"""The methodology clarity gate. Ordinary code: no model, no network, no database.

A methodology that is too vague, incomplete or open ended to build from is turned back with a list
of faults, each naming a section, so the person who uploaded it can fix it and send it again. The
verdict comes from the rules in `templates/gate-v1.yaml` (clause IDs GT-1 to GT-10). This file holds
the procedure for each rule. Every word list, pattern and limit it uses is read from that file, and
it holds no value that belongs to a school. It does not depend on how a document numbers its
sections: settings are found by their name, measures by what their text says.

Input: the sections of the document (each with `id`, `title` and `text`) and the template's
`method_ref` list ({rule id: the section it rests on}). Output: a list of `Fault`, in section
order. The same input always gives the same list.

A fault quotes the section text exactly, cut to the length the file allows. It never rewords it.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

GATE_ENV = "GATE_FILE"  # names the gate file, if it is not in the usual place
GATE_NAME = "gate-v1.yaml"
CONTAINER_FOLDER = "/templates"  # where the compose files mount the templates folder

CHECK_IDS = tuple(f"GT-{n}" for n in range(1, 11))
# What each check must carry in the file: a plain text, or a non-empty list of words, or a count.
PARAMS = {
    "GT-1": {"required_settings": "words", "row_index_cell": "pattern", "needed": "text"},
    "GT-2": {"input_markers": "words"},
    "GT-3": {"value_words": "words"},
    "GT-4": {"vague_words": "words"},
    "GT-5": {"example_markers": "words", "document_example_phrases": "words", "needed": "text"},
    "GT-6": {
        "operators": "words",
        "max_name_words": "count",
        "min_name_words": "count",
        "ignore_leading_words": "words",
    },
    "GT-9": {
        "trigger_phrases": "words",
        "procedure_markers": "words",
        "min_numbered_steps": "count",
    },
    "GT-10": {"vague_scope_words": "words", "reference_words": "words"},
}

# How a section is cut into sentences: after . ! or ?, at a blank line, and before a list item.
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n\s*\n|\n(?=- )")
# A lettered measure under a section (`n.m.A`): the reader's own id for the document's lettered
# measures. It is a form of numbering, not a school rule.
LETTERED = re.compile(r"\.[A-Z]$")
NUMBER = re.compile(r"\d")
NUMBERED_STEP = re.compile(r"^[ \t]*\d+\.[ \t]", re.MULTILINE)  # a list item "1. Do this"
# A number that only points at another section: "section 12", "see 6.4", "sections 11 and 12".
REFERENCE_NUMBER = r"\d+(?:\.\d+)*(?:\.[A-Z])?"


class GateFileError(Exception):
    """The gate file is missing, is not YAML, or does not hold what the gate needs."""


@dataclass(frozen=True)
class Fault:
    code: str
    section: str | None  # the document's own number for the section, None for the whole document
    quote: str  # exact words from the section, at most the limit in the file; "" when none apply
    needed: str  # what to add or change, in plain words
    rule: str  # GT-n

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Gate:
    version: str
    quote_max: int
    formula_markers: tuple  # marks that say a section works something out
    definition_phrases: tuple  # phrases that say a section defines a calculation
    source_phrases: tuple
    checks: dict  # GT-n -> that check's entry in the file


# --- the gate file -----------------------------------------------------------------------------
def gate_path() -> Path:
    """The gate file: GATE_FILE if set, else the templates folder of the repo, else /templates."""
    named = os.environ.get(GATE_ENV)
    if named:
        return Path(named)
    beside_source = Path(__file__).resolve().parents[3] / "templates" / GATE_NAME
    if beside_source.is_file():
        return beside_source
    return Path(CONTAINER_FOLDER) / GATE_NAME


def _pattern_ok(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        re.compile(value)
    except re.error:
        return False
    return True


def _kind_ok(value: Any, kind: str) -> bool:
    if kind == "text":
        return isinstance(value, str) and bool(value.strip())
    if kind == "pattern":
        return _pattern_ok(value)
    if kind == "words":
        return (
            isinstance(value, list)
            and bool(value)
            and all(isinstance(w, str) and w.strip() for w in value)
        )
    return isinstance(value, int) and not isinstance(value, bool) and value > 0  # a count


def _need(data: Any, key: str, kind: str, where: str) -> Any:
    if not isinstance(data, dict) or not _kind_ok(data.get(key), kind):
        raise GateFileError(f"{where}: `{key}` is missing or is not a {kind}")
    return data[key]


def _check_aliases(entry: dict, where: str) -> None:
    """`aliases`: {a required setting: [other names for it]}. Each name must be a listed setting."""
    aliases = entry.get("aliases")
    if not isinstance(aliases, dict):
        raise GateFileError(f"{where}: `aliases` is missing or is not a mapping")
    for name, alternatives in aliases.items():
        if name not in entry["required_settings"]:
            raise GateFileError(
                f"{where}: `aliases` names {name!r}, which is not a required setting"
            )
        if not _kind_ok(alternatives, "words"):
            raise GateFileError(f"{where}: the aliases of {name!r} are not a list of names")


def load_gate(path: str | Path | None = None) -> Gate:
    """Read and check the gate file. Raises GateFileError with a plain reason."""
    path = Path(path) if path else gate_path()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        reason = error.strerror or error
        raise GateFileError(f"cannot read the gate file {path}: {reason}") from error
    except yaml.YAMLError as error:
        raise GateFileError(f"{path} is not valid YAML: {error}") from error
    if not isinstance(data, dict):
        raise GateFileError(f"{path}: the gate file must be a mapping at the top level")
    version = _need(data, "gate_version", "text", str(path))
    quote_max = _need(data, "quote_max_chars", "count", str(path))
    detection = data.get("measure_detection")
    where = f"{path}, measure_detection"
    formula_markers = _need(detection, "formula_markers", "words", where)
    definition_phrases = _need(detection, "definition_phrases", "words", where)
    source_phrases = _need(data, "source_phrases", "words", str(path))
    entries = data.get("checks")
    if not isinstance(entries, list) or [
        e.get("id") if isinstance(e, dict) else None for e in entries
    ] != list(CHECK_IDS):
        wanted = ", ".join(CHECK_IDS)
        raise GateFileError(f"{path}: `checks` must hold {wanted} once each, in order")
    checks = {}
    for entry in entries:
        where = f"{path}, {entry['id']}"
        _need(entry, "code", "text", where)
        _need(entry, "text", "text", where)
        for key, kind in PARAMS.get(entry["id"], {}).items():
            _need(entry, key, kind, where)
        if entry["id"] == "GT-1":
            _check_aliases(entry, where)
        checks[entry["id"]] = entry
    if len({c["code"] for c in checks.values()}) != len(checks):
        raise GateFileError(f"{path}: two checks share one code")
    return Gate(
        version,
        quote_max,
        tuple(formula_markers),
        tuple(definition_phrases),
        tuple(source_phrases),
        checks,
    )


# --- reading the text --------------------------------------------------------------------------
def _phrase(phrase: str) -> re.Pattern:
    """A whole word or phrase, any capitals, any spacing or line break between its words.

    A phrase that starts or ends with a sign (a division mark, a full stop) has no word edge there.
    """
    body = r"\s+".join(re.escape(w) for w in phrase.split())
    head = r"(?<!\w)" if phrase.strip()[0].isalnum() else ""
    tail = r"(?!\w)" if phrase.strip()[-1].isalnum() else ""
    return re.compile(head + body + tail, re.IGNORECASE)


def _patterns(phrases) -> list:
    return [_phrase(p) for p in phrases]


def _first_hit(patterns: list, text: str) -> tuple | None:
    """(index in `patterns`, match) of the earliest match in `text`, or None."""
    best = None
    for index, pattern in enumerate(patterns):
        found = pattern.search(text)
        if found and (best is None or found.start() < best[1].start()):
            best = (index, found)
    return best


def _any(patterns: list, text: str) -> bool:
    return any(p.search(text) for p in patterns)


def _sentences(text: str) -> list:
    """(start, end) of each sentence in `text`. A quote is cut from these, so it is exact."""
    spans, position = [], 0
    for brk in SENTENCE_BREAK.finditer(text):
        spans.append((position, brk.start()))
        position = brk.end()
    spans.append((position, len(text)))
    out = []
    for start, end in spans:
        chunk = text[start:end]
        if chunk.strip():
            lead, trail = len(chunk) - len(chunk.lstrip()), len(chunk) - len(chunk.rstrip())
            out.append((start + lead, end - trail))
    return out


def _clip(text: str, start: int, end: int, limit: int, focus: int | None = None) -> str:
    """text[start:end], cut to `limit` characters. A cut keeps the word at `focus` in view."""
    if end - start <= limit:
        return text[start:end]
    if focus is not None and focus > start + limit:
        return text[focus - limit : focus]
    return text[start : start + limit]


def _first_sentence(text: str, limit: int) -> str:
    spans = _sentences(text)
    return _clip(text, *spans[0], limit) if spans else ""


def _first_line(text: str, limit: int) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:limit]
    return ""


def _reference_pattern(words) -> re.Pattern:
    """A pointer to another section: a word like "section" or "see" and then its number(s)."""
    head = "|".join(re.escape(w) for w in words)
    more = rf"(?:\s*(?:,|and|or)\s*{REFERENCE_NUMBER})*"
    return re.compile(rf"(?<!\w)(?:{head})\s+{REFERENCE_NUMBER}{more}", re.IGNORECASE)


def _setting_line(name: str, index_cell: str) -> re.Pattern:
    """A line that starts with the setting's name and has text after it.

    Before the name there may be table bars, bold marks, a list dash and a cell that only numbers
    the row. After it there may be bold marks, a colon, a full stop or a table bar, and then the
    text itself. A label with nothing after it but marks and stops does not define a setting.
    So these all count: a Markdown table row, the same row read from a PDF (no bars), and a label
    line such as "Name: value". A name wrapped over two lines counts.
    """
    body = r"\s+".join(re.escape(w) for w in name.split())
    before = rf"^[ \t]*[|*\-]*[ \t]*(?:(?:{index_cell})[ \t]*\|?[ \t]*)?\**[ \t]*"
    after = r"(?!\w)\**[ \t]*[:.|]?[ \t|*]*(?=[^\s|*:.])"
    return re.compile(before + body + after, re.IGNORECASE | re.MULTILINE)


# --- the checks --------------------------------------------------------------------------------
class _Run:
    """One pass of the gate over one document: the faults found so far, in the order made."""

    def __init__(self, sections, gate: Gate):
        self.sections = list(sections)
        self.gate = gate
        self.faults: list = []

    def add(self, rule: str, section, quote: str, needed: str) -> None:
        code = self.gate.checks[rule]["code"]
        self.faults.append(Fault(code, section, quote, needed, rule))

    def section(self, section_id: str):
        return next((s for s in self.sections if s.id == section_id), None)

    def measures(self) -> list:
        """The sections that define a calculation, in document order.

        A lettered measure always is one. Any other section with no sub-section is one when its
        text holds a formula mark or a phrase that defines a calculation (both from the gate file).
        A section that holds sub-sections is not one itself: its sub-sections are looked at.
        """
        ids = [s.id for s in self.sections if s.id]
        definitions = _patterns(self.gate.definition_phrases)
        out = []
        for s in self.sections:
            if not s.id:
                continue
            if LETTERED.search(s.id):
                out.append(s)
            elif any(other.startswith(s.id + ".") for other in ids):
                continue
            elif any(mark in s.text for mark in self.gate.formula_markers) or _any(
                definitions, s.text
            ):
                out.append(s)
        return out


def _gt1_settings(run: _Run) -> None:
    check = run.gate.checks["GT-1"]
    found = {}  # setting name -> index of the first section that defines it, or None
    for name in check["required_settings"]:
        names = [name] + list(check["aliases"].get(name, []))  # its own name or an accepted one
        lines = [_setting_line(n, check["row_index_cell"]) for n in names]
        found[name] = next(
            (i for i, s in enumerate(run.sections) if any(p.search(s.text) for p in lines)), None
        )
    tally: dict = {}
    for index in found.values():
        if index is not None:
            tally[index] = tally.get(index, 0) + 1
    # The fault is put on the section that holds most of the settings (the first one on a tie),
    # which is the settings table. If no setting is found anywhere it is on the whole document.
    home = min(tally, key=lambda i: (-tally[i], i)) if tally else None
    section = run.sections[home] if home is not None else None
    where = section.id if section else None
    quote = _first_line(section.text, run.gate.quote_max) if section else ""
    for name, index in found.items():
        if index is None:
            run.add("GT-1", where, quote, check["needed"].format(name=name))


def _gt2_inputs(run: _Run) -> None:
    markers = _patterns(run.gate.checks["GT-2"]["input_markers"])
    for measure in run.measures():
        if not _any(markers, measure.text):
            quote = _first_sentence(measure.text, run.gate.quote_max)
            run.add("GT-2", measure.id, quote, f"Say what measure {measure.id} is worked out from.")


def _gt3_sources(run: _Run) -> None:
    values = _patterns(run.gate.checks["GT-3"]["value_words"])
    sources = _patterns(run.gate.source_phrases)
    for measure in run.measures():
        hit = _first_hit(values, measure.text)
        if hit and not _any(sources, measure.text):
            start, end = next(
                (a, b) for a, b in _sentences(measure.text) if a <= hit[1].start() < b
            )
            quote = _clip(measure.text, start, end, run.gate.quote_max, hit[1].end())
            needed = (
                f"Say where the values in measure {measure.id} come from, "
                "or that the template supplies them."
            )
            run.add("GT-3", measure.id, quote, needed)


def _vague(run: _Run, rule: str, key: str, ask: str, pointers: re.Pattern | None) -> None:
    """A word from the list, in a sentence with no number and no source phrase.

    With `pointers`, a number that only points at another section does not count as a number.
    """
    words = run.gate.checks[rule][key]
    vague, sources = _patterns(words), _patterns(run.gate.source_phrases)
    for section in run.sections:
        if not section.id:
            continue  # the title block before the first section
        for start, end in _sentences(section.text):
            sentence = section.text[start:end]
            hit = _first_hit(vague, sentence)
            if hit is None:
                continue
            counted = pointers.sub(" ", sentence) if pointers else sentence
            if NUMBER.search(counted) or _any(sources, sentence):
                continue
            quote = _clip(section.text, start, end, run.gate.quote_max, start + hit[1].end())
            run.add(rule, section.id, quote, ask.format(word=words[hit[0]]))


def _gt4_vague(run: _Run) -> None:
    ask = 'Give the value for "{word}", or say where it comes from.'
    _vague(run, "GT-4", "vague_words", ask, None)


def _gt10_scope(run: _Run) -> None:
    ask = 'Say what "{word}" covers here: give a value, a condition, or where it is set.'
    pointers = _reference_pattern(run.gate.checks["GT-10"]["reference_words"])
    _vague(run, "GT-10", "vague_scope_words", ask, pointers)


def _gt5_examples(run: _Run) -> None:
    check = run.gate.checks["GT-5"]
    markers = _patterns(check["example_markers"])
    named = _patterns(check["document_example_phrases"])
    document_has_one = any(_any(named, s.text) for s in run.sections)
    for measure in run.measures():
        if not document_has_one and not _any(markers, measure.text):
            quote = _first_sentence(measure.text, run.gate.quote_max)
            run.add("GT-5", measure.id, quote, check["needed"].format(id=measure.id))


def _gt9_procedures(run: _Run) -> None:
    check = run.gate.checks["GT-9"]
    triggers, markers = _patterns(check["trigger_phrases"]), _patterns(check["procedure_markers"])
    for section in run.sections:
        if not section.id:
            continue
        for start, end in _sentences(section.text):
            if _first_hit(triggers, section.text[start:end]) is None:
                continue
            others = section.text[:start] + "\n" + section.text[end:]
            steps = len(NUMBERED_STEP.findall(others))
            if _any(markers, others) or steps >= check["min_numbered_steps"]:
                continue
            quote = _clip(section.text, start, end, run.gate.quote_max)
            needed = (
                "This sentence says a check or a method is applied, but the section gives no "
                "steps or rule for it. Give them, or say where they are."
            )
            run.add("GT-9", section.id, quote, needed)


def _operator_pattern(operators: list) -> re.Pattern:
    parts = []
    for op in sorted(operators, key=len, reverse=True):
        body = r"\s+".join(re.escape(w) for w in op.split())
        parts.append(rf"(?<!\w){body}(?!\w)" if op[0].isalnum() else body)
    number = r"\s*(\d+(?:\.\d+)?)\s*(%?)(?![\w/])"
    return re.compile("(?:" + "|".join(parts) + ")" + number, re.IGNORECASE)


def _gt6_contradictions(run: _Run) -> None:
    check = run.gate.checks["GT-6"]
    pattern = _operator_pattern(check["operators"])
    ignore = {w.lower() for w in check["ignore_leading_words"]}
    seen: dict = {}
    for section in run.sections:
        if not section.id:
            continue
        for start, end in _sentences(section.text):
            sentence = section.text[start:end]
            for found in pattern.finditer(sentence):
                words = re.findall(r"[A-Za-z][A-Za-z\-]*", sentence[: found.start()])
                words = [w.lower() for w in words[-check["max_name_words"] :]]
                while words and words[0] in ignore:
                    words.pop(0)
                if len(words) < check["min_name_words"]:
                    continue
                value = found.group(1) + found.group(2)
                quote = _clip(section.text, start, end, run.gate.quote_max)
                seen.setdefault(" ".join(words), []).append((section.id, value, quote))
    for name, entries in seen.items():
        first, said = entries[0], set()
        for section_id, value, quote in entries[1:]:
            if section_id != first[0] and value != first[1] and section_id not in said:
                said.add(section_id)
                needed = (
                    f"Section {section_id} gives {name} as {value}, but section {first[0]} "
                    f"gives {first[1]}. State one value."
                )
                run.add("GT-6", section_id, quote, needed)


def _gt7_template_refs(run: _Run, refs: dict) -> None:
    ids = {s.id for s in run.sections if s.id}
    for rule in sorted(refs):
        ref = str(refs[rule])
        if ref not in ids:
            needed = (
                f"Section {ref} is not in the document, "
                f"and rule {rule} of the school's template rests on it."
            )
            run.add("GT-7", ref, "", needed)


def check(sections, refs: dict | None = None, gate: Gate | None = None) -> list:
    """The faults of a document, in section order. An empty list means the gate passes it.

    `sections`: the document's sections in order. `refs`: {template rule id: section it rests on}.
    Without numbering (GT-8) no section can be named, so that is the only fault and no other check
    is run. Faults in one section come in the order of the checks. Faults that name a section the
    document does not have come last.
    """
    gate = gate or load_gate()
    run = _Run(sections, gate)
    if not any(s.id for s in run.sections):
        quote = _first_sentence(run.sections[0].text, gate.quote_max) if run.sections else ""
        needed = "Number the sections of the document so that each one can be named."
        run.add("GT-8", None, quote, needed)
        return run.faults
    _gt1_settings(run)
    _gt2_inputs(run)
    _gt3_sources(run)
    _gt4_vague(run)
    _gt5_examples(run)
    _gt6_contradictions(run)
    _gt9_procedures(run)
    _gt10_scope(run)
    _gt7_template_refs(run, refs or {})
    place: dict = {}
    for index, s in enumerate(run.sections):
        if s.id:
            place.setdefault(s.id, index)
    end = len(run.sections)
    return sorted(run.faults, key=lambda f: place.get(f.section, end))


def measure_ids(sections, gate: Gate | None = None) -> list:
    """The ids of the sections the gate treats as measures, in document order."""
    return [s.id for s in _Run(sections, gate or load_gate()).measures()]


def describe(fault: Fault) -> str:
    """The fault as the plain text kept with the document: what is needed, the rule, the quote."""
    lines = [f"{fault.needed} ({fault.rule})"]
    if fault.quote:
        lines.append(f'Quote: "{fault.quote}"')
    return "\n".join(lines)
