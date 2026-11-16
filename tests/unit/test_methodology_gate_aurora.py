"""The clarity gate on the Aurora methodology and on v2.0 (T-051 follow-up, 2026-11-16).

Aurora is a second methodology with another structure: its settings table is in section 4, section
6.3 is a list of competences, and its PDF is a variable-font export whose font names never say Bold.
`data/synthetic/methodology/README-aurora.md` says which faults a good gate reports on it and which
it must not. The expected values here come from that README and from reading the document by hand,
not from running the gate.

* Must not report: the settings table sitting in section 4, section 6.3 as a measure, the PDF as
  unnumbered.
* Should report: complementary suppression and the reconstruction checks with no method (section
  10), and vague wording ("important", "where feasible", "where lawful").
* Not found by any rule, and not tested: the ambiguous "Mixed" label, "term" used for courses, and
  the draft or illustrative notice (GT-11 is not built, see the log).

Aurora names four of the eleven required settings differently from v2.0 ("Institution and
context", "Analysis design", "Subjects and learning areas", "Reporting, roles and approvals"). The
owner approved those four as accepted alternative names (`aliases` in the gate file, each marked
PROPOSED), so GT-1 reports nothing on Aurora. A name that is not listed is still a missing setting.

The PDF half skips without `pdfplumber`.
"""

import json

import pytest
import yaml
from test_methodology_gate import BANNED, FIXTURES, GATE, ROOT

from mdeis.ingest import methodology
from mdeis.ingest import methodology_gate as gate

AURORA_MD = FIXTURES / "methodology-aurora.md"
AURORA_PDF = FIXTURES / "methodology-aurora.pdf"
V2_MD = FIXTURES / "methodology-v2.0.md"

def read(path):
    parsed = methodology.parse_methodology(path)
    assert not isinstance(parsed, methodology.Failure), parsed
    return parsed


def faults(path, refs=None):
    return gate.check(read(path).sections, refs or {}, GATE)


def shape(found):
    return [(f.rule, f.section) for f in found]


def of_rule(found, rule):
    return [f for f in found if f.rule == rule]


def text_of(parsed, section_id):
    return next(s.text for s in parsed.sections if s.id == section_id)


# --------------------------------------------------------------------------- Aurora, in Markdown
def test_aurora_the_measures_are_found_by_content_and_6_3_is_not_one():
    ids = gate.measure_ids(read(AURORA_MD).sections, GATE)
    assert ids == ["6.1", "8", "9", "13"]
    assert "6.3" not in ids


def test_aurora_no_false_fault_from_gt2_gt3_gt5_gt6_gt7_gt8():
    found = faults(AURORA_MD)
    for rule in ("GT-2", "GT-3", "GT-5", "GT-6", "GT-7", "GT-8"):
        assert of_rule(found, rule) == [], rule
    assert [f for f in found if f.section == "6.3"] == []


def test_aurora_no_fault_from_gt4_it_uses_none_of_the_four_old_vague_words():
    assert of_rule(faults(AURORA_MD), "GT-4") == []


def test_aurora_the_settings_table_in_section_4_is_found_and_every_setting_is_defined():
    assert of_rule(faults(AURORA_MD), "GT-1") == []


def test_aurora_without_the_aliases_the_four_differently_named_settings_would_be_missing(tmp_path):
    data = yaml.safe_load((ROOT / "templates" / "gate-v1.yaml").read_text(encoding="utf-8"))
    data["checks"][0]["aliases"] = {}
    path = tmp_path / "gate.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    bare = gate.load_gate(path)
    found = of_rule(gate.check(read(AURORA_MD).sections, {}, bare), "GT-1")
    assert {f.section for f in found} == {"4"}
    assert len(found) == 4 and "Evaluation type" in found[1].needed


def test_aurora_gt9_reports_complementary_suppression_and_the_reconstruction_checks():
    parsed = read(AURORA_MD)
    found = of_rule(gate.check(parsed.sections, {}, GATE), "GT-9")
    assert [f.section for f in found] == ["10", "10"]
    assert "Complementary suppression" in found[0].quote
    assert "reconstruction risk" in found[1].quote
    assert all(f.code == "procedure_not_defined" for f in found)
    assert all(f.quote in text_of(parsed, "10") for f in found)


def test_aurora_gt10_reports_important_where_feasible_and_where_lawful():
    parsed = read(AURORA_MD)
    found = of_rule(gate.check(parsed.sections, {}, GATE), "GT-10")
    assert [f.section for f in found] == ["2", "2", "4", "12"]
    assert "important statement" in found[0].quote
    assert "Send important findings" in found[1].quote
    assert "(section 12)" in found[1].quote, "a pointer to section 12 is not a value"
    assert "where feasible" in found[2].quote
    assert "where lawful" in found[3].quote
    assert all(f.code == "vague_scope" for f in found)


def test_aurora_is_turned_back_because_it_has_real_faults_and_the_list_is_what_was_read_by_hand():
    found = faults(AURORA_MD)
    assert found, "a document with real faults is not passed"
    assert shape(found) == (
        [("GT-10", "2"), ("GT-10", "2")]
        + [("GT-10", "4")]
        + [("GT-9", "10"), ("GT-9", "10")]
        + [("GT-10", "12")]
    )


def test_aurora_every_quote_is_verbatim_short_and_every_fault_is_plain_text():
    parsed = read(AURORA_MD)
    texts = {s.id: s.text for s in parsed.sections}
    for fault in gate.check(parsed.sections, {}, GATE):
        assert len(fault.quote) <= GATE.quote_max
        assert fault.quote == "" or fault.quote in texts[fault.section]
        assert fault.needed.strip() and chr(0x2014) not in fault.needed
        assert not any(word in fault.needed.lower() for word in BANNED), fault.needed


def test_aurora_the_same_input_twice_gives_the_same_faults_in_the_same_order():
    assert faults(AURORA_MD) == faults(AURORA_MD)


def test_aurora_on_the_command_line_prints_the_same_faults_as_json(capsys):
    assert methodology.main(["--gate", str(AURORA_MD)]) == 0
    printed = json.loads(capsys.readouterr().out)["faults"]
    assert [(f["rule"], f["section"]) for f in printed] == shape(faults(AURORA_MD))


def test_a_template_rule_on_a_section_aurora_has_is_not_a_fault_and_one_it_lacks_is():
    assert of_rule(faults(AURORA_MD, {"TR-1": "6.4", "TR-2": "8"}), "GT-7") == []
    found = of_rule(faults(AURORA_MD, {"TR-9": "3.1"}), "GT-7")
    assert [(f.section, f.quote) for f in found] == [("3.1", "")]


# --------------------------------------------------------------------------- both formats agree
def test_the_aurora_pdf_and_markdown_give_the_same_section_ids_and_the_same_fault_set():
    pytest.importorskip("pdfplumber")
    if not AURORA_PDF.exists():
        pytest.skip("methodology-aurora.pdf is not here")
    pdf, md = read(AURORA_PDF), read(AURORA_MD)
    assert [s.id for s in pdf.sections] == [s.id for s in md.sections]
    from_pdf = gate.check(pdf.sections, {}, GATE)
    from_md = gate.check(md.sections, {}, GATE)
    assert shape(from_pdf) == shape(from_md)
    assert [f.code for f in from_pdf] == [f.code for f in from_md]
    assert of_rule(from_pdf, "GT-1") == [], "the PDF's settings table is read as table rows"
    assert [f.section for f in from_pdf] != [None] * len(from_pdf), "faults name their sections"


def test_the_aurora_pdf_is_not_turned_back_as_unnumbered():
    pytest.importorskip("pdfplumber")
    if not AURORA_PDF.exists():
        pytest.skip("methodology-aurora.pdf is not here")
    found = faults(AURORA_PDF)
    assert of_rule(found, "GT-8") == []
    assert {"GT-9", "GT-10"} <= {f.rule for f in found}


def test_print_what_the_gate_finds_in_aurora_in_both_formats(capsys):
    """Not a verdict: it prints the faults so the owner can read them (run with -s)."""
    with capsys.disabled():
        for name, path in (("md", AURORA_MD), ("pdf", AURORA_PDF)):
            if name == "pdf":
                pytest.importorskip("pdfplumber")
            found = faults(path)
            print(f"\nGATE ON aurora ({name}): {len(found)} fault(s)")
            for fault in found:
                print(f"  {fault.rule} {fault.code} section={fault.section}: "
                      f"{fault.quote[:70]!r}")
    assert True


# --------------------------------------------------------------------------- v2.0 stays rejected
def v2_refs():
    template = yaml.safe_load((ROOT / "templates" / "v1.yaml").read_text(encoding="utf-8"))
    return {
        item["id"]: str(item["method_ref"])
        for value in template.values()
        if isinstance(value, list)
        for item in value
        if isinstance(item, dict) and "method_ref" in item
    }


LETTERED_V2 = ["5.3.A", "5.3.B", "5.3.C", "5.3.D", "5.3.E",
               "6.3.A", "6.3.B", "6.3.C", "6.3.D", "6.3.E", "6.3.F"]


def test_v2_0_has_the_same_eleven_lettered_measures_and_no_other_measure_by_content():
    assert gate.measure_ids(read(V2_MD).sections, GATE) == LETTERED_V2


def test_v2_0_is_still_turned_back_the_gate_was_not_loosened_to_let_it_through():
    found = gate.check(read(V2_MD).sections, v2_refs(), GATE)
    assert found
    rules = {f.rule for f in found}
    assert {"GT-2", "GT-3", "GT-4", "GT-5", "GT-10"} <= rules
    for rule in ("GT-1", "GT-6", "GT-7", "GT-8", "GT-9"):
        assert rule not in rules, rule


def test_v2_0_the_faults_that_stay_and_the_two_that_go_after_the_marker_change():
    found = gate.check(read(V2_MD).sections, v2_refs(), GATE)
    assert [f.section for f in of_rule(found, "GT-2")] == ["5.3.B", "5.3.E", "6.3.D", "6.3.E"]
    assert [f.section for f in of_rule(found, "GT-3")] == ["5.3.C"]
    assert len(of_rule(found, "GT-4")) == 8, "GT-4 is unchanged"
    assert [f.section for f in of_rule(found, "GT-5")] == [
        "5.3.B", "5.3.C", "5.3.D", "5.3.E", "6.3.A", "6.3.B", "6.3.C", "6.3.D", "6.3.E", "6.3.F"]


def test_print_what_the_gate_finds_in_v2_0_now(capsys):
    """Prints the faults so the owner can compare them with the 25 faults of the first run."""
    found = gate.check(read(V2_MD).sections, v2_refs(), GATE)
    with capsys.disabled():
        print(f"\nGATE ON v2.0 (reader and gate follow-up): {len(found)} fault(s)")
        counts = {}
        for fault in found:
            counts[fault.rule] = counts.get(fault.rule, 0) + 1
            print(f"  {fault.rule} {fault.code} section={fault.section}: {fault.quote[:70]!r}")
        print("  per rule:", dict(sorted(counts.items())))
    assert all(f.rule in GATE.checks for f in found)
