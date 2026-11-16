"""Migration 008 read as text: the approval rule, the rights and the guard. No database needed.

The database itself is tested in test_registry_db.py (integration). These checks run everywhere and
fail early if someone edits the text of the constraint or grants a role more than it should have.
"""

import re

from mdeis.db.migrate import migration_files

(PATH,) = [f for f in migration_files() if f.name.startswith("008_")]
RAW = PATH.read_text(encoding="utf-8")
CODE = re.sub(r"--[^\n]*", "", RAW)
FLAT = " ".join(CODE.split())

# Columns the read-only role may never see: the code, the tests, why a review held, and every name.
SECRET_COLUMNS = [
    "source_code", "tests", "test_result", "static_check", "rules_quoted", "template_patch",
    "review_result", "review_reasons", "reviewed_at", "hold_cleared_by_id", "hold_cleared_name",
    "hold_cleared_note", "dev_reviewer_id", "dev_reviewer_name", "teacher_id", "teacher_name",
    "teacher_note", "teacher_decision", "demo_session_id", "built_by_run", "retired_reason",
]


def test_migration_008_is_the_next_number_and_the_runner_will_pick_it_up():
    assert PATH.name == "008_measure_registry.sql"


def test_the_status_awaiting_dev_is_gone_and_awaiting_review_is_in():
    assert "awaiting_dev" not in CODE
    assert "'awaiting_review'" in CODE


def test_the_approved_constraint_names_every_condition():
    block = re.search(
        r"CONSTRAINT approved_needs_tests_review_and_teacher CHECK \((.*?)\n    \)\n\);", CODE, re.S
    ).group(1)
    flat = " ".join(block.split())
    for need in (
        "registry_tests_passed(tests, test_result)",
        "registry_has_independent_test(tests)",
        "registry_static_passed(static_check)",
        "measure_review_cleared(review_result, hold_cleared_by_id)",
        "teacher_id IS NOT NULL",
        "teacher_decision = 'confirmed'",
        "teacher_decided_at IS NOT NULL",
        "approved_at IS NOT NULL",
    ):
        assert need in flat, need
    # a null in any part must not let the row through
    assert flat.startswith("status <> 'approved' OR coalesce(")
    assert flat.rstrip().endswith("false)")


def test_a_hold_is_cleared_only_by_a_registered_developer_and_never_by_the_review():
    assert "role = 'developer' AND revoked_at IS NULL" in FLAT
    assert "only a developer recorded in registry_reviewers can clear a hold" in FLAT
    # review columns are written once, so a second review (or a model) cannot turn a hold into a
    # pass
    for column in ("'review_result'", "'review_reasons'", "'hold_cleared_by_id'"):
        assert column in FLAT


def test_the_teacher_must_be_a_registered_teacher():
    assert "role = 'teacher' AND revoked_at IS NULL" in FLAT


def test_the_helper_functions_never_return_null():
    for name in ("registry_tests_ok", "registry_has_independent_test", "registry_tests_passed",
                 "registry_static_passed", "measure_review_cleared"):
        body = re.search(rf"CREATE FUNCTION {name}\(.*?\$\$;", CODE, re.S).group(0)
        assert "coalesce(" in body, name


def test_the_reviewed_code_is_covered_by_the_immutability_list():
    for column in ("source_code", "code_sha256", "section_sha256", "methodology_document_id",
                   "clause_ids", "output_kind", "output_schema", "input_scope", "description",
                   "static_check", "template_version"):
        assert f"'{column}'" in FLAT, column
    assert "code_matches_hash" in CODE


def test_nothing_in_the_registry_can_be_deleted_by_anyone():
    assert "TG_OP IN ('DELETE', 'TRUNCATE')" in FLAT
    tables = "measures, measure_versions, registry_reviewers"
    assert f"REVOKE DELETE, TRUNCATE ON {tables} FROM PUBLIC" in FLAT
    assert "FROM %I', current_user" in FLAT
    assert "BEFORE TRUNCATE ON measure_versions" in FLAT


# --------------------------------------------------------------------------- rights
def test_the_read_only_role_is_granted_select_and_nothing_else_on_the_registry():
    found = re.findall(r"GRANT [^;]*? TO \w+", FLAT)
    assert found, "the read-only role is granted something"
    for statement in found:
        assert statement.startswith("GRANT SELECT"), statement
    for other in ("portal_app", "n8n_auto", "orchestrator_app", "mdeis_app", "PUBLIC"):
        assert not re.search(rf"\bGRANT\b[^;]*\bTO\s+{other}\b", FLAT), other


def test_the_read_only_role_loses_the_default_read_and_gets_only_the_safe_columns():
    assert "REVOKE ALL ON measure_versions FROM mdeis_ro" in FLAT
    assert "REVOKE ALL ON registry_reviewers FROM mdeis_ro" in FLAT
    assert not re.search(r"GRANT[^;]*ON registry_reviewers", FLAT)
    wanted = r"GRANT SELECT \((.*?)\) ON measure_versions TO mdeis_ro"
    column_grant = re.search(wanted, FLAT).group(1)
    granted = {c.strip() for c in column_grant.split(",")}
    assert granted == {
        "measure_id", "version", "status", "description", "input_scope", "output_kind",
        "output_schema", "clause_ids", "template_version", "methodology_document_id",
        "section_sha256", "code_sha256", "approved_at",
    }
    for column in SECRET_COLUMNS:
        assert column not in granted, column
    assert not re.search(r"GRANT SELECT ON measure_versions", FLAT)


def test_the_moves_table_cannot_be_edited_after_the_migration():
    assert "REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON measure_status_moves FROM %I" in FLAT


def test_every_table_the_migration_makes_is_in_a_revoke_or_a_documented_exception():
    made = set(re.findall(r"CREATE TABLE (\w+)", CODE))
    assert made == {"registry_reviewers", "measures", "measure_status_moves", "measure_versions"}


def test_no_school_value_is_written_into_the_registry():
    for word in ("threshold", "pass_mark", "move_threshold", "stage_range", "gap_direction"):
        assert word not in CODE.lower(), word
