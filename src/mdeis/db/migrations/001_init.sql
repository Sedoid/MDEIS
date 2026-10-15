-- Migration 001: the core tables (T-003). SPEC.md section 6; IDs from SPEC.md section 4.
--
-- Rules for every migration in this folder:
--   * Plain SQL, applied once, in file order, by `python -m mdeis.db.migrate` as the app role.
--   * Never edit a migration that has been applied anywhere. Add the next number instead.
--   * No school value lives here: no scale, threshold, range, coefficient or term count. Those
--     are in the criteria template. The schema only says what shape a value has.

-- The init script already created this as superuser. IF NOT EXISTS makes it a no-op for us.
CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------------------------
-- IDs. 'A-' plus 6 digits, and so on. Sequences give the number; this function writes it out.
-- It never cuts a number short (lpad would): past the width it simply gets longer.
-- ---------------------------------------------------------------------------------------------
CREATE FUNCTION mdeis_id(prefix text, n bigint, width integer) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS
$$ SELECT prefix || repeat('0', greatest(width - length(n::text), 0)) || n::text $$;

CREATE SEQUENCE learner_seq;
CREATE SEQUENCE assessment_seq;
CREATE SEQUENCE issue_seq;
CREATE SEQUENCE report_seq;

-- ---------------------------------------------------------------------------------------------
-- Records
-- ---------------------------------------------------------------------------------------------
CREATE TABLE learners (
    learner_id  text PRIMARY KEY DEFAULT mdeis_id('L-', nextval('learner_seq'), 4)
                CHECK (learner_id ~ '^L-[0-9]{4,}$'),
    school_code text NOT NULL CHECK (school_code <> ''),
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- One row is one term score for one subject. A NULL score means the card showed no score for
-- that term. It is stored as missing and is never turned into zero.
CREATE TABLE assessments (
    assessment_id text PRIMARY KEY DEFAULT mdeis_id('A-', nextval('assessment_seq'), 6)
                  CHECK (assessment_id ~ '^A-[0-9]{6,}$'),
    learner_id    text NOT NULL REFERENCES learners,
    subject       text NOT NULL CHECK (subject <> ''),
    score         numeric,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX assessments_learner_idx ON assessments (learner_id);

-- A problem found in the data. `fingerprint` is a hash of kind, learner, subject and records, so
-- running the check again finds the same issue instead of adding a second copy.
CREATE TABLE issues (
    issue_id    text PRIMARY KEY DEFAULT mdeis_id('I-', nextval('issue_seq'), 6)
                CHECK (issue_id ~ '^I-[0-9]{6,}$'),
    learner_id  text NOT NULL REFERENCES learners,
    fingerprint text NOT NULL UNIQUE,
    kind        text NOT NULL CHECK (kind <> ''),
    severity    text NOT NULL CHECK (severity IN ('blocking', 'warning')),
    subject     text,
    record_ids  text[] NOT NULL DEFAULT '{}',
    detail      text NOT NULL DEFAULT '',
    question    text NOT NULL DEFAULT '',
    run_id      text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX issues_learner_idx ON issues (learner_id);

-- The approver's answers. Append only: a changed mind is a new row, and the latest row wins.
-- The approver comes from the verified session, never from a tool argument.
CREATE TABLE resolutions (
    resolution_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    issue_id      text NOT NULL REFERENCES issues,
    resolution    text NOT NULL
                  CHECK (resolution IN ('confirm_absent', 'exclude_record', 'use_value', 'keep_as_is')),
    value         numeric,
    note          text,
    approver_id   text NOT NULL CHECK (approver_id <> ''),
    approver_name text,
    run_id        text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT value_only_for_use_value CHECK ((resolution = 'use_value') = (value IS NOT NULL))
);
CREATE INDEX resolutions_issue_idx ON resolutions (issue_id, resolution_id);

-- ---------------------------------------------------------------------------------------------
-- Rulebook versions and their rule sentences.
-- A version is known by its file name (v1, v2-strict). Once loaded, a version never changes;
-- loading a changed file under the same name is refused by the loader and by the trigger below.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE templates (
    version_name       text PRIMARY KEY CHECK (version_name ~ '^[A-Za-z0-9][A-Za-z0-9._-]*$'),
    file_name          text NOT NULL,
    sha256             text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    template_id        text NOT NULL,
    declared_version   text,
    language           text,
    institution_code   text,
    institution_name   text,
    methodology_version text,
    start_date         date,
    review_date        date,
    approved_by        text,
    content            jsonb NOT NULL,
    -- The newest loaded version becomes the one new work uses. Only one can be active.
    active             boolean NOT NULL DEFAULT false,
    loaded_by          text,
    loaded_at          timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX templates_one_active ON templates ((true)) WHERE active;

CREATE TABLE clauses (
    version_name    text NOT NULL REFERENCES templates,
    clause_id       text NOT NULL CHECK (clause_id ~ '^[A-Z]{2}-[0-9]+$'),
    area            text NOT NULL,
    text            text NOT NULL,
    method_ref      text,
    params          jsonb NOT NULL DEFAULT '{}',
    text_sha256     text NOT NULL,
    -- 768 numbers from nomic-embed-text-v1.5 (EMBED_DIM in .env). NULL until embedded.
    embedding       vector(768),
    embedding_model text,
    PRIMARY KEY (version_name, clause_id)
);
CREATE INDEX clauses_embedding_idx ON clauses USING hnsw (embedding vector_cosine_ops);

-- ---------------------------------------------------------------------------------------------
-- Runs, reports, and the diary
-- ---------------------------------------------------------------------------------------------
CREATE TABLE runs (
    run_id             text PRIMARY KEY CHECK (run_id ~ '^RUN-.+'),
    requested_by_id    text,
    requested_by_name  text,
    mode               text,
    model              text,
    status             text NOT NULL DEFAULT 'running'
                       CHECK (status IN ('running', 'done', 'failed', 'refused')),
    started_at         timestamptz NOT NULL DEFAULT now(),
    finished_at        timestamptz
);

CREATE TABLE reports (
    report_id        text PRIMARY KEY DEFAULT mdeis_id('R-', nextval('report_seq'), 6)
                     CHECK (report_id ~ '^R-[0-9]{6,}$'),
    learner_id       text NOT NULL REFERENCES learners,
    run_id           text,
    status           text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'released')),
    body             text NOT NULL,
    -- Numbers, clause ids, record ids and result_hash exactly as the tools returned them.
    findings         jsonb NOT NULL DEFAULT '{}',
    -- Set true by check_citations, in the tool, never by the model.
    citations_ok     boolean NOT NULL DEFAULT false,
    released_by_id   text,
    released_by_name text,
    released_at      timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    -- The gate in the database too: nothing is released without a person, a time and a passed check.
    CONSTRAINT released_needs_approver_and_check CHECK (
        status <> 'released'
        OR (released_by_id IS NOT NULL AND released_at IS NOT NULL AND citations_ok)
    )
);
CREATE INDEX reports_learner_idx ON reports (learner_id);

-- The diary. Every tool call, allowed or refused. Append only (see below).
-- Each row also stores a hash of itself and of the row before it, so an edit made by someone who
-- gets around the trigger (a superuser, a dropped trigger) still shows up in verify_action_log().
CREATE SEQUENCE action_log_seq;

CREATE TABLE action_log (
    event_id      bigint PRIMARY KEY,
    at            timestamptz NOT NULL DEFAULT now(),
    run_id        text,
    tool          text NOT NULL CHECK (tool <> ''),
    inputs        jsonb NOT NULL DEFAULT '{}',
    outputs       jsonb NOT NULL DEFAULT '{}',
    outcome       text NOT NULL DEFAULT 'ok' CHECK (outcome IN ('ok', 'refused', 'blocked', 'error')),
    approver_id   text,
    approver_name text,
    prev_hash     bytea NOT NULL,
    row_hash      bytea NOT NULL
);
CREATE INDEX action_log_run_idx ON action_log (run_id);

CREATE FUNCTION action_log_hash(
    p_event_id bigint, p_at timestamptz, p_run_id text, p_tool text, p_inputs jsonb,
    p_outputs jsonb, p_outcome text, p_approver_id text, p_approver_name text, p_prev bytea
) RETURNS bytea LANGUAGE sql IMMUTABLE AS
$$
    SELECT sha256(
        convert_to(concat_ws('|',
            p_event_id::text,
            to_char(p_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
            coalesce(p_run_id, '<null>'), p_tool, p_inputs::text, p_outputs::text, p_outcome,
            coalesce(p_approver_id, '<null>'), coalesce(p_approver_name, '<null>')), 'UTF8')
        || p_prev)
$$;

-- Runs before every INSERT. Takes a lock so two writers cannot both extend the same row, then
-- sets the event number and both hashes itself. Whatever the caller sent for them is ignored.
CREATE FUNCTION action_log_chain() RETURNS trigger LANGUAGE plpgsql AS
$$
DECLARE
    last_hash bytea;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('mdeis.action_log'));
    SELECT row_hash INTO last_hash FROM action_log ORDER BY event_id DESC LIMIT 1;
    NEW.event_id  := nextval('action_log_seq');
    NEW.prev_hash := coalesce(last_hash, sha256(convert_to('mdeis action log genesis', 'UTF8')));
    NEW.row_hash  := action_log_hash(NEW.event_id, NEW.at, NEW.run_id, NEW.tool, NEW.inputs,
                                     NEW.outputs, NEW.outcome, NEW.approver_id, NEW.approver_name,
                                     NEW.prev_hash);
    RETURN NEW;
END
$$;
CREATE TRIGGER action_log_chain BEFORE INSERT ON action_log
    FOR EACH ROW EXECUTE FUNCTION action_log_chain();

CREATE FUNCTION action_log_refuse() RETURNS trigger LANGUAGE plpgsql AS
$$
BEGIN
    RAISE EXCEPTION 'action_log is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'restrict_violation';
END
$$;
CREATE TRIGGER action_log_no_change BEFORE UPDATE OR DELETE ON action_log
    FOR EACH ROW EXECUTE FUNCTION action_log_refuse();
CREATE TRIGGER action_log_no_truncate BEFORE TRUNCATE ON action_log
    FOR EACH STATEMENT EXECUTE FUNCTION action_log_refuse();

-- Second lock on the same door: the app role does not even hold the privileges.
REVOKE UPDATE, DELETE, TRUNCATE ON action_log FROM PUBLIC;
DO $$
BEGIN
    EXECUTE format('REVOKE UPDATE, DELETE, TRUNCATE ON action_log FROM %I', current_user);
END
$$;

-- Returns no rows when the diary is intact, else the first event whose hash or link is wrong.
CREATE FUNCTION verify_action_log() RETURNS TABLE (bad_event_id bigint, reason text)
LANGUAGE sql STABLE AS
$$
    SELECT s.event_id,
           CASE WHEN s.prev_hash IS DISTINCT FROM
                     coalesce(s.lag_hash, sha256(convert_to('mdeis action log genesis', 'UTF8')))
                THEN 'broken link to the row before'
                ELSE 'row was changed' END
    FROM (SELECT l.*, lag(l.row_hash) OVER (ORDER BY l.event_id) AS lag_hash FROM action_log l) s
    WHERE s.prev_hash IS DISTINCT FROM
              coalesce(s.lag_hash, sha256(convert_to('mdeis action log genesis', 'UTF8')))
       OR s.row_hash IS DISTINCT FROM
              action_log_hash(s.event_id, s.at, s.run_id, s.tool, s.inputs, s.outputs, s.outcome,
                              s.approver_id, s.approver_name, s.prev_hash)
    ORDER BY s.event_id
    LIMIT 1
$$;

-- ---------------------------------------------------------------------------------------------
-- Things that must not change once written
-- ---------------------------------------------------------------------------------------------
-- A row may only change in the columns named in the trigger's arguments. DELETE is refused.
CREATE FUNCTION mdeis_freeze() RETURNS trigger LANGUAGE plpgsql AS
$$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '% rows cannot be deleted', TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
    END IF;
    -- With no arguments TG_ARGV can come through as NULL, so it is turned into an empty list.
    IF (to_jsonb(OLD) - coalesce(TG_ARGV, '{}'::text[]))
       IS DISTINCT FROM (to_jsonb(NEW) - coalesce(TG_ARGV, '{}'::text[])) THEN
        RAISE EXCEPTION '% rows cannot be changed once written', TG_TABLE_NAME
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;

-- A loaded rulebook version never changes. Only the active flag moves.
CREATE TRIGGER templates_frozen BEFORE UPDATE OR DELETE ON templates
    FOR EACH ROW EXECUTE FUNCTION mdeis_freeze('active');
-- Its rule sentences never change either. The embedding can be filled in afterwards.
CREATE TRIGGER clauses_frozen BEFORE UPDATE OR DELETE ON clauses
    FOR EACH ROW EXECUTE FUNCTION mdeis_freeze('embedding', 'embedding_model');
-- The approver's answers are a record, not a draft.
CREATE TRIGGER resolutions_frozen BEFORE UPDATE OR DELETE ON resolutions
    FOR EACH ROW EXECUTE FUNCTION mdeis_freeze();

-- A released report is never recomputed, edited or deleted.
CREATE FUNCTION reports_released_frozen() RETURNS trigger LANGUAGE plpgsql AS
$$
BEGIN
    IF OLD.status = 'released' THEN
        RAISE EXCEPTION 'report % is released and cannot be changed or deleted', OLD.report_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER reports_released_frozen BEFORE UPDATE OR DELETE ON reports
    FOR EACH ROW EXECUTE FUNCTION reports_released_frozen();
