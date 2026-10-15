"""The reading half: the methodology reader against its truth files.

The Markdown copies of the real v2.0 document have a list of the sections a correct reader finds
(`*.sections.json`) and a list of what differs between them (`methodology-changes.json`), both made
by separate code that does not import src/mdeis. The real PDF has the same list of sections as v2.0.
The reader must find exactly those, and say which rules of the school's rulebook rest on a section
that changed.
"""

import json
from pathlib import Path

import pytest
import yaml

from mdeis.ingest import methodology as m

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "synthetic" / "methodology"
REAL_PDF = ROOT / "data" / "synthetic" / "samples" / "Methodology_of_mdeis.pdf"
COPIES = ["methodology-v2.0", "methodology-v2.1-small", "methodology-v2.1-full"]


def sections_truth(name):
    return json.loads((DATA / f"{name}.sections.json").read_text(encoding="utf-8"))


def read_md(name):
    result = m.parse_methodology(DATA / f"{name}.md")
    assert not isinstance(result, m.Failure), result
    return result


def ids(parsed):
    return [{"id": s.id, "level": s.level, "title": s.title} for s in parsed.sections if s.id]


def refs():
    """{rule id: section} straight from the rulebook file (the database holds it as method_ref)."""
    template = yaml.safe_load((ROOT / "templates" / "v1.yaml").read_text(encoding="utf-8"))
    return {
        item["id"]: str(item["method_ref"])
        for value in template.values()
        if isinstance(value, list)
        for item in value
        if isinstance(item, dict) and "method_ref" in item
    }


@pytest.mark.parametrize("name", COPIES)
def test_the_markdown_copies_give_exactly_the_sections_in_their_truth_file(name):
    assert ids(read_md(name)) == sections_truth(name)


def test_numbered_list_items_are_text_not_sections():
    parsed = read_md("methodology-v2.0")
    found = {s.id for s in parsed.sections}
    assert "3.2" in found and "10" in found
    assert not any(i and i.startswith("3.2.") for i in found)
    text = next(s.text for s in parsed.sections if s.id == "3.2")
    assert "1. Laws and official education regulations." in text


def test_each_section_knows_its_page_and_its_own_words():
    for s in read_md("methodology-v2.0").sections:
        assert s.page >= 1 and s.sha256 and (s.id is None or s.title)
    title_block = read_md("methodology-v2.0").sections[0]
    assert title_block.id is None and "Simple Evaluation Methodology" in title_block.text


@pytest.mark.parametrize("name", ["methodology-v2.1-small", "methodology-v2.1-full"])
def test_a_changed_copy_is_compared_with_the_base_exactly_as_the_truth_file_says(name):
    changes = json.loads((DATA / "methodology-changes.json").read_text(encoding="utf-8"))
    want = changes["copies"][f"{name}.md"]
    found = m.diff_sections(read_md("methodology-v2.0").sections, read_md(name).sections)
    by_kind = {
        kind: sorted(i["ref_id"] for i in found if i["kind"] == kind)
        for kind in (m.ADDED, m.REMOVED, m.CHANGED)
    }
    assert by_kind[m.ADDED] == sorted(x["section"] for x in want["added"])
    assert by_kind[m.REMOVED] == sorted(x["section"] for x in want["removed"])
    assert by_kind[m.CHANGED] == sorted(x["section"] for x in want["changed"])


@pytest.mark.parametrize("name", ["methodology-v2.1-small", "methodology-v2.1-full"])
def test_the_rules_resting_on_a_changed_section_are_named(name):
    changes = json.loads((DATA / "methodology-changes.json").read_text(encoding="utf-8"))
    want = changes["copies"][f"{name}.md"]["template_rules_resting_on_changed_or_removed_sections"]
    parsed = read_md(name)
    found = m.diff_sections(read_md("methodology-v2.0").sections, parsed.sections)
    moved = {i["ref_id"] for i in found if i["kind"] in (m.REMOVED, m.CHANGED)}
    issues = m.check_template_refs(parsed.sections, refs(), moved)
    assert not [i for i in issues if i["kind"] == m.REF_MISSING]
    named = {}
    for i in issues:
        assert i["kind"] == m.REF_CHANGED
        named.setdefault(i["section"], []).append(i["ref_id"])
    for section, rules in want.items():
        assert sorted(named.get(section, [])) == sorted(rules), section


def test_the_unchanged_base_has_no_finding_at_all():
    parsed = read_md("methodology-v2.0")
    assert parsed.issues == ()
    assert m.check_template_refs(parsed.sections, refs(), set()) == []


def test_a_document_that_drops_a_section_a_rule_rests_on_is_reported():
    parsed = read_md("methodology-v2.0")
    without = [s for s in parsed.sections if s.id != "6.4"]
    issues = m.check_template_refs(without, refs(), {"6.4"})
    assert ("template_ref_missing", "TR-1") in {(i["kind"], i["ref_id"]) for i in issues}


def test_a_document_without_numbering_is_one_block_and_is_flagged():
    parsed = read_md("methodology-free-form")
    assert [s.id for s in parsed.sections] == [None]
    assert [i["kind"] for i in parsed.issues] == [m.NO_MARKERS]


def test_the_same_words_with_other_spacing_are_not_a_change():
    assert m.fingerprint("a  b\n c") == m.fingerprint("a b c")
    assert m.fingerprint("a\u200b b") == m.fingerprint("a b"), "zero-width spaces are removed"
    assert m.fingerprint("a b") != m.fingerprint("a b.")


def test_a_wrong_file_type_is_refused(tmp_path):
    path = tmp_path / "notes.docx"
    path.write_bytes(b"x")
    assert m.parse_methodology(path).kind == m.UNSUPPORTED_TYPE
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"not a pdf")
    assert m.parse_methodology(fake).kind == m.NOT_A_PDF


# --------------------------------------------------------------------------- the real PDF
def test_the_real_pdf_gives_the_sections_of_its_truth_file_with_pages():
    pytest.importorskip("pdfplumber")
    if not REAL_PDF.exists():
        pytest.skip("the real methodology PDF is not in data/synthetic/samples")
    parsed = m.parse_methodology(REAL_PDF)
    assert not isinstance(parsed, m.Failure), parsed
    assert ids(parsed) == sections_truth("methodology-v2.0")
    pages = [s.page for s in parsed.sections]
    assert pages == sorted(pages), "sections run in page order"
    assert max(pages) <= parsed.pages
