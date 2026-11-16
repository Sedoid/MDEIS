"""Migration 009 as text: the file, its names, and what it grants (T-060, slice 3). No database
needed, so these run everywhere. The constraints themselves are tested in test_lesson_plans_db.py.
"""

import re
from pathlib import Path

from mdeis.db.migrate import migration_files

MIGRATIONS = Path(__file__).resolve().parents[2] / "src" / "mdeis" / "db" / "migrations"
SQL = MIGRATIONS / "009_lesson_plans.sql"


def code():
    text = SQL.read_text(encoding="utf-8")
    return re.sub(r"--[^\n]*", "", text)


def test_the_lesson_migration_is_number_nine_and_the_numbers_still_have_no_gap():
    names = [f.name for f in migration_files()]
    assert "009_lesson_plans.sql" in names
    assert names.index("009_lesson_plans.sql") == 8
    numbers = [int(n.split("_")[0]) for n in names]
    assert numbers == list(range(1, len(numbers) + 1))


def test_every_constraint_the_tests_and_the_code_rely_on_is_named_in_the_file():
    sql = code()
    for name in (
        "approved_needs_approver_and_check", "decision_whole", "plan_has_kept_edit",
        "edits_cite_stored_evidence", "original_matches_hash", "objective_matches_hash",
        "revised_matches_hash", "pins_match_column", "shapes_are_right",
        "lesson_plans_guard", "lesson_plans_no_truncate", "lesson_plan_moves",
    ):
        assert name in sql, name


def test_the_three_legal_moves_are_data_in_the_file():
    sql = code()
    moves = ("('proposed', 'approved')", "('proposed', 'rejected')",
             "('approved', 'superseded')")
    for move in moves:
        assert move in sql


def test_nothing_is_granted_that_writes_and_the_read_only_role_loses_the_table():
    sql = code()
    grants = re.findall(r"\bGRANT\b[^;]*;", sql, re.IGNORECASE)
    assert grants == ["GRANT SELECT ON lesson_plan_moves TO mdeis_ro;"]
    assert "REVOKE ALL ON lesson_plans FROM mdeis_ro" in sql
    assert re.search(r"REVOKE DELETE, TRUNCATE ON lesson_plans[^;]*FROM PUBLIC", sql)


def test_no_role_that_the_portal_or_the_gateway_uses_is_named_so_none_can_write_the_table():
    sql = code().lower()
    for role in ("portal_app", "gateway", "orchestrator_app", "n8n"):
        assert role not in sql


def test_a_plan_is_never_deleted_by_the_file_and_a_delete_raises():
    sql = code()
    assert "never deleted" in sql
    assert not re.search(r"\bdelete\s+from\b", sql, re.IGNORECASE)


def test_the_school_is_a_column_filled_from_the_session_and_no_school_value_is_written():
    sql = code().lower()
    assert "school_code" in sql
    assert not re.search(r"'(occ|tst|s-001)'", sql)
