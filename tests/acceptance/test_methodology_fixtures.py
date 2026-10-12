"""The changed copies of the methodology document and the list of what differs (E-15 data side)."""

import difflib
import json

import pytest
import yaml
from synthetic import generate, methodology

# The sections of the real v2.0 PDF, read off its headings (bold 17 pt, bold 13 pt, bold 11 pt A-F)
# by eye, page by page, and typed here. Not produced by the converter that makes the Markdown.
REAL_SECTIONS = (
    ["1", "2", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "3", "3.1", "3.2", "3.3", "4"]
    + [f"4.S{n}" for n in range(1, 10)]
    + ["5", "5.1", "5.2", "5.3"]
    + [f"5.3.{x}" for x in "ABCDE"]
    + ["5.4", "5.5", "6", "6.1", "6.2", "6.3"]
    + [f"6.3.{x}" for x in "ABCDEF"]
    + ["6.4", "6.5", "6.6", "7", "8", "9", "10", "11", "12", "13"]
    + [f"13.{n}" for n in range(1, 7)]
    + ["14", "15", "16"]
)


@pytest.fixture(scope="module")
def made(root, templates_dir):
    source = root / "data" / "synthetic" / "methodology" / "methodology-v2.0.md"
    files = generate.methodology_files(source, templates_dir / "v1.yaml")
    text = {k.split("/", 1)[1]: v.decode() for k, v in files.items()}
    return text, source.read_text(encoding="utf-8")


def changed_lines(a, b):
    sm = difflib.SequenceMatcher(None, a.splitlines(), b.splitlines(), autojunk=False)
    return [op for op in sm.get_opcodes() if op[0] != "equal"]


def test_the_base_document_has_every_section_of_the_real_one(made):
    text, base = made
    assert [s["id"] for s in methodology.sections(base)] == REAL_SECTIONS
    titles = {s["id"]: s["title"] for s in methodology.sections(base)}
    assert titles["6.4"] == "Trend labels" and titles["4.S3"] == "Check the data"
    assert titles["5.3.A"] == "Proficiency distribution"


def test_numbered_list_items_are_not_taken_for_sections(made):
    _, base = made
    ids = {s["id"] for s in methodology.sections(base)}
    assert "3.2" in ids and not any(i.startswith("3.2.") for i in ids)
    assert "10" in ids
    assert "1. Laws and official education regulations." in base  # still there, as text
    assert "## 1. Laws" not in base


def test_the_small_copy_differs_in_one_sentence_of_6_4(made):
    text, base = made
    small = text["methodology-v2.1-small.md"]
    ops = changed_lines(base, small)
    assert len(ops) == 1
    start, end = methodology.section_span(base, "6.4")
    first_changed = base.splitlines()[ops[0][1]]
    assert base.index(first_changed) >= start and base.index(first_changed) < end
    assert [s["id"] for s in methodology.sections(small)] == REAL_SECTIONS


def test_the_full_copy_changes_adds_and_removes_one_section_each(made):
    text, base = made
    full = text["methodology-v2.1-full.md"]
    ids = [s["id"] for s in methodology.sections(full)]
    assert set(ids) - set(REAL_SECTIONS) == {"5.6"}
    assert set(REAL_SECTIONS) - set(ids) == {"6.6"}
    assert "### 6.6 Example" not in full and "### 5.6 Small groups" in full
    assert "or on results from assessments that cannot reasonably be compared" in full


def test_the_changes_file_says_exactly_that_and_names_the_rules(made):
    text, _ = made
    changes = json.loads(text["methodology-changes.json"])
    small = changes["copies"]["methodology-v2.1-small.md"]
    full = changes["copies"]["methodology-v2.1-full.md"]
    assert [c["section"] for c in small["changed"]] == ["6.4"]
    assert small["added"] == [] and small["removed"] == []
    assert [c["section"] for c in full["changed"]] == ["6.4"]
    assert [a["section"] for a in full["added"]] == ["5.6"]
    assert [r["section"] for r in full["removed"]] == ["6.6"]
    key = "template_rules_resting_on_changed_or_removed_sections"
    assert small[key] == {"6.4": ["TR-1"]}
    assert full[key] == {"6.4": ["TR-1"], "6.6": []}


def test_the_rules_named_are_the_ones_the_template_ties_to_6_4(templates_dir):
    for name in ("v1", "v2-strict"):
        raw = yaml.safe_load((templates_dir / f"{name}.yaml").read_text())
        tied = sorted(r["id"] for r in raw["trend"] if r.get("method_ref") == "6.4")
        assert tied == ["TR-1"], name


def test_the_free_form_copy_has_no_numbering_on_any_heading(made):
    text, _ = made
    free = text["methodology-free-form.md"]
    heads = [line for line in free.splitlines() if line.startswith("#")]
    assert len(heads) >= 60
    assert not [h for h in heads if h.lstrip("# ")[:1].isdigit() or "Step " in h]
    assert json.loads(text["methodology-free-form.sections.json"]) == []


def test_the_section_lists_match_the_documents(made):
    text, _ = made
    for name in ("methodology-v2.0", "methodology-v2.1-small", "methodology-v2.1-full"):
        listed = [s["id"] for s in json.loads(text[f"{name}.sections.json"])]
        assert listed == [s["id"] for s in methodology.sections(text[f"{name}.md"])]


def test_building_twice_gives_the_same_text(root, templates_dir, made):
    source = root / "data" / "synthetic" / "methodology" / "methodology-v2.0.md"
    again = generate.methodology_files(source, templates_dir / "v1.yaml")
    text, _ = made
    assert {k.split("/", 1)[1]: v.decode() for k, v in again.items()} == text
