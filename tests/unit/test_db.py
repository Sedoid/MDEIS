"""Migration 001: the migration runner, the diary, the rulebook tables, the roles.

Marked `integration`: needs DATABASE_URL (a Postgres with pgvector, the app role).
Run on the server:  make test   (or: pytest -m integration)
"""

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from dbtools import make_learner, make_template, one, refused, run_migrate, vec
from psycopg import errors

MIGRATIONS = Path(__file__).resolve().parents[2] / "src" / "mdeis" / "db" / "migrations"

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- migrations
def schema_fingerprint(conn):
    rows = []
    for sql in (
        "SELECT table_name, column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns WHERE table_schema = 'public' ORDER BY 1, 2",
        "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) "
        "FROM pg_constraint WHERE connamespace = 'public'::regnamespace ORDER BY 1, 2",
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1",
        "SELECT tgrelid::regclass::text, tgname FROM pg_trigger "
        "WHERE NOT tgisinternal ORDER BY 1, 2",
        "SELECT proname FROM pg_proc WHERE pronamespace = 'public'::regnamespace ORDER BY 1",
    ):
        rows.extend(str(r) for r in conn.execute(sql).fetchall())
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def test_every_migration_file_is_recorded(conn):
    recorded = [r[0] for r in conn.execute("SELECT name FROM schema_migrations ORDER BY name")]
    assert recorded == sorted(p.name for p in MIGRATIONS.glob("*.sql"))


def test_running_the_migrations_again_changes_nothing(conn):
    before = schema_fingerprint(conn)
    out = subprocess.run(
        [sys.executable, "-m", "mdeis.db.migrate"],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        check=True,
    ).stdout
    assert "0 applied now" in out
    assert schema_fingerprint(conn) == before


def test_vector_extension_and_search_room(conn):
    assert one(conn, "SELECT count(*) FROM pg_extension WHERE extname = 'vector'") == 1
    for table in ("clauses", "method_chunks"):
        kind = one(
            conn,
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = %s::regclass AND attname = 'embedding'",
            (table,),
        )
        assert kind == "vector(768)", table
        index = one(
            conn,
            "SELECT indexdef FROM pg_indexes WHERE tablename = %s AND indexdef ILIKE '%%hnsw%%'",
            (table,),
        )
        assert "vector_cosine_ops" in index


def test_search_by_meaning_returns_the_nearest_rule(conn):
    name = make_template(conn)
    for i, clause in enumerate(["TR-1", "TR-2", "ST-1"]):
        conn.execute(
            "INSERT INTO clauses (version_name, clause_id, area, text, text_sha256, embedding) "
            "VALUES (%s, %s, %s, 'x', 'h', %s::vector)",
            (name, clause, clause[:2], vec(i)),
        )
    nearest = conn.execute(
        "SELECT clause_id FROM clauses WHERE version_name = %s "
        "ORDER BY embedding <=> %s::vector LIMIT 1",
        (name, vec(1)),
    ).fetchone()[0]
    assert nearest == "TR-2"


# --------------------------------------------------------------------------- ids
def test_id_formats(conn):
    assert one(conn, "SELECT mdeis_id('A-', 123, 6)") == "A-000123"
    assert one(conn, "SELECT mdeis_id('L-', 7, 4)") == "L-0007"
    # A number longer than the width is never cut short.
    assert one(conn, "SELECT mdeis_id('A-', 1234567, 6)") == "A-1234567"
    learner = make_learner(conn)
    assert re.fullmatch(r"L-\d{4,}", learner)
    record = one(
        conn,
        "INSERT INTO assessments (learner_id, subject, score) VALUES (%s, 'Maths', 14) "
        "RETURNING assessment_id",
        (learner,),
    )
    assert re.fullmatch(r"A-\d{6,}", record)


def test_a_missing_score_is_stored_as_missing_not_zero(conn):
    learner = make_learner(conn)
    score = conn.execute(
        "INSERT INTO assessments (learner_id, subject, score) VALUES (%s, 'Maths', NULL) "
        "RETURNING score",
        (learner,),
    ).fetchone()[0]
    assert score is None


# --------------------------------------------------------------------------- the diary
def log(conn, tool="test_tool", **extra):
    return conn.execute(
        "INSERT INTO action_log (tool, inputs, outputs, run_id, approver_id) "
        "VALUES (%s, '{\"a\": 1}', '{}', 'RUN-test', %s) RETURNING event_id, prev_hash, row_hash",
        (tool, extra.get("approver")),
    ).fetchone()


def test_diary_accepts_new_lines_and_chains_them(conn):
    first = log(conn, "one")
    second = log(conn, "two")
    assert second[0] > first[0]
    assert second[1] == first[2], "each line must carry the hash of the line before it"
    assert first[2] != second[2]
    assert conn.execute("SELECT * FROM verify_action_log()").fetchall() == []


def test_diary_ignores_ids_and_hashes_sent_by_the_caller(conn):
    row = conn.execute(
        "INSERT INTO action_log (event_id, tool, prev_hash, row_hash) "
        "VALUES (-5, 'forged', '\\x00', '\\x00') RETURNING event_id, prev_hash, row_hash"
    ).fetchone()
    assert row[0] != -5 and bytes(row[1]) != b"\x00" and bytes(row[2]) != b"\x00"
    assert conn.execute("SELECT * FROM verify_action_log()").fetchall() == []


def test_editing_the_diary_is_refused(conn):
    event = log(conn)[0]
    refused(
        conn,
        errors.InsufficientPrivilege,
        "UPDATE action_log SET tool = 'edited' WHERE event_id = %s",
        (event,),
    )
    refused(
        conn, errors.InsufficientPrivilege, "DELETE FROM action_log WHERE event_id = %s", (event,)
    )
    refused(conn, errors.InsufficientPrivilege, "TRUNCATE action_log")


def test_the_trigger_refuses_even_if_the_privilege_comes_back(conn):
    """Two locks on one door: take the first away and the second still holds."""
    event = log(conn)[0]
    conn.execute("GRANT UPDATE, DELETE, TRUNCATE ON action_log TO CURRENT_USER")
    for sql, params in (
        ("UPDATE action_log SET tool = 'edited' WHERE event_id = %s", (event,)),
        ("DELETE FROM action_log WHERE event_id = %s", (event,)),
        ("TRUNCATE action_log", ()),
    ):
        error = refused(conn, errors.RestrictViolation, sql, params)
        assert "append-only" in str(error)
    assert one(conn, "SELECT tool FROM action_log WHERE event_id = %s", (event,)) == "test_tool"


def test_a_change_made_by_getting_round_the_trigger_is_detected(conn):
    log(conn, "a")
    victim = log(conn, "b")[0]
    log(conn, "c")
    conn.execute("GRANT UPDATE ON action_log TO CURRENT_USER")
    conn.execute("ALTER TABLE action_log DISABLE TRIGGER action_log_no_change")
    conn.execute("UPDATE action_log SET tool = 'edited' WHERE event_id = %s", (victim,))
    bad = conn.execute("SELECT bad_event_id, reason FROM verify_action_log()").fetchall()
    assert bad == [(victim, "row was changed")]


def test_removing_a_line_from_the_chain_is_detected(conn):
    log(conn, "a")
    middle = log(conn, "b")[0]
    last = log(conn, "c")[0]
    conn.execute("GRANT DELETE ON action_log TO CURRENT_USER")
    conn.execute("ALTER TABLE action_log DISABLE TRIGGER action_log_no_change")
    conn.execute("DELETE FROM action_log WHERE event_id = %s", (middle,))
    bad = conn.execute("SELECT bad_event_id, reason FROM verify_action_log()").fetchall()
    assert bad == [(last, "broken link to the row before")]


def test_diary_rejects_an_unknown_outcome_and_an_empty_tool(conn):
    refused(
        conn, errors.CheckViolation, "INSERT INTO action_log (tool, outcome) VALUES ('t', 'maybe')"
    )
    refused(conn, errors.CheckViolation, "INSERT INTO action_log (tool) VALUES ('')")


# --------------------------------------------------------------------------- issues, answers
def make_issue(conn, learner, fingerprint="f1"):
    return one(
        conn,
        "INSERT INTO issues (learner_id, fingerprint, kind, severity) "
        "VALUES (%s, %s, 'missing_term', 'blocking') RETURNING issue_id",
        (learner, fingerprint),
    )


def test_the_same_issue_is_not_stored_twice(conn):
    learner = make_learner(conn)
    make_issue(conn, learner)
    refused(
        conn,
        errors.UniqueViolation,
        "INSERT INTO issues (learner_id, fingerprint, kind, severity) "
        "VALUES (%s, 'f1', 'missing_term', 'blocking')",
        (learner,),
    )
    refused(
        conn,
        errors.CheckViolation,
        "INSERT INTO issues (learner_id, fingerprint, kind, severity) "
        "VALUES (%s, 'f2', 'x', 'fatal')",
        (learner,),
    )


def test_an_answer_needs_a_named_person_and_is_a_record(conn):
    issue = make_issue(conn, make_learner(conn))
    refused(
        conn,
        errors.CheckViolation,
        "INSERT INTO resolutions (issue_id, resolution, approver_id) VALUES (%s, 'keep_as_is', '')",
        (issue,),
    )
    refused(
        conn,
        errors.NotNullViolation,
        "INSERT INTO resolutions (issue_id, resolution) VALUES (%s, 'keep_as_is')",
        (issue,),
    )
    refused(
        conn,
        errors.CheckViolation,
        "INSERT INTO resolutions (issue_id, resolution, approver_id) "
        "VALUES (%s, 'delete_everything', 'u1')",
        (issue,),
    )
    # A value goes with use_value, and only with use_value.
    refused(
        conn,
        errors.CheckViolation,
        "INSERT INTO resolutions (issue_id, resolution, approver_id) "
        "VALUES (%s, 'use_value', 'u1')",
        (issue,),
    )
    refused(
        conn,
        errors.CheckViolation,
        "INSERT INTO resolutions (issue_id, resolution, value, approver_id) "
        "VALUES (%s, 'keep_as_is', 12, 'u1')",
        (issue,),
    )
    rid = one(
        conn,
        "INSERT INTO resolutions (issue_id, resolution, value, approver_id) "
        "VALUES (%s, 'use_value', 12, 'u1') RETURNING resolution_id",
        (issue,),
    )
    refused(
        conn,
        errors.RestrictViolation,
        "UPDATE resolutions SET note = 'changed' WHERE resolution_id = %s",
        (rid,),
    )
    refused(
        conn, errors.RestrictViolation, "DELETE FROM resolutions WHERE resolution_id = %s", (rid,)
    )


# --------------------------------------------------------------------------- rulebook versions
def test_a_loaded_rulebook_version_never_changes(conn):
    name = make_template(conn, "t-frozen", active=True)
    refused(
        conn,
        errors.RestrictViolation,
        "UPDATE templates SET content = '{\"x\": 1}' WHERE version_name = %s",
        (name,),
    )
    refused(
        conn,
        errors.RestrictViolation,
        "UPDATE templates SET sha256 = repeat('c', 64) WHERE version_name = %s",
        (name,),
    )
    refused(
        conn, errors.RestrictViolation, "DELETE FROM templates WHERE version_name = %s", (name,)
    )
    conn.execute("UPDATE templates SET active = false WHERE version_name = %s", (name,))  # allowed


def test_only_one_rulebook_version_is_active(conn):
    make_template(conn, "t-one", active=True)
    refused(
        conn,
        errors.UniqueViolation,
        "INSERT INTO templates (version_name, file_name, sha256, template_id, content, active) "
        "VALUES ('t-two', 't-two.yaml', repeat('a', 64), 'tid', '{}', true)",
    )


def test_a_version_name_is_a_plain_file_name(conn):
    for bad in ("", " v1", "v1/../x", "-v1"):
        refused(
            conn,
            errors.CheckViolation,
            "INSERT INTO templates (version_name, file_name, sha256, template_id, content) "
            "VALUES (%s, 'f', repeat('a', 64), 'tid', '{}')",
            (bad,),
        )
    refused(
        conn,
        errors.CheckViolation,
        "INSERT INTO templates (version_name, file_name, sha256, template_id, content) "
        "VALUES ('v-ok', 'f', 'not-a-hash', 'tid', '{}')",
    )


def test_rule_sentences_never_change_but_their_vector_can_be_filled_in(conn):
    name = make_template(conn)
    conn.execute(
        "INSERT INTO clauses (version_name, clause_id, area, text, text_sha256) "
        "VALUES (%s, 'TR-1', 'TR', 'old words', 'h')",
        (name,),
    )
    refused(
        conn,
        errors.RestrictViolation,
        "UPDATE clauses SET text = 'new words' WHERE version_name = %s",
        (name,),
    )
    refused(conn, errors.RestrictViolation, "DELETE FROM clauses WHERE version_name = %s", (name,))
    conn.execute(
        "UPDATE clauses SET embedding = %s::vector, embedding_model = 'm' WHERE version_name = %s",
        (vec(3), name),
    )
    refused(
        conn,
        errors.CheckViolation,
        "INSERT INTO clauses (version_name, clause_id, area, text, text_sha256) "
        "VALUES (%s, 'bad id', 'TR', 't', 'h')",
        (name,),
    )


def test_the_same_rule_id_can_exist_in_two_versions(conn):
    for name in (make_template(conn, "t-a"), make_template(conn, "t-b")):
        conn.execute(
            "INSERT INTO clauses (version_name, clause_id, area, text, text_sha256) "
            "VALUES (%s, 'TR-1', 'TR', 't', 'h')",
            (name,),
        )
    refused(
        conn,
        errors.UniqueViolation,
        "INSERT INTO clauses (version_name, clause_id, area, text, text_sha256) "
        "VALUES ('t-a', 'TR-1', 'TR', 't', 'h')",
    )


# --------------------------------------------------------------------------- reports
def draft(conn, learner, **cols):
    base = {"learner_id": learner, "body": "text"}
    base.update(cols)
    names = ", ".join(base)
    marks = ", ".join(["%s"] * len(base))
    return one(
        conn,
        f"INSERT INTO reports ({names}) VALUES ({marks}) RETURNING report_id",
        list(base.values()),
    )


def test_a_report_cannot_be_released_without_a_person_a_passed_check_and_a_version(conn):
    learner = make_learner(conn)
    template = make_template(conn, "t-rep", active=True)
    report = draft(conn, learner, template_version=template)
    attempts = [
        "SET status = 'released'",
        "SET status = 'released', released_by_id = 'u1', released_at = now()",
        "SET status = 'released', released_by_id = 'u1', citations_ok = true",
    ]
    for change in attempts:
        refused(
            conn, errors.CheckViolation, f"UPDATE reports {change} WHERE report_id = %s", (report,)
        )
    unpinned = draft(conn, learner, citations_ok=True)
    refused(
        conn,
        errors.CheckViolation,
        "UPDATE reports SET status = 'released', released_by_id = 'u1', released_at = now() "
        "WHERE report_id = %s",
        (unpinned,),
    )
    conn.execute(
        "UPDATE reports SET status = 'released', released_by_id = 'u1', "
        "released_at = now(), citations_ok = true WHERE report_id = %s",
        (report,),
    )


def test_a_released_report_is_never_changed_or_deleted(conn):
    learner = make_learner(conn)
    template = make_template(conn, "t-rel", active=True)
    report = draft(
        conn,
        learner,
        template_version=template,
        citations_ok=True,
        status="released",
        released_by_id="u1",
        released_at="2026-10-12T10:00:00Z",
    )
    refused(
        conn,
        errors.RestrictViolation,
        "UPDATE reports SET body = 'rewritten' WHERE report_id = %s",
        (report,),
    )
    refused(conn, errors.RestrictViolation, "DELETE FROM reports WHERE report_id = %s", (report,))
    # A pending draft can still be replaced.
    pending = draft(conn, learner)
    conn.execute("UPDATE reports SET body = 'better' WHERE report_id = %s", (pending,))
    conn.execute("DELETE FROM reports WHERE report_id = %s", (pending,))


# --------------------------------------------------------------------------- keys and rights
def tables(conn):
    return [
        r[0]
        for r in conn.execute(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename <> 'schema_migrations'"
        )
    ]


def can(conn, role, table, privilege):
    return one(conn, "SELECT has_table_privilege(%s, %s, %s)", (role, f"public.{table}", privilege))


def test_the_borrowed_tool_can_only_read(conn, roles):
    role = roles["ro"]
    assert one(conn, "SELECT rolcanlogin FROM pg_roles WHERE rolname = %s", (role,))
    settings = one(conn, "SELECT rolconfig FROM pg_roles WHERE rolname = %s", (role,))
    assert "default_transaction_read_only=on" in settings
    # It reads every table except the diary, and `learners` only by column: the id, the school, the
    # card's ID number and, since migration 005, the name (so a learner can be found by name).
    not_whole = {"action_log", "learners"}
    for table in tables(conn):
        assert can(conn, role, table, "SELECT") == (table not in not_whole), table
        for write in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            assert not can(conn, role, table, write), (table, write)
    column = "SELECT has_column_privilege(%s, 'public.learners', %s, 'SELECT')"
    assert one(conn, column, (role, "learner_id"))
    assert one(conn, column, (role, "school_code"))
    assert one(conn, column, (role, "display_name"))
    assert one(conn, column, (role, "school_ref"))


def test_the_toolbox_can_write_but_not_edit_the_diary(conn, roles):
    role = roles["app"]
    assert can(conn, role, "learners", "INSERT") and can(conn, role, "assessments", "UPDATE")
    assert can(conn, role, "action_log", "INSERT") and can(conn, role, "action_log", "SELECT")
    for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
        assert not can(conn, role, "action_log", privilege), privilege


def test_the_upload_page_key_can_only_add_and_read_documents(conn, roles):
    role = roles["portal"]
    allowed = {("documents", "INSERT"), ("documents", "SELECT"), ("document_issues", "SELECT")}
    for table in tables(conn):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            assert can(conn, role, table, privilege) == ((table, privilege) in allowed), (
                table,
                privilege,
            )
    assert one(conn, "SELECT has_sequence_privilege(%s, 'document_seq', 'USAGE')", (role,))


def test_the_side_helper_n8n_has_no_rights_on_learner_data_yet(conn, roles):
    """n8n_auto gets read access to named views later (migration 004). Until then, nothing."""
    for table in tables(conn):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert not can(conn, roles["auto"], table, privilege), (table, privilege)


def test_the_agent_and_n8n_cabinets_hold_no_learner_data(conn, roles):
    for key in ("orchestrator", "n8n", "auto"):
        mdeis = one(
            conn, "SELECT has_database_privilege(%s, %s, 'CONNECT')", (roles[key], conn.info.dbname)
        )
        if key == "auto":
            assert mdeis  # n8n_auto may connect to read its views, and nothing else
        else:
            assert not mdeis, f"{roles[key]} must not connect to the learner database"
    assert one(
        conn,
        "SELECT has_database_privilege(%s, %s, 'CONNECT')",
        (roles["orchestrator"], os.environ.get("ORCHESTRATOR_DB_NAME", "orchestrator")),
    )


# --------------------------------------------------------------------------- the move-in script
def test_all_migrations_build_the_schema_from_nothing_and_a_second_run_does_nothing(
    conn, scratch_schema
):
    name, url = scratch_schema
    files = sorted(p.name for p in MIGRATIONS.glob("*.sql"))
    first = run_migrate(url)
    assert first.returncode == 0, first.stderr
    assert f"{len(files)} applied now, 0 already applied" in first.stdout
    count = one(
        conn,
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s",
        (name,),
    )
    second = run_migrate(url)
    assert second.returncode == 0, second.stderr
    assert f"0 applied now, {len(files)} already applied" in second.stdout
    assert (
        one(conn, "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s", (name,))
        == count
    )
    recorded = [
        r[0] for r in conn.execute(f'SELECT name FROM "{name}".schema_migrations ORDER BY name')
    ]
    assert recorded == files


def test_a_migration_edited_after_it_was_applied_is_refused(conn, scratch_schema, tmp_path):
    _, url = scratch_schema
    (tmp_path / "001_a.sql").write_text("CREATE TABLE a_table (id integer);\n")
    assert run_migrate(url, tmp_path).returncode == 0
    (tmp_path / "001_a.sql").write_text("CREATE TABLE a_table (id integer, extra text);\n")
    again = run_migrate(url, tmp_path)
    assert again.returncode == 1
    assert "001_a.sql was changed after it was applied" in again.stderr


def test_a_broken_migration_leaves_nothing_half_done(conn, scratch_schema, tmp_path):
    name, url = scratch_schema
    (tmp_path / "001_a.sql").write_text("CREATE TABLE ok_table (id integer);\n")
    (tmp_path / "002_b.sql").write_text("CREATE TABLE half_table (id integer);\nSELECT 1 / 0;\n")
    result = run_migrate(url, tmp_path)
    assert result.returncode != 0
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (name,)
        )
    }
    assert "ok_table" in tables and "half_table" not in tables
    recorded = [r[0] for r in conn.execute(f'SELECT name FROM "{name}".schema_migrations')]
    assert recorded == ["001_a.sql"]
