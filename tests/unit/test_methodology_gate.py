"""The methodology clarity gate (T-051): the rules, run on small inline documents.

The gate is pure: sections and the template's `method_ref` list go in, a list of faults comes out.
Nothing here needs Postgres. The documents are written inline in Markdown and read with the real
methodology reader, so the sections are exactly what the worker would hand the gate. The rules, the
word lists and the section names are read from `templates/gate-v1.yaml`; a test that needs a
different list writes its own gate file.

Written before the gate (tests first). They fail until `mdeis.ingest.methodology_gate` and
`templates/gate-v1.yaml` exist, and pass once they do.

Follow-up of 2026-11-16 (reader and gate): the gate no longer depends on how a document numbers its
sections. GT-1 finds a setting by its name anywhere, GT-2, GT-3 and GT-5 find a measure by what its
text says, and GT-9 and GT-10 are new. The tests that pinned section 3.1 and the sections 5.3 and
6.3 were rewritten for that, by the owner's decision. The Aurora document is in
test_methodology_gate_aurora.py.
"""

import ast
import json
from pathlib import Path

import pytest
import yaml

from mdeis.ingest import methodology
from mdeis.ingest import methodology_gate as gate

ROOT = Path(__file__).resolve().parents[2]
GATE_FILE = ROOT / "templates" / "gate-v1.yaml"
FIXTURES = ROOT / "data" / "synthetic" / "methodology"
GATE = gate.load_gate()
SETTINGS = GATE.checks["GT-1"]["required_settings"]
BANNED = ("weak", "remedial", "gifted", "lazy", "rank", "label", "pathway", "career")

# --------------------------------------------------------------------------- inline documents
GOOD_5 = """#### A. Share meeting the standard

This is worked out from the learners with valid results.
Where N is the number of learners with valid results and n is the number who meet the
standard, the share is n divided by N.
The standard is approved by the school in its template.

For example, if 12 of 40 learners meet the standard, the share is 30 percent.
"""

GOOD_6 = """#### A. Change in score

The change is worked out using the first and the last score in the window.
The window comes from the school's template.

For example, scores of 10 and 14 give a change of 4 points.
"""


def rows(settings=SETTINGS):
    return "\n".join(f"| {name} | Says what it is for. |" for name in settings)


def doc(settings=SETTINGS, section_2="MDEIS describes the records.", m5=GOOD_5, m6=GOOD_6,
        table=None):
    """A methodology in the document's own numbering that clears every check."""
    table = rows(settings) if table is None else table
    return f"""# A test methodology

## 1. Purpose

This document says how records are read.

## 2. Principles

{section_2}

## 3. Evaluation settings

### 3.1 Required settings

| Setting | Purpose |
|---|---|
{table}

## 4. Process

### Step 1: Read the request

The request is read first.

## 5. Group analysis

### 5.3 Main measures

MDEIS may calculate the following.

{m5}
## 6. Change over time

### 6.2 Possible evidence

Records are read as they are.

### 6.3 Main measures

{m6}
### 6.4 Reading the trend

The configuration defines the number of results needed.
"""


def sections_of(markdown):
    parsed = methodology.parse_markdown(markdown)
    return list(parsed.sections)


def faults_of(markdown, refs=None, which=None):
    found = gate.check(sections_of(markdown), refs or {}, which or GATE)
    return found


def of_rule(found, rule):
    return [f for f in found if f.rule == rule]


def text_of(sections, section_id):
    return next(s.text for s in sections if s.id == section_id)


# --------------------------------------------------------------------------- the gate file
def test_the_gate_file_has_every_check_once_with_its_id_code_and_plain_text():
    data = yaml.safe_load(GATE_FILE.read_text(encoding="utf-8"))
    ids = [c["id"] for c in data["checks"]]
    assert ids == [f"GT-{n}" for n in range(1, 11)]
    for check in data["checks"]:
        assert check["code"] and check["text"].strip(), check["id"]
    assert len({c["code"] for c in data["checks"]}) == 10


def test_the_codes_are_the_ones_the_design_note_names():
    codes = {c: GATE.checks[c]["code"] for c in GATE.checks}
    assert codes == {
        "GT-1": "required_setting_missing",
        "GT-2": "measure_no_inputs",
        "GT-3": "measure_no_source_for_values",
        "GT-4": "vague_term",
        "GT-5": "no_example",
        "GT-6": "contradiction",
        "GT-7": "template_ref_broken",
        "GT-8": "no_numbering",
        "GT-9": "procedure_not_defined",
        "GT-10": "vague_scope",
    }


def test_every_value_in_the_gate_file_is_marked_as_a_proposal():
    keys = ("quote_max_chars", "formula_markers", "definition_phrases", "source_phrases",
            "required_settings", "row_index_cell", "input_markers", "value_words", "vague_words",
            "example_markers", "document_example_phrases", "max_name_words", "min_name_words",
            "operators", "ignore_leading_words", "trigger_phrases", "procedure_markers",
            "min_numbered_steps", "vague_scope_words", "reference_words", "aliases")
    lines = GATE_FILE.read_text(encoding="utf-8").splitlines()
    found = {}
    for line in lines:
        stripped = line.strip().lstrip("- ")
        for key in keys:
            if stripped.startswith(key + ":"):
                found[key] = line
    assert set(found) == set(keys), set(keys) - set(found)
    for key, line in found.items():
        assert "# PROPOSED: owner to confirm" in line, key


def test_the_settings_list_is_the_table_of_section_3_1_of_the_real_v2_0():
    real = (FIXTURES / "methodology-v2.0.md").read_text(encoding="utf-8")
    table = real.split("### 3.1 Required settings")[1].split("### 3.2")[0]
    names = [line.split("|")[1].strip() for line in table.splitlines()
             if line.startswith("|") and "---" not in line and "Setting" not in line]
    assert SETTINGS == names and len(names) == 11


def test_no_section_number_is_in_the_gate_file_as_a_rule():
    """The gate must not depend on where a document puts its settings or its measures."""
    data = yaml.safe_load(GATE_FILE.read_text(encoding="utf-8"))
    assert "settings_section" not in data["checks"][0]
    assert "measure_sections" not in data
    assert set(data["measure_detection"]) == {"formula_markers", "definition_phrases"}
    assert GATE.formula_markers and GATE.definition_phrases


def test_every_alias_is_marked_as_a_proposal_and_belongs_to_a_required_setting():
    data = yaml.safe_load(GATE_FILE.read_text(encoding="utf-8"))
    aliases = data["checks"][0]["aliases"]
    assert set(aliases) <= set(SETTINGS)
    lines = GATE_FILE.read_text(encoding="utf-8").splitlines()
    for alternatives in aliases.values():
        assert alternatives and all(isinstance(a, str) and a.strip() for a in alternatives)
        for alias in alternatives:
            hits = [line for line in lines if line.strip().startswith(f"- {alias}")]
            assert len(hits) == 1, alias
            assert "# PROPOSED: owner to confirm" in hits[0], alias


def test_an_empty_alias_mapping_is_allowed_but_a_missing_one_is_refused(tmp_path):
    data = yaml.safe_load(GATE_FILE.read_text(encoding="utf-8"))
    data["checks"][0]["aliases"] = {}
    path = tmp_path / "gate.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert gate.load_gate(path).checks["GT-1"]["aliases"] == {}
    del data["checks"][0]["aliases"]
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(gate.GateFileError):
        gate.load_gate(path)


def test_only_the_four_aliases_the_owner_approved_are_in_the_file():
    aliases = GATE.checks["GT-1"]["aliases"]
    assert aliases == {
        "Institution and educational context": ["Institution and context"],
        "Evaluation type": ["Analysis design"],
        "Subject or learning area": ["Subjects and learning areas"],
        "Reporting preferences": ["Reporting, roles and approvals"],
    }


def test_the_fault_messages_of_gt1_and_gt5_come_from_the_file_and_hold_no_banned_word():
    one = GATE.checks["GT-1"]["needed"]
    five = GATE.checks["GT-5"]["needed"]
    assert "{name}" in one and "{id}" in five
    for text in (one, five):
        assert not any(word in text.lower() for word in BANNED), text
        assert chr(0x2014) not in text


def test_the_new_word_lists_hold_what_the_owner_asked_for():
    scope = GATE.checks["GT-10"]["vague_scope_words"]
    assert {"important", "where feasible", "where lawful"} <= set(scope)
    assert len(scope) <= 3 + 3, "the three asked for and at most three more"
    assert set(GATE.checks["GT-4"]["vague_words"]) == {
        "suitable", "appropriate", "as needed", "reasonable"}


def broken_gate(tmp_path, change):
    data = yaml.safe_load(GATE_FILE.read_text(encoding="utf-8"))
    change(data)
    path = tmp_path / "gate.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["checks"].pop(2),
        lambda d: d["checks"][0].update(required_settings=[]),
        lambda d: d["checks"][3].update(vague_words="suitable"),
        lambda d: d.update(quote_max_chars=0),
        lambda d: d["measure_detection"].update(formula_markers=[]),
        lambda d: d.pop("measure_detection"),
        lambda d: d["checks"][1].pop("code"),
        lambda d: d.update(checks="none"),
        lambda d: d["checks"][0].update(row_index_cell="(unclosed"),
        lambda d: d["checks"][4].update(document_example_phrases=[]),
        lambda d: d["checks"][8].update(trigger_phrases=[]),
        lambda d: d["checks"][8].update(min_numbered_steps=0),
        lambda d: d["checks"][9].pop("reference_words"),
        lambda d: d["checks"][0].update(aliases={"Not a required setting": ["Something"]}),
        lambda d: d["checks"][0].update(aliases={"Evaluation type": []}),
        lambda d: d["checks"][0].update(aliases=["Analysis design"]),
        lambda d: d["checks"][0].pop("aliases"),
        lambda d: d["checks"][0].pop("needed"),
        lambda d: d["checks"][4].pop("needed"),
    ],
)
def test_a_gate_file_that_is_incomplete_is_refused_with_a_plain_error(tmp_path, change):
    with pytest.raises(gate.GateFileError):
        gate.load_gate(broken_gate(tmp_path, change))


def test_a_gate_file_that_is_missing_or_not_yaml_is_refused(tmp_path):
    with pytest.raises(gate.GateFileError):
        gate.load_gate(tmp_path / "nothing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("checks: [unclosed", encoding="utf-8")
    with pytest.raises(gate.GateFileError):
        gate.load_gate(bad)


def test_the_gate_file_can_be_named_by_an_environment_variable(tmp_path, monkeypatch):
    path = broken_gate(tmp_path, lambda d: d.update(gate_version="gate-test"))
    monkeypatch.setenv(gate.GATE_ENV, str(path))
    assert gate.load_gate().version == "gate-test"
    monkeypatch.setenv(gate.GATE_ENV, str(tmp_path / "absent.yaml"))
    with pytest.raises(gate.GateFileError):
        gate.load_gate()


# --------------------------------------------------------------------------- a clear document
def test_a_clear_document_has_no_fault():
    assert faults_of(doc(), {"TR-1": "6.4", "TR-2": "6.3"}) == []


def test_a_clear_document_gives_an_empty_list_not_none():
    assert gate.check(sections_of(doc()), {}, GATE) == []


# --------------------------------------------------------------------------- GT-1
def test_gt1_a_setting_missing_from_the_settings_section_is_a_fault():
    left = [s for s in SETTINGS if s != "Comparison rules"]
    found = of_rule(faults_of(doc(settings=left)), "GT-1")
    assert [(f.code, f.section) for f in found] == [("required_setting_missing", "3.1")]
    assert "Comparison rules" in found[0].needed
    assert found[0].quote and found[0].quote in text_of(sections_of(doc(settings=left)), "3.1")


def test_gt1_one_fault_for_each_missing_setting_in_the_files_order():
    left = SETTINGS[2:]
    found = of_rule(faults_of(doc(settings=left)), "GT-1")
    assert len(found) == 2
    assert SETTINGS[0] in found[0].needed and SETTINGS[1] in found[1].needed


def test_gt1_a_setting_with_no_purpose_counts_as_not_defined():
    table = rows(SETTINGS[1:]) + f"\n| {SETTINGS[0]} | |"
    found = of_rule(faults_of(doc(table=table)), "GT-1")
    assert len(found) == 1 and SETTINGS[0] in found[0].needed


def test_gt1_the_name_is_matched_ignoring_capitals_and_spacing():
    table = rows(SETTINGS).replace("Evaluation type", "evaluation   TYPE")
    assert of_rule(faults_of(doc(table=table)), "GT-1") == []


def test_gt1_the_settings_can_be_in_any_section_with_any_number():
    markdown = doc().replace("### 3.1 Required settings", "### 3.9 Something else")
    assert of_rule(faults_of(markdown), "GT-1") == []
    moved = doc(table="").replace("## 4. Process", f"## 4. Process\n\n{rows_with_header()}\n")
    assert of_rule(faults_of(moved), "GT-1") == []


def rows_with_header(settings=SETTINGS):
    return "| Setting | Purpose |\n|---|---|\n" + rows(settings)


def test_gt1_no_table_and_no_label_anywhere_is_one_fault_per_setting_on_the_whole_document():
    markdown = doc(table="")
    found = of_rule(faults_of(markdown), "GT-1")
    assert [f.section for f in found] == [None] * len(SETTINGS)
    assert [f.quote for f in found] == [""] * len(SETTINGS)
    assert all(name in f.needed for name, f in zip(SETTINGS, found, strict=True))


def test_gt1_a_table_with_a_row_number_column_is_read_by_its_setting_column():
    table = "\n".join(f"| {n} | {name} | A value. |" for n, name in enumerate(SETTINGS, 1))
    assert of_rule(faults_of(doc(table=table)), "GT-1") == []
    hash_header = "| # | Setting | Value |\n|---|---|---|\n" + table
    assert of_rule(faults_of(doc(table=hash_header)), "GT-1") == []


def test_gt1_a_row_read_from_a_pdf_has_no_bars_and_still_counts():
    table = "\n".join(f"{n} {name} A value." for n, name in enumerate(SETTINGS, 1))
    assert of_rule(faults_of(doc(table=table)), "GT-1") == []
    without_value = "\n".join(f"{n} {name}" for n, name in enumerate(SETTINGS, 1))
    assert len(of_rule(faults_of(doc(table=without_value)), "GT-1")) == len(SETTINGS)


@pytest.mark.parametrize(
    "label",
    ["**{name}:** longitudinal", "**{name}.** longitudinal", "{name}: longitudinal",
     "- {name}: longitudinal", "**{name}** longitudinal"],
)
def test_gt1_a_bold_or_label_line_anywhere_defines_the_setting(label):
    left = [s for s in SETTINGS if s != "Evaluation type"]
    markdown = doc(settings=left).replace(
        "MDEIS describes the records.", label.format(name="Evaluation type"))
    assert of_rule(faults_of(markdown), "GT-1") == []


def test_gt1_a_label_with_nothing_after_it_does_not_define_the_setting():
    left = [s for s in SETTINGS if s != "Evaluation type"]
    markdown = doc(settings=left).replace("MDEIS describes the records.", "**Evaluation type:**")
    assert len(of_rule(faults_of(markdown), "GT-1")) == 1


def test_gt1_the_name_must_start_the_line_a_mention_inside_a_sentence_is_not_a_definition():
    left = [s for s in SETTINGS if s != "Evaluation type"]
    markdown = doc(settings=left).replace(
        "MDEIS describes the records.", "The evaluation type is chosen later.")
    assert len(of_rule(faults_of(markdown), "GT-1")) == 1


def test_gt1_a_name_that_wraps_onto_two_lines_still_counts():
    left = [s for s in SETTINGS if s != "Institution and educational context"]
    markdown = doc(settings=left).replace(
        "MDEIS describes the records.", "Institution and\neducational context A school.")
    assert of_rule(faults_of(markdown), "GT-1") == []


def test_gt1_a_name_that_is_only_part_of_a_longer_name_does_not_count():
    left = [s for s in SETTINGS if s != "Evaluation type"]
    markdown = doc(settings=left).replace(
        "MDEIS describes the records.", "Evaluation types: several of them.")
    assert len(of_rule(faults_of(markdown), "GT-1")) == 1


def test_gt1_a_name_that_is_not_listed_is_a_fault_and_is_not_matched_by_meaning():
    table = rows([s for s in SETTINGS if s != "Evaluation type"]) + "\n| Evaluation kind | A. |"
    found = of_rule(faults_of(doc(table=table)), "GT-1")
    assert len(found) == 1 and "Evaluation type" in found[0].needed


ALIASES = [(name, alias) for name, alts in GATE.checks["GT-1"]["aliases"].items() for alias in alts]


@pytest.mark.parametrize("name,alias", ALIASES)
def test_gt1_each_listed_alias_counts_as_the_setting(name, alias):
    table = rows([s for s in SETTINGS if s != name]) + f"\n| {alias} | Says what it is for. |"
    assert of_rule(faults_of(doc(table=table)), "GT-1") == []
    as_label = doc(settings=[s for s in SETTINGS if s != name]).replace(
        "MDEIS describes the records.", f"**{alias}:** a value")
    assert of_rule(faults_of(as_label), "GT-1") == []


@pytest.mark.parametrize("name,alias", ALIASES)
def test_gt1_an_alias_with_nothing_after_it_does_not_count(name, alias):
    table = rows([s for s in SETTINGS if s != name]) + f"\n| {alias} | |"
    assert len(of_rule(faults_of(doc(table=table)), "GT-1")) == 1


@pytest.mark.parametrize("name,alias", ALIASES)
def test_gt1_an_alias_defines_only_its_own_setting_not_another(name, alias):
    others = [s for s in SETTINGS if s != name]
    table = rows(others[1:]) + f"\n| {alias} | A value. |"
    found = of_rule(faults_of(doc(table=table)), "GT-1")
    assert len(found) == 1 and others[0] in found[0].needed


def test_gt1_the_setting_itself_still_counts_when_it_has_an_alias():
    assert of_rule(faults_of(doc()), "GT-1") == []


# --------------------------------------------------------------------------- GT-2
NO_INPUTS = """#### B. Overall shape

This shows how the results look across the group.

For example, a group with ten results shows ten points.
"""


def test_gt2_a_measure_that_names_no_inputs_is_a_fault_on_that_measure():
    found = of_rule(faults_of(doc(m5=GOOD_5 + "\n" + NO_INPUTS)), "GT-2")
    assert [(f.code, f.section) for f in found] == [("measure_no_inputs", "5.3.B")]
    assert found[0].quote in text_of(sections_of(doc(m5=GOOD_5 + "\n" + NO_INPUTS)), "5.3.B")


def test_gt2_each_input_marker_from_the_file_is_enough():
    for marker in GATE.checks["GT-2"]["input_markers"]:
        body = f"#### B. Overall shape\n\nThis is {marker} the records. For example, one record.\n"
        assert of_rule(faults_of(doc(m6=body)), "GT-2") == [], marker


def test_gt2_a_section_that_works_something_out_is_a_measure_with_no_letters_and_any_number():
    markdown = doc(m6="The change = the last score minus the first.\n")
    found = of_rule(faults_of(markdown), "GT-2")
    assert [f.section for f in found] == ["6.3"]
    assert of_rule(faults_of(doc(section_2="The change = the last score minus the first.")),
                   "GT-2")[0].section == "2"


def test_gt2_a_section_that_only_describes_is_not_a_measure():
    markdown = doc(section_2="This shows the shape. For example, a shape.")
    assert of_rule(faults_of(markdown), "GT-2") == []
    assert of_rule(faults_of(doc(m6="This shows the change. For example, 10 to 14.\n")),
                   "GT-2") == []


ELSEWHERE = "\n## 7. Elsewhere\n\n{}\n"
COMPETENCES = """The school looks at these competences (a list): learning to learn, thinking and
problem solving, and taking care of oneself.

| Level | Description |
|---|---|
| Emerging | Shows it only with strong support |
| Secure | Shows it independently |
"""


def test_gt2_a_list_of_competences_with_no_calculation_in_it_is_not_a_measure():
    markdown = doc(m6=COMPETENCES)
    assert [f for f in faults_of(markdown) if f.rule in ("GT-2", "GT-3", "GT-5")] == []
    assert "6.3" not in gate.measure_ids(sections_of(markdown), GATE)


def test_the_measure_sections_are_found_by_what_they_say_not_by_their_number():
    plain = doc(m5="", m6="") + ELSEWHERE.format("The gap is the difference between scores.")
    assert gate.measure_ids(sections_of(plain), GATE) == ["7"]


def test_a_lettered_measure_is_always_a_measure_even_with_no_formula_in_it():
    ids = gate.measure_ids(sections_of(doc()), GATE)
    assert ids == ["5.3.A", "6.3.A"]


@pytest.mark.parametrize("mark", ["=", "÷", "×", "Δ"])
def test_each_formula_mark_from_the_file_makes_a_leaf_section_a_measure(mark):
    plain = doc(m5="", m6="") + f"\n## 7. Elsewhere\n\nThe gap {mark} two scores.\n"
    assert gate.measure_ids(sections_of(plain), GATE) == ["7"]


@pytest.mark.parametrize(
    "phrase",
    ["percentage of", "difference between", "divided by", "calculated as", "worked out as",
     "defined as"],
)
def test_each_definition_phrase_from_the_file_makes_a_leaf_section_a_measure(phrase):
    plain = doc(m5="", m6="") + f"\n## 7. Elsewhere\n\nThe gap is the {phrase} two scores.\n"
    assert gate.measure_ids(sections_of(plain), GATE) == ["7"]


def test_a_section_with_sub_sections_is_not_a_measure_its_sub_sections_are_looked_at():
    markdown = doc(m5="", m6="").replace(
        "### 6.2 Possible evidence\n\nRecords are read as they are.",
        "The gap = a - b.\n\n### 6.2 Possible evidence\n\nThe gap = a - b.")
    ids = gate.measure_ids(sections_of(markdown), GATE)
    assert "6" not in ids and "6.2" in ids


def test_a_percentage_mentioned_without_a_definition_is_not_a_measure():
    plain = doc(m5="", m6="") + "\n## 7. Elsewhere\n\nA grade is not a percentage.\n"
    assert gate.measure_ids(sections_of(plain), GATE) == []


# --------------------------------------------------------------------------- GT-3
def test_gt3_a_measure_that_needs_a_threshold_and_says_nowhere_it_comes_from_is_a_fault():
    body = ("#### B. Meeting the bar\n\nThis is worked out from the results. "
            "The threshold must be met. For example, 5 of 10 met it.\n")
    found = of_rule(faults_of(doc(m5=GOOD_5 + "\n" + body)), "GT-3")
    assert [(f.code, f.section) for f in found] == [("measure_no_source_for_values", "5.3.B")]
    assert "threshold" in found[0].quote


@pytest.mark.parametrize("source", ["template", "configuration", "approved", "defined by"])
def test_gt3_a_source_phrase_anywhere_in_the_measure_clears_it(source):
    body = ("#### B. Meeting the bar\n\nThis is worked out from the results. "
            f"The threshold must be met. The {source} school says what it is. "
            "For example, 5 of 10 met it.\n")
    assert of_rule(faults_of(doc(m5=GOOD_5 + "\n" + body)), "GT-3") == []


@pytest.mark.parametrize("word", ["threshold", "weight", "range"])
def test_gt3_each_value_word_from_the_file_is_noticed(word):
    body = (f"#### B. Meeting the bar\n\nThis is worked out from the results. A {word} applies. "
            "For example, 5 of 10 met it.\n")
    assert len(of_rule(faults_of(doc(m5=GOOD_5 + "\n" + body)), "GT-3")) == 1


# --------------------------------------------------------------------------- GT-4
def test_gt4_a_vague_word_with_no_value_and_no_source_in_its_sentence_is_a_fault():
    found = of_rule(faults_of(doc(section_2="The method must be suitable for the group.")), "GT-4")
    assert [(f.code, f.section) for f in found] == [("vague_term", "2")]
    assert found[0].quote == "The method must be suitable for the group."
    assert "suitable" in found[0].needed


@pytest.mark.parametrize("word", ["suitable", "appropriate", "as needed", "reasonable"])
def test_gt4_each_listed_word_is_noticed_and_named(word):
    found = of_rule(faults_of(doc(section_2=f"The step is done {word} here.")), "GT-4")
    assert len(found) == 1 and word in found[0].needed


@pytest.mark.parametrize(
    "sentence",
    [
        "A suitable pass mark is 12 out of 20.",
        "A suitable value is supplied by the template.",
        "Where appropriate, the configuration says how.",
        "The approved value is suitable.",
    ],
)
def test_gt4_a_number_or_a_source_in_the_same_sentence_clears_it(sentence):
    assert of_rule(faults_of(doc(section_2=sentence)), "GT-4") == []


def test_gt4_a_value_in_the_next_sentence_does_not_clear_it():
    found = of_rule(faults_of(doc(section_2="Use a suitable bar. The bar is 12.")), "GT-4")
    assert len(found) == 1 and found[0].quote == "Use a suitable bar."


def test_gt4_other_forms_of_the_word_are_not_the_listed_word():
    markdown = doc(section_2="Results that are reasonably comparable.")
    assert of_rule(faults_of(markdown), "GT-4") == []


def test_gt4_the_sentence_after_a_line_break_is_still_one_sentence():
    text = "Choose a suitable bar for the group\nbased on the template."
    assert of_rule(faults_of(doc(section_2=text)), "GT-4") == []


def test_gt4_bullet_items_are_sentences_of_their_own():
    text = "Settings:\n\n- A suitable bar.\n- A bar of 12."
    found = of_rule(faults_of(doc(section_2=text)), "GT-4")
    assert [f.quote for f in found] == ["- A suitable bar."]


def test_gt4_the_word_is_matched_in_every_section_with_a_number():
    markdown = doc(section_2="A suitable bar.").replace(
        "This document says how records are read.", "Read them in a reasonable way.")
    assert [f.section for f in of_rule(faults_of(markdown), "GT-4")] == ["1", "2"]


def test_gt4_a_long_sentence_is_cut_but_the_quote_still_holds_the_word():
    sentence = "Start " + "word " * 80 + "then a suitable bar " + "word " * 30 + "end."
    found = of_rule(faults_of(doc(section_2=sentence)), "GT-4")
    assert len(found) == 1
    assert len(found[0].quote) <= GATE.quote_max and "suitable" in found[0].quote


# --------------------------------------------------------------------------- GT-5
def test_gt5_a_measure_without_a_worked_example_is_a_fault():
    body = ("#### B. Change in score\n\nThis is worked out from the first and last score, "
            "where both are valid.\n")
    found = of_rule(faults_of(doc(m6=GOOD_6 + "\n" + body)), "GT-5")
    assert [(f.code, f.section) for f in found] == [("no_example", "6.3.B")]


@pytest.mark.parametrize("marker", ["example", "for instance", "e.g.", "suppose"])
def test_gt5_each_example_marker_from_the_file_is_enough(marker):
    body = f"#### B. Change in score\n\nWhere both scores are valid. A case, {marker} 10 and 14.\n"
    assert of_rule(faults_of(doc(m6=GOOD_6 + "\n" + body)), "GT-5") == []


NO_EXAMPLE_B = ("#### B. Change in score\n\nThis is worked out from the first and last score, "
                "where both are valid.\n")


@pytest.mark.parametrize("phrase", ["worked example", "worked examples", "worked test case",
                                    "worked test cases"])
def test_gt5_a_worked_example_named_in_another_section_covers_the_measure(phrase):
    markdown = doc(m6=GOOD_6 + "\n" + NO_EXAMPLE_B).replace(
        "MDEIS describes the records.", f"The {phrase} sit in the appendix.")
    assert of_rule(faults_of(markdown), "GT-5") == []


def test_gt5_the_phrase_in_the_document_is_matched_whole_not_as_part_of_other_words():
    markdown = doc(m6=GOOD_6 + "\n" + NO_EXAMPLE_B).replace(
        "MDEIS describes the records.", "The worked samples sit in the appendix.")
    assert [f.section for f in of_rule(faults_of(markdown), "GT-5")] == ["6.3.B"]


def test_gt5_a_plain_example_word_in_another_section_does_not_cover_a_measure():
    markdown = doc(m6=GOOD_6 + "\n" + NO_EXAMPLE_B).replace(
        "MDEIS describes the records.", "For example, a school may choose its own words.")
    assert [f.section for f in of_rule(faults_of(markdown), "GT-5")] == ["6.3.B"]


# --------------------------------------------------------------------------- GT-6
def test_gt6_two_sections_that_give_different_values_for_one_named_setting_are_a_fault():
    markdown = doc(section_2="The pass mark is 12.").replace(
        "This document says how records are read.", "The pass mark is 10.")
    found = of_rule(faults_of(markdown), "GT-6")
    assert [(f.code, f.section) for f in found] == [("contradiction", "2")]
    assert "1" in found[0].needed and "pass mark" in found[0].needed
    assert found[0].quote == "The pass mark is 12."


def test_gt6_the_same_value_in_two_sections_is_not_a_contradiction():
    markdown = doc(section_2="The pass mark is 10.").replace(
        "This document says how records are read.", "The pass mark is 10.")
    assert of_rule(faults_of(markdown), "GT-6") == []


def test_gt6_two_values_inside_one_section_are_not_compared():
    assert of_rule(faults_of(doc(section_2="The pass mark is 10. The pass mark is 12.")),
                   "GT-6") == []


def test_gt6_the_leading_article_and_capitals_do_not_make_two_names():
    markdown = doc(section_2="pass mark = 12.").replace(
        "This document says how records are read.", "The Pass Mark is 10.")
    assert len(of_rule(faults_of(markdown), "GT-6")) == 1


def test_gt6_a_text_value_is_not_compared_only_numbers():
    markdown = doc(section_2="The report style is long.").replace(
        "This document says how records are read.", "The report style is short.")
    assert of_rule(faults_of(markdown), "GT-6") == []


# --------------------------------------------------------------------------- GT-7
def test_gt7_a_template_rule_that_rests_on_a_missing_section_is_a_fault():
    found = of_rule(faults_of(doc(), {"TR-9": "7.2"}), "GT-7")
    assert [(f.code, f.section, f.quote) for f in found] == [("template_ref_broken", "7.2", "")]
    assert "TR-9" in found[0].needed and "7.2" in found[0].needed


def test_gt7_one_fault_per_rule_in_rule_order_and_none_for_sections_that_exist():
    refs = {"TR-3": "9.9", "TR-1": "6.4", "TR-2": "8.8"}
    found = of_rule(faults_of(doc(), refs), "GT-7")
    assert [f.section for f in found] == ["8.8", "9.9"]


# --------------------------------------------------------------------------- GT-8
FREE_FORM = """# Our approach

We look at how each child is doing and we are flexible. It must be suitable and appropriate.

Each school sets its own bar. There is nothing numbered here.
"""


def test_gt8_a_document_without_numbering_is_one_fault_and_no_other_check_runs():
    found = faults_of(FREE_FORM, {"TR-1": "6.4"})
    assert [(f.code, f.rule, f.section) for f in found] == [("no_numbering", "GT-8", None)]
    assert found[0].quote and found[0].quote in sections_of(FREE_FORM)[0].text


def test_gt8_an_empty_document_has_no_numbering_and_a_fault_with_no_quote():
    found = gate.check([], {}, GATE)
    assert [(f.rule, f.section, f.quote) for f in found] == [("GT-8", None, "")]


# --------------------------------------------------------------------------- GT-9
APPLIED = "Complementary suppression is applied when a hidden value could be recovered."
CHECKED = "Overlapping filters and repeated queries are checked for reconstruction risk."


def test_gt9_a_sentence_that_says_a_method_is_applied_with_no_steps_or_rule_is_a_fault():
    found = of_rule(faults_of(doc(section_2=APPLIED)), "GT-9")
    assert [(f.code, f.section, f.quote) for f in found] == [
        ("procedure_not_defined", "2", APPLIED)]
    assert "steps or rule" in found[0].needed


def test_gt9_each_trigger_phrase_from_the_file_is_noticed():
    for phrase in GATE.checks["GT-9"]["trigger_phrases"]:
        found = of_rule(faults_of(doc(section_2=f"The cell {phrase} here.")), "GT-9")
        assert len(found) == 1, phrase


def test_gt9_two_trigger_sentences_with_no_procedure_are_two_faults_in_their_order():
    found = of_rule(faults_of(doc(section_2=APPLIED + " " + CHECKED)), "GT-9")
    assert [f.quote for f in found] == [APPLIED, CHECKED]


def test_gt9_one_fault_for_a_sentence_even_when_it_holds_two_trigger_phrases():
    both = "Complementary suppression is applied when a value could be recovered."
    assert len(of_rule(faults_of(doc(section_2=both)), "GT-9")) == 1


def test_gt9_every_procedure_marker_from_the_file_in_another_sentence_clears_it():
    for marker in GATE.checks["GT-9"]["procedure_markers"]:
        text = f"{APPLIED} The {marker} is written here."
        assert of_rule(faults_of(doc(section_2=text)), "GT-9") == [], marker


def test_gt9_numbered_steps_in_the_section_clear_it():
    steps = "\n\n1. Add up the cells.\n2. Hide the next smallest cell."
    assert of_rule(faults_of(doc(section_2=APPLIED + steps)), "GT-9") == []
    one = "\n\n1. Add up the cells."
    assert len(of_rule(faults_of(doc(section_2=APPLIED + one)), "GT-9")) == 1


def test_gt9_a_marker_in_the_trigger_sentence_itself_does_not_clear_it():
    text = "The method is applied when a value could be recovered."
    assert len(of_rule(faults_of(doc(section_2=text)), "GT-9")) == 1


def test_gt9_the_steps_must_be_in_the_same_section():
    markdown = doc(section_2=APPLIED).replace(
        "This document says how records are read.", "First do this. Then do that.")
    assert [f.section for f in of_rule(faults_of(markdown), "GT-9")] == ["2"]


def test_gt9_a_sentence_with_no_trigger_phrase_is_not_a_fault():
    assert of_rule(faults_of(doc(section_2="Cells are hidden when they are small.")),
                   "GT-9") == []


def test_gt9_the_title_block_is_not_checked():
    markdown = doc().replace("# A test methodology", f"# A test methodology\n\n{APPLIED}")
    assert of_rule(faults_of(markdown), "GT-9") == []


# --------------------------------------------------------------------------- GT-10
def test_gt10_each_word_from_the_file_is_noticed_and_named():
    for word in GATE.checks["GT-10"]["vague_scope_words"]:
        found = of_rule(faults_of(doc(section_2=f"The step is done {word} here.")), "GT-10")
        assert len(found) == 1 and word in found[0].needed, word
        assert found[0].code == "vague_scope" and found[0].section == "2"


def test_gt10_the_words_asked_for_by_the_owner_are_caught_in_a_sentence():
    for sentence in ("Send important findings to the teacher.",
                     "Show the old results where feasible.",
                     "The reviewer is recorded where lawful."):
        found = of_rule(faults_of(doc(section_2=sentence)), "GT-10")
        assert [f.quote for f in found] == [sentence]


@pytest.mark.parametrize(
    "sentence",
    ["Send important findings within 5 days.",
     "Send important findings as the template says.",
     "Show the results where feasible, as set by the school.",
     "Send important findings to the teacher, approved by the head."],
)
def test_gt10_a_number_or_a_source_in_the_same_sentence_clears_it(sentence):
    assert of_rule(faults_of(doc(section_2=sentence)), "GT-10") == []


@pytest.mark.parametrize(
    "sentence",
    ["Send important findings to the teacher (section 12).",
     "Send important findings to the teacher (see 6.4).",
     "Send important findings as sections 11 and 12 say.",
     "Send important findings as clause 4.B says."],
)
def test_gt10_a_number_that_only_points_at_another_section_is_not_a_value(sentence):
    assert len(of_rule(faults_of(doc(section_2=sentence)), "GT-10")) == 1


def test_gt10_a_real_number_next_to_a_pointer_still_clears_it():
    sentence = "Send important findings within 5 days (section 12)."
    assert of_rule(faults_of(doc(section_2=sentence)), "GT-10") == []


def test_gt10_a_value_in_the_next_sentence_does_not_clear_it():
    found = of_rule(faults_of(doc(section_2="Send important findings. The limit is 12.")), "GT-10")
    assert [f.quote for f in found] == ["Send important findings."]


def test_gt10_gt4_keeps_its_own_condition_a_pointer_number_still_clears_a_gt4_word():
    sentence = "Use a suitable bar (section 12)."
    assert of_rule(faults_of(doc(section_2=sentence)), "GT-4") == []
    assert of_rule(faults_of(doc(section_2="Use a suitable bar.")), "GT-4") != []


def test_gt10_the_vague_word_lists_of_gt4_and_gt10_do_not_overlap():
    four = set(GATE.checks["GT-4"]["vague_words"])
    ten = set(GATE.checks["GT-10"]["vague_scope_words"])
    assert not four & ten


def test_gt10_a_long_sentence_is_cut_but_the_quote_still_holds_the_word():
    sentence = "Start " + "word " * 80 + "then important " + "word " * 30 + "end."
    found = of_rule(faults_of(doc(section_2=sentence)), "GT-10")
    assert len(found) == 1
    assert len(found[0].quote) <= GATE.quote_max and "important" in found[0].quote


# --------------------------------------------------------------------------- order, quotes, repeats
def messy():
    return doc(
        settings=[s for s in SETTINGS if s != "Evaluation type"],
        section_2="The method must be suitable.",
        m5=GOOD_5 + "\n" + NO_INPUTS,
        m6="The change = the last score minus the first.\n",
    )


def test_faults_come_in_section_order_and_the_template_faults_come_last():
    sections = sections_of(messy())
    order = {s.id: i for i, s in enumerate(sections) if s.id}
    found = faults_of(messy(), {"TR-1": "6.4", "TR-9": "7.2"})
    named = [f.section for f in found]
    assert named[-1] == "7.2" and found[-1].rule == "GT-7"
    in_document = [order[s] for s in named[:-1]]
    assert in_document == sorted(in_document) and len(in_document) >= 4
    assert {"2", "3.1", "5.3.B", "6.3"} <= set(named)


def test_inside_one_section_the_faults_come_in_the_order_of_the_checks():
    body = "#### B. Meeting the bar\n\nThe threshold must be suitable.\n"
    found = [f.rule for f in faults_of(doc(m5=GOOD_5 + "\n" + body)) if f.section == "5.3.B"]
    assert found == sorted(found) and found == ["GT-2", "GT-3", "GT-4", "GT-5"]


def test_the_same_input_twice_gives_the_same_faults_in_the_same_order():
    refs = {"TR-9": "7.2"}
    first, second = faults_of(messy(), refs), faults_of(messy(), refs)
    assert first == second and len(first) > 4
    assert [f.to_dict() for f in first] == [f.to_dict() for f in second]


def test_the_order_of_the_template_references_given_does_not_change_the_answer():
    one = faults_of(doc(), {"TR-1": "7.1", "TR-2": "7.2"})
    two = faults_of(doc(), dict(reversed(list({"TR-1": "7.1", "TR-2": "7.2"}.items()))))
    assert one == two


def test_no_quote_is_longer_than_the_limit_or_differs_from_the_section_text():
    sections = sections_of(messy())
    found = gate.check(sections, {"TR-9": "7.2"}, GATE)
    for fault in found:
        assert len(fault.quote) <= GATE.quote_max
        if fault.quote:
            assert fault.quote in next(s.text for s in sections if s.id == fault.section), fault


def test_the_limit_on_a_quote_comes_from_the_file(tmp_path):
    short = gate.load_gate(broken_gate(tmp_path, lambda d: d.update(quote_max_chars=15)))
    sections = sections_of(doc(section_2="The method must be suitable for the whole group."))
    found = of_rule(gate.check(sections, {}, short), "GT-4")
    assert len(found) == 1 and len(found[0].quote) <= 15
    assert found[0].quote in text_of(sections, "2")


def test_the_word_lists_come_from_the_file_not_the_code(tmp_path):
    def change(data):
        data["checks"][3]["vague_words"] = ["splendid"]

    own = gate.load_gate(broken_gate(tmp_path, change))
    sections = sections_of(doc(section_2="A splendid bar. A suitable bar."))
    found = of_rule(gate.check(sections, {}, own), "GT-4")
    assert [f.quote for f in found] == ["A splendid bar."]


@pytest.mark.parametrize(
    "name",
    ["methodology-v2.0", "methodology-v2.1-small", "methodology-v2.1-full",
     "methodology-free-form"],
)
def test_on_the_committed_fixtures_every_quote_is_verbatim_short_and_every_fault_is_typed(name):
    sections = sections_of((FIXTURES / f"{name}.md").read_text(encoding="utf-8"))
    refs = {"TR-1": "6.4", "TR-2": "6.3", "TR-3": "4", "EX-1": "6.2"}
    found = gate.check(sections, refs, GATE)
    texts = {s.id: s.text for s in sections}
    for fault in found:
        assert fault.rule in GATE.checks and fault.code == GATE.checks[fault.rule]["code"]
        assert len(fault.quote) <= GATE.quote_max
        assert fault.quote == "" or fault.quote in texts[fault.section]
        assert fault.needed.strip()
        assert chr(0x2014) not in fault.needed
        assert not any(word in fault.needed.lower() for word in BANNED), fault.needed


def test_a_fault_turns_into_a_plain_dict_and_a_detail_line_with_the_rule_and_the_quote():
    found = faults_of(doc(section_2="The method must be suitable."))
    fault = of_rule(found, "GT-4")[0]
    assert set(fault.to_dict()) == {"code", "section", "quote", "needed", "rule"}
    detail = gate.describe(fault)
    assert fault.needed in detail and "GT-4" in detail and fault.quote in detail
    assert json.dumps(fault.to_dict())


def test_print_what_the_gate_finds_in_the_real_v2_0(capsys):
    """Not a verdict: it prints the faults so the owner can read them (run with -s).

    The expected list is not written here. The owner decides whether a fault is a fault in the
    document or in the bar, so no test pins the result for v2.0.
    """
    sections = sections_of((FIXTURES / "methodology-v2.0.md").read_text(encoding="utf-8"))
    template = yaml.safe_load((ROOT / "templates" / "v1.yaml").read_text(encoding="utf-8"))
    refs = {
        item["id"]: str(item["method_ref"])
        for value in template.values()
        if isinstance(value, list)
        for item in value
        if isinstance(item, dict) and "method_ref" in item
    }
    found = gate.check(sections, refs, GATE)
    with capsys.disabled():
        print(f"\nGATE ON v2.0: {len(found)} fault(s)")
        for fault in found:
            print(f"  {fault.rule} {fault.code} section={fault.section}: {fault.quote[:70]!r}")
    assert all(f.rule in GATE.checks for f in found)


# --------------------------------------------------------------------------- what the gate imports
ALLOWED = {"__future__", "json", "os", "re", "sys", "dataclasses", "pathlib", "typing", "yaml"}


def test_the_gate_imports_no_model_client_and_makes_no_network_call():
    source = (ROOT / "src" / "mdeis" / "ingest" / "methodology_gate.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    seen = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            seen |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "the gate imports nothing from the rest of the package"
            seen.add((node.module or "").split(".")[0])
    assert seen <= ALLOWED, seen - ALLOWED
    for forbidden in ("socket", "http", "urllib", "httpx", "requests", "openai", "anthropic",
                      "psycopg", "subprocess"):
        assert forbidden not in seen


def test_the_gate_holds_no_school_value_and_no_clause_text_in_code():
    source = (ROOT / "src" / "mdeis" / "ingest" / "methodology_gate.py").read_text(encoding="utf-8")
    for word in ("suitable", "appropriate", "threshold", "Comparison rules", "5.3", "6.3", "3.1",
                 "important", "where feasible", "where lawful", "complementary", "percentage",
                 "worked example", "Evaluation type", "Analysis design", "Institution and context"):
        assert word not in source.replace('"""', ""), word


def test_the_gate_is_not_read_by_a_model_path():
    for place in ("orchestrator", "gateway"):
        for path in (ROOT / "src" / place).rglob("*.py"):
            assert "methodology_gate" not in path.read_text(encoding="utf-8"), path


# --------------------------------------------------------------------------- the command line
def test_the_command_line_prints_the_faults_of_a_file_as_json(tmp_path, capsys):
    path = tmp_path / "m.md"
    path.write_text(doc(section_2="A suitable bar."), encoding="utf-8")
    assert methodology.main(["--gate", str(path)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert [f["rule"] for f in printed["faults"]] == ["GT-4"]
    assert set(printed["faults"][0]) == {"code", "section", "quote", "needed", "rule"}


def test_the_command_line_can_take_the_rules_of_a_template_for_the_references(tmp_path, capsys):
    path = tmp_path / "m.md"
    path.write_text(doc(), encoding="utf-8")
    template = ROOT / "templates" / "v1.yaml"
    assert methodology.main(["--gate", str(path), "--template", str(template)]) == 0
    assert json.loads(capsys.readouterr().out) == {"faults": []}
    path.write_text(doc().replace("### 6.4 Reading the trend", "### 6.5 Reading the trend"),
                    encoding="utf-8")
    methodology.main(["--gate", str(path), "--template", str(template)])
    found = json.loads(capsys.readouterr().out)["faults"]
    assert [(f["rule"], f["section"]) for f in found] == [("GT-7", "6.4")]


def test_the_command_line_says_so_when_the_file_cannot_be_read(tmp_path, capsys):
    path = tmp_path / "m.exe"
    path.write_text("x", encoding="utf-8")
    assert methodology.main(["--gate", str(path)]) == 1
    assert "failure" in json.loads(capsys.readouterr().out)


def test_the_plain_reader_command_is_unchanged(tmp_path, capsys):
    path = tmp_path / "m.md"
    path.write_text(doc(), encoding="utf-8")
    assert methodology.main([str(path)]) == 0
    assert "parsed" in json.loads(capsys.readouterr().out)
