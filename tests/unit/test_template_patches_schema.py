"""Migration 010 read as text (T-071): the patch table, its guard, its grants. No database.

The same rules are enforced by the database in tests/unit/test_template_patches_db.py."""

import re
from pathlib import Path

from mdeis.db.migrate import migration_files
from mdeis.measure_build import patch

ROOT = Path(__file__).resolve().parents[2]
FILE = ROOT / "src" / "mdeis" / "db" / "migrations" / "010_template_patches.sql"


def sql():
    return re.sub(r"--[^\n]*", "", FILE.read_text(encoding="utf-8"))


def test_the_migration_is_the_next_numbered_file_and_earlier_ones_are_not_edited():
    names = [f.name for f in migration_files()]
    assert "010_template_patches.sql" in names
    assert names == sorted(names) and names.index("010_template_patches.sql") == 9


def test_it_creates_the_table_the_sequences_and_nothing_else_it_does_not_own():
    code = sql()
    assert "CREATE TABLE template_patches" in code
    assert "CREATE SEQUENCE template_patch_seq" in code
    assert "CREATE SEQUENCE measure_param_seq" in code
    assert not re.search(r"\bALTER\s+TABLE\b", code, re.IGNORECASE), "no earlier table is changed"


def test_the_statuses_are_proposed_applied_and_rejected_and_a_patch_starts_proposed():
    code = sql()
    assert "CHECK (status IN ('proposed', 'applied', 'rejected'))" in code
    assert "DEFAULT 'proposed'" in code


def test_the_patch_text_is_held_with_its_hash_and_the_hash_must_match():
    code = sql()
    assert "patch_sha256" in code and "patch_matches_hash" in code
    assert "sha256(convert_to(patch_text, 'UTF8'))" in code


def test_applying_needs_a_confirmed_measure_and_the_name_is_copied_by_the_guard():
    code = sql()
    assert "teacher_decision = 'confirmed'" in code
    assert "NEW.confirmed_by_name := decided.teacher_name" in code
    assert "applied_whole" in code and "rejected_has_a_reason" in code


def test_the_guard_refuses_delete_and_truncate_and_changes_to_what_the_patch_is():
    code = sql()
    assert "TG_OP IN ('DELETE', 'TRUNCATE')" in code
    assert "BEFORE INSERT OR UPDATE OR DELETE ON template_patches" in code
    assert "BEFORE TRUNCATE ON template_patches" in code
    for column in ("patch_text", "patch_sha256", "clauses", "section_hashes", "measure_id"):
        assert f"'{column}'" in code, column


def test_nobody_may_delete_and_the_read_only_role_has_nothing_on_it():
    code = sql()
    assert "REVOKE DELETE, TRUNCATE ON template_patches FROM PUBLIC" in code
    assert "FROM %I', current_user" in code
    assert "REVOKE ALL ON template_patches FROM mdeis_ro" in code
    assert not re.search(r"GRANT\s+[^;]*template_patches", code, re.IGNORECASE)


def test_the_table_holds_no_school_value_and_no_clause_text_is_written_in_the_sql():
    code = sql().lower()
    for word in ("move_threshold", "pass_mark", "window_terms", "min_scores"):
        assert word not in code, word


def test_a_patch_body_hash_is_the_hash_of_the_canonical_text_the_table_checks():
    # The table checks sha256 of the text; the code hashes the same text. One definition.
    body = {"base_template_version": "baobab-v1", "measure_params": [{"id": "MP-1"}]}
    import hashlib

    text = patch.canonical_text(body)
    assert patch.patch_sha256(body) == hashlib.sha256(text.encode("utf-8")).hexdigest()
