"""The gate in the methodology ingest (T-051): what `store_methodology` does with its verdict.

The connection is a recorder, so these run without Postgres and show exactly which statements the
ingest sends. The database side (the real constraints, the portal's queries) is in
test_methodology_gate_store.py.

Written before the wiring (tests first). They fail until the gate is called from
`store_methodology`, and pass once it is.
"""

import re
from pathlib import Path

import pytest
from test_methodology_gate import FREE_FORM, GATE, SETTINGS, doc

from mdeis.ingest import methodology as m
from mdeis.ingest import methodology_gate as gate

ROOT = Path(__file__).resolve().parents[2]
NEW = "D-000009"


class Recorder:
    """A connection that records every statement. `previous` is the methodology in force."""

    def __init__(self, previous=None):
        self.calls, self.previous, self.last = [], previous, ""

    def execute(self, sql, params=()):
        self.last = " ".join(sql.split())
        self.calls.append((self.last, tuple(params)))
        return self

    def fetchone(self):
        if self.previous and self.last.startswith("SELECT document_id FROM documents"):
            return ("D-000001",)
        return None

    def fetchall(self):
        if self.previous and self.last.startswith("SELECT section_id"):
            return [(s.id, s.title, s.page, s.text, s.sha256) for s in self.previous]
        return []

    def starting(self, text):
        return [params for sql, params in self.calls if sql.startswith(text)]


@pytest.fixture(autouse=True)
def no_template_rules(monkeypatch):
    monkeypatch.setattr(m, "template_refs", lambda conn: {})


def parsed_of(markdown):
    return m.parse_markdown(markdown)


def store(markdown, previous=None):
    conn = Recorder(previous=previous.sections if previous else None)
    result = m.store_methodology(conn, NEW, parsed_of(markdown))
    return conn, result


def kinds(conn):
    return [params[1] for params in conn.starting("INSERT INTO document_issues")]


# --------------------------------------------------------------------------- a rejected document
VAGUE = doc(section_2="The method must be suitable for the group.")


def test_a_fault_fails_the_document_with_the_kind_methodology_unclear():
    conn, result = store(VAGUE)
    assert result["status"] == "failed" and result["active"] is False
    assert result["failure_kind"] == "methodology_unclear" == m.UNCLEAR
    (update,) = conn.starting("UPDATE documents SET status = 'failed'")
    kind, detail, document_id = update
    assert kind == "methodology_unclear" and document_id == NEW
    assert detail.strip(), "a failed document always says why"


def test_the_faults_are_kept_as_document_issues_with_section_page_and_the_words_quoted():
    conn, result = store(VAGUE)
    rows = conn.starting("INSERT INTO document_issues")
    assert len(rows) == result["issues"] == 1
    document_id, kind, ref_id, page, detail = rows[0]
    assert (document_id, kind, ref_id) == (NEW, "vague_term", "2")
    assert page == next(s.page for s in parsed_of(VAGUE).sections if s.id == "2")
    assert "GT-4" in detail and "The method must be suitable for the group." in detail


def test_the_sections_are_stored_first_and_then_the_faults_and_then_the_status():
    conn, _ = store(VAGUE)
    steps = [sql for sql, _ in conn.calls if sql.startswith(("INSERT", "UPDATE"))]
    sections = len(parsed_of(VAGUE).sections)
    assert [s.split(" (")[0] for s in steps[:sections]] == ["INSERT INTO method_chunks"] * sections
    assert [s.split(" (")[0] for s in steps[sections:-1]] == ["INSERT INTO document_issues"]
    assert steps[-1].startswith("UPDATE documents SET status = 'failed'")


def test_a_rejected_document_is_not_compared_and_does_not_replace_the_one_in_force():
    previous = parsed_of(doc())
    conn, result = store(VAGUE, previous=previous)
    assert result["previous"] == "D-000001"
    assert set(kinds(conn)) == {"vague_term"}, "no section_added, section_changed or review issue"
    assert conn.starting("UPDATE documents SET active = false WHERE document_id") == []
    assert all("needs_review" not in sql and "'done'" not in sql for sql, _ in conn.calls)


def test_a_rejected_document_makes_no_template_comparison_even_with_rules_that_rest_on_it(
        monkeypatch):
    monkeypatch.setattr(m, "template_refs", lambda conn: {"TR-1": "6.4"})
    conn, _ = store(VAGUE)
    assert "template_ref_missing" not in kinds(conn)
    assert "referenced_section_changed" not in kinds(conn)


def test_a_broken_template_reference_is_turned_back_as_a_fault_not_sent_to_review(monkeypatch):
    monkeypatch.setattr(m, "template_refs", lambda conn: {"TR-1": "9.9"})
    conn, result = store(doc())
    assert result["status"] == "failed"
    assert kinds(conn) == ["template_ref_broken"]
    assert conn.starting("INSERT INTO document_issues")[0][2] == "9.9"


def test_a_document_without_numbering_is_turned_back_with_one_fault_and_no_section_named():
    conn, result = store(FREE_FORM)
    rows = conn.starting("INSERT INTO document_issues")
    assert result["status"] == "failed" and len(rows) == 1
    assert rows[0][1] == "no_numbering" and rows[0][2] is None and rows[0][3] is None


def test_every_fault_is_stored_in_the_order_the_gate_gives_it():
    conn, _ = store(doc(settings=SETTINGS[1:], section_2="A suitable bar."))
    stored = [(p[2], p[1]) for p in conn.starting("INSERT INTO document_issues")]
    sections = [s.id for s in parsed_of(VAGUE).sections if s.id]
    positions = [sections.index(section) for section, _ in stored]
    assert positions == sorted(positions) and len(stored) == 2


# --------------------------------------------------------------------------- a clear document
def test_a_clear_document_goes_on_exactly_as_before_and_is_put_in_force():
    conn, result = store(doc())
    assert result == {
        "status": "done", "active": True, "sections": len(parsed_of(doc()).sections),
        "issues": 0, "previous": None,
    }
    assert conn.starting("INSERT INTO document_issues") == []
    (update,) = conn.starting("UPDATE documents SET status = %s")
    assert update == ("done", True, NEW)


def test_a_clear_document_that_replaces_another_unchanged_takes_its_place():
    previous = parsed_of(doc())
    conn, result = store(doc(), previous=previous)
    assert result["status"] == "done" and result["previous"] == "D-000001"
    assert conn.starting("UPDATE documents SET active = false WHERE document_id") == [("D-000001",)]


def test_a_clear_document_that_changes_a_section_still_waits_for_a_person():
    previous = parsed_of(doc())
    conn, result = store(doc(section_2="MDEIS describes the records in plain words."),
                         previous=previous)
    assert result["status"] == "needs_review" and result["active"] is False
    assert kinds(conn) == ["section_changed"]
    assert conn.starting("UPDATE documents SET active = false WHERE document_id") == []


def test_a_clear_document_with_a_repeated_section_number_still_goes_to_review():
    twice = doc().replace("## 4. Process", "### 3.1 Again\n\nSaid twice.\n\n## 4. Process")
    conn, result = store(twice)
    assert result["status"] == "needs_review"
    assert kinds(conn) == ["duplicate_section_marker"]


def test_a_clear_document_with_a_template_rule_on_a_section_that_changed_is_still_reported(
        monkeypatch):
    monkeypatch.setattr(m, "template_refs", lambda conn: {"TR-1": "6.4"})
    previous = parsed_of(doc())
    changed = doc().replace("The configuration defines the number of results needed.",
                            "The configuration defines how many results are needed.")
    conn, result = store(changed, previous=previous)
    assert result["status"] == "needs_review"
    assert kinds(conn) == ["section_changed", "referenced_section_changed"]


# --------------------------------------------------------------------------- what is passed in
def test_the_gate_gets_the_stored_sections_and_the_rules_of_the_active_template(monkeypatch):
    seen = []
    monkeypatch.setattr(m, "template_refs", lambda conn: {"TR-1": "6.4"})

    def spy(sections, refs, *args):
        seen.append((list(sections), dict(refs)))
        return []

    monkeypatch.setattr(m.methodology_gate, "check", spy)
    store(doc())
    assert len(seen) == 1
    assert seen[0][0] == list(parsed_of(doc()).sections) and seen[0][1] == {"TR-1": "6.4"}


def test_a_gate_file_that_cannot_be_read_stops_the_ingest_instead_of_letting_a_document_through(
        monkeypatch, tmp_path):
    monkeypatch.setenv(gate.GATE_ENV, str(tmp_path / "absent.yaml"))
    with pytest.raises(gate.GateFileError):
        store(doc())


# --------------------------------------------------------------------------- no schema change
def test_failure_kind_is_free_text_in_sql_so_the_new_kind_needs_no_migration():
    migrations = sorted((ROOT / "src" / "mdeis" / "db" / "migrations").glob("*.sql"))
    text = "\n".join(p.read_text(encoding="utf-8") for p in migrations)
    assert "methodology_unclear" not in text, "no migration names the kind"
    assert re.search(r"failure_kind\s+text\s*,", text), "a plain text column"
    assert re.search(r"failure_kind\s+(NOT\s+)?IN\s*\(", text, re.I) is None
    assert re.search(r"CHECK\s*\([^)]*failure_kind\s*(=|<>|IN|~)", text, re.I) is None
    assert "failed_has_kind CHECK (status <> 'failed' OR failure_kind IS NOT NULL)" in text


def test_the_gate_file_sits_beside_the_rulebooks_and_is_version_gate_v1():
    names = sorted(p.name for p in (ROOT / "templates").glob("*.yaml"))
    assert "gate-v1.yaml" in names and GATE.version == "gate-v1"
