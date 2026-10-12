-- Role and privilege checks for the `mdeis` database. Task T-003 / T-030 / T-038 (docs/CONTAINERS.md section 6).
--
-- Run it as the Postgres superuser, after `make migrate`, from the repo root:
--
--   docker exec -i postgres-mdeis sh -c \
--     'psql -U "$POSTGRES_USER" -d "$MDEIS_DB_NAME" -v ON_ERROR_STOP=1 -f -' \
--     < deploy/postgres/checks/roles.sql
--
-- (`postgres-mdeis` is `postgres-` plus STACK_SUFFIX from .env.)
--
-- It changes nothing: everything runs inside one transaction that is rolled back at the end. It
-- reads the role and database names from the container's environment, so renamed roles still work.
-- Output: one PASS or FAIL line per check, then a summary. It exits non-zero if any check fails.

\set QUIET on
\set ON_ERROR_STOP on
\getenv app_role MDEIS_APP_USER
\getenv ro_role MDEIS_RO_USER
\getenv portal_role PORTAL_DB_USER
\getenv auto_role N8N_AUTO_DB_USER
\getenv orch_role ORCHESTRATOR_DB_USER
\getenv n8n_role N8N_DB_USER
\getenv mdeis_db MDEIS_DB_NAME
\getenv orch_db ORCHESTRATOR_DB_NAME
\getenv n8n_db N8N_DB_NAME

BEGIN;
\o /dev/null

CREATE TEMP TABLE results (n serial, ok boolean NOT NULL, what text NOT NULL);

-- Try one statement as one role. Returns 'ok' or the SQLSTATE that refused it. The savepoint that
-- plpgsql opens around the EXCEPTION block undoes the statement and the role switch on failure.
CREATE FUNCTION pg_temp.try(role_name text, stmt text) RETURNS text LANGUAGE plpgsql AS $$
BEGIN
    BEGIN
        EXECUTE format('SET LOCAL ROLE %I', role_name);
        EXECUTE stmt;
        EXECUTE 'RESET ROLE';
        RETURN 'ok';
    EXCEPTION WHEN OTHERS THEN
        RETURN SQLSTATE;
    END;
END $$;

CREATE FUNCTION pg_temp.allowed(what text, role_name text, stmt text) RETURNS void LANGUAGE plpgsql AS $$
DECLARE r text := pg_temp.try(role_name, stmt);
BEGIN
    INSERT INTO pg_temp.results (ok, what) VALUES (r = 'ok', format('%s may: %s (%s)', role_name, what, r));
END $$;

-- "Denied" means refused for any reason except a mistake in the statement itself (syntax, missing table).
CREATE FUNCTION pg_temp.denied(what text, role_name text, stmt text) RETURNS void LANGUAGE plpgsql AS $$
DECLARE r text := pg_temp.try(role_name, stmt);
BEGIN
    INSERT INTO pg_temp.results (ok, what)
    VALUES (r IN ('42501', '25006', 'P0001'), format('%s may NOT: %s (%s)', role_name, what, r));
END $$;

CREATE FUNCTION pg_temp.is_true(what text, cond boolean) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO pg_temp.results (ok, what) VALUES (coalesce(cond, false), what);
END $$;

-- A document row to read back. It is rolled back with everything else.
INSERT INTO documents (document_id, kind, original_name, stored_name, sha256, size_bytes, uploader_id, uploader_name)
VALUES ('D-999999', 'results', 'amara.pdf', 'stored-check', repeat('a', 64), 1, 'u1', 'Uploader Name');
INSERT INTO learners (learner_id, school_code, school_ref, display_name) VALUES ('L-9999', 'CHK', 'XX/0000/0000', 'Check Learner');
INSERT INTO action_log (tool) VALUES ('roles_check');

-- ---- the app role: the toolbox ---------------------------------------------------------------
SELECT pg_temp.allowed('add a diary line', :'app_role', $$INSERT INTO action_log (tool) VALUES ('x')$$);
SELECT pg_temp.allowed('read the diary', :'app_role', $$SELECT count(*) FROM action_log$$);
SELECT pg_temp.denied('edit a diary line', :'app_role', $$UPDATE action_log SET tool = 'y'$$);
SELECT pg_temp.denied('delete a diary line', :'app_role', $$DELETE FROM action_log$$);
SELECT pg_temp.denied('empty the diary', :'app_role', $$TRUNCATE action_log$$);
SELECT pg_temp.allowed('write learners', :'app_role', $$UPDATE learners SET display_name = display_name$$);

-- ---- the read-only role: the borrowed server -------------------------------------------------
SELECT pg_temp.allowed('read learners', :'ro_role', $$SELECT * FROM learners$$);
SELECT pg_temp.allowed('read documents', :'ro_role', $$SELECT * FROM documents$$);
SELECT pg_temp.allowed('read scores and the rule sentences', :'ro_role',
    $$SELECT count(*) FROM assessments, clauses, templates, reports, issues, runs, action_log, report_pin_status$$);
SELECT pg_temp.denied('add a row', :'ro_role', $$INSERT INTO runs DEFAULT VALUES$$);
SELECT pg_temp.denied('edit a row', :'ro_role', $$UPDATE issues SET detail = 'x'$$);
SELECT pg_temp.denied('create a table', :'ro_role', $$CREATE TABLE ro_probe (x int)$$);
SELECT pg_temp.denied('edit a diary line', :'ro_role', $$UPDATE action_log SET tool = 'y'$$);

-- ---- the portal role: the upload page --------------------------------------------------------
SELECT pg_temp.allowed('add an upload', :'portal_role',
    $$INSERT INTO documents (document_id, kind, original_name, stored_name, sha256, size_bytes, uploader_id, uploader_name)
      VALUES ('D-999998', 'results', 'b.pdf', 'stored-check-2', repeat('b', 64), 1, 'u1', 'U')$$);
SELECT pg_temp.allowed('read the upload list', :'portal_role', $$SELECT * FROM documents$$);
SELECT pg_temp.allowed('read problems with a document', :'portal_role', $$SELECT * FROM document_issues$$);
SELECT pg_temp.is_true(format('%s may take the next document number', :'portal_role'),
    has_sequence_privilege(:'portal_role', 'document_seq', 'USAGE'));
SELECT pg_temp.denied('change an upload', :'portal_role', $$UPDATE documents SET status = 'done'$$);
SELECT pg_temp.denied('delete an upload', :'portal_role', $$DELETE FROM documents$$);
SELECT pg_temp.denied('add a problem to a document', :'portal_role',
    $$INSERT INTO document_issues (document_id, kind) VALUES ('D-999999', 'x')$$);
SELECT pg_temp.denied('read learners', :'portal_role', $$SELECT 1 FROM learners$$);
SELECT pg_temp.denied('read scores', :'portal_role', $$SELECT 1 FROM assessments$$);
SELECT pg_temp.denied('read reports', :'portal_role', $$SELECT 1 FROM reports$$);
SELECT pg_temp.denied('read the diary', :'portal_role', $$SELECT 1 FROM action_log$$);
SELECT pg_temp.denied('write the diary', :'portal_role', $$INSERT INTO action_log (tool) VALUES ('x')$$);

-- ---- n8n's read-only role: nothing yet (its views arrive with T-046) -------------------------
SELECT pg_temp.denied('read learners', :'auto_role', $$SELECT 1 FROM learners$$);
SELECT pg_temp.denied('read reports', :'auto_role', $$SELECT 1 FROM reports$$);
SELECT pg_temp.denied('read the diary', :'auto_role', $$SELECT 1 FROM action_log$$);
SELECT pg_temp.denied('add a row', :'auto_role', $$INSERT INTO runs DEFAULT VALUES$$);

-- ---- who can open which database -------------------------------------------------------------
SELECT pg_temp.is_true(format('%s can open %s', :'app_role', :'mdeis_db'), has_database_privilege(:'app_role', :'mdeis_db', 'CONNECT'));
SELECT pg_temp.is_true(format('%s can open %s', :'ro_role', :'mdeis_db'), has_database_privilege(:'ro_role', :'mdeis_db', 'CONNECT'));
SELECT pg_temp.is_true(format('%s can open %s', :'portal_role', :'mdeis_db'), has_database_privilege(:'portal_role', :'mdeis_db', 'CONNECT'));
SELECT pg_temp.is_true(format('%s can NOT open %s (the helper has its own cabinet)', :'orch_role', :'mdeis_db'),
    NOT has_database_privilege(:'orch_role', :'mdeis_db', 'CONNECT'));
SELECT pg_temp.is_true(format('%s can NOT open %s', :'n8n_role', :'mdeis_db'),
    NOT has_database_privilege(:'n8n_role', :'mdeis_db', 'CONNECT'));
SELECT pg_temp.is_true(format('%s can open %s', :'orch_role', :'orch_db'), has_database_privilege(:'orch_role', :'orch_db', 'CONNECT'));
SELECT pg_temp.is_true(format('%s can NOT open %s', :'app_role', :'orch_db'), NOT has_database_privilege(:'app_role', :'orch_db', 'CONNECT'));
SELECT pg_temp.is_true(format('%s can NOT open %s', :'ro_role', :'orch_db'), NOT has_database_privilege(:'ro_role', :'orch_db', 'CONNECT'));
SELECT pg_temp.is_true(format('%s can NOT open %s', :'portal_role', :'orch_db'), NOT has_database_privilege(:'portal_role', :'orch_db', 'CONNECT'));
SELECT pg_temp.is_true(format('%s can NOT open %s', :'auto_role', :'n8n_db'), NOT has_database_privilege(:'auto_role', :'n8n_db', 'CONNECT'));
SELECT pg_temp.is_true('the two read-only roles are read-only even if a grant is added by mistake',
    (SELECT bool_and(rolconfig @> ARRAY['default_transaction_read_only=on'])
     FROM pg_roles WHERE rolname IN (:'ro_role', :'auto_role')));

-- ---- the diary and the search room -----------------------------------------------------------
SELECT pg_temp.is_true('the diary has its three triggers (chain, no edit or delete, no truncate)',
    (SELECT count(*) FROM pg_trigger WHERE tgrelid = 'action_log'::regclass AND NOT tgisinternal) = 3);
SELECT pg_temp.is_true('the diary hash chain is intact', NOT EXISTS (SELECT 1 FROM verify_action_log()));
SELECT pg_temp.is_true('the vector extension is installed', EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector'));
SELECT pg_temp.is_true('rule sentences hold 768 numbers',
    (SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE attrelid = 'clauses'::regclass AND attname = 'embedding') = 'vector(768)');
SELECT pg_temp.is_true('methodology pieces hold 768 numbers',
    (SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE attrelid = 'method_chunks'::regclass AND attname = 'embedding') = 'vector(768)');
SELECT pg_temp.is_true('both vector columns have a nearest-neighbour index',
    (SELECT count(*) FROM pg_indexes WHERE indexdef ILIKE '%USING hnsw%' AND tablename IN ('clauses', 'method_chunks')) = 2);
SELECT pg_temp.is_true('both migrations are recorded', (SELECT count(*) FROM schema_migrations) >= 2);

-- ---- report ----------------------------------------------------------------------------------
\o
\set QUIET off
\pset format unaligned
\pset tuples_only on
SELECT CASE WHEN ok THEN 'PASS  ' ELSE 'FAIL  ' END || what FROM results ORDER BY n;
SELECT format('%s checks, %s passed, %s FAILED', count(*), count(*) FILTER (WHERE ok), count(*) FILTER (WHERE NOT ok)) FROM results;
SELECT count(*) FILTER (WHERE NOT ok) AS failed FROM results \gset
ROLLBACK;
\if :failed
  \echo 'roles.sql: FAILED'
  -- A deliberate error: with ON_ERROR_STOP, psql then exits non-zero.
  DO $$ BEGIN RAISE EXCEPTION 'roles.sql: some role checks failed'; END $$;
\else
  \echo 'roles.sql: all checks passed'
\endif

-- ---- the helper's own cabinet holds no learner data (a separate connection) ------------------
\connect :orch_db
\pset format unaligned
\pset tuples_only on
SELECT CASE WHEN count(*) = 0 THEN 'PASS  the orchestrator database has none of the MDEIS tables'
            ELSE 'FAIL  the orchestrator database holds MDEIS tables: ' || string_agg(tablename, ', ') END
FROM pg_tables WHERE schemaname = 'public' AND tablename IN
    ('learners', 'assessments', 'issues', 'resolutions', 'reports', 'action_log', 'documents', 'clauses');
