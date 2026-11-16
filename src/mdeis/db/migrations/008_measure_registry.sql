-- Migration 008: the registry of generated measures (T-052, slice 1). Additive: no earlier table is
-- changed. No school value lives here.
--
-- A measure is a pure function the methodology asked for. A version is one piece of generated code
-- with the tests that were run against it. Nothing in this file runs code. The registry only holds
-- what a version is, what was checked, and who decided.
--
-- The path of a version:
--   proposed > testing > awaiting_review > awaiting_demo > approved
--   proposed, testing, awaiting_review or awaiting_demo > rejected or retired
--   approved > retired
-- `awaiting_review` is the automated review. It can only HOLD: it records `pass` or `hold` and its
-- reasons, once. It never approves and never clears a hold. A hold is cleared only by a person who
-- is recorded in `registry_reviewers` as a developer. The human developer review stays as optional
-- columns (`dev_reviewer_*`): nothing requires it yet.
--
-- `approved` is refused by the database unless ALL of these hold:
--   1. the tests passed: at least one test, every recorded test ran, none failed;
--   2. at least one test did not come from the model: its source is `teacher` or `document_example`;
--   3. the static check passed;
--   4. the automated review is `pass`, or it is `hold` and a person has cleared the hold;
--   5. the teacher's decision is `confirmed`, by a person recorded as a teacher in the registry.
-- The same rule is in code (src/mdeis/registry). Here it is the last lock, as it is for reports.
--
-- Honest limit: `mdeis_app` owns these tables. The constraints and the guard stop a bug and a
-- careless write. They do not stop someone who holds that role's password. The action log is what
-- shows such a change, and every status move is one row in it.

CREATE SEQUENCE measure_seq;

-- Who may clear a hold (developer) and who may decide as the teacher. Filled by an admin step on the
-- server, never by the model and never from a tool argument. Keyed by the user id the signed token
-- carries.
CREATE TABLE registry_reviewers (
    user_id     text NOT NULL CHECK (user_id <> ''),
    role        text NOT NULL CHECK (role IN ('developer', 'teacher')),
    name        text NOT NULL CHECK (name <> ''),
    granted_by  text NOT NULL CHECK (granted_by <> ''),
    granted_at  timestamptz NOT NULL DEFAULT now(),
    revoked_at  timestamptz,
    PRIMARY KEY (user_id, role)
);

CREATE TABLE measures (
    measure_id  text PRIMARY KEY DEFAULT mdeis_id('MS-', nextval('measure_seq'), 4)
                CHECK (measure_id ~ '^MS-[0-9]{4,}$'),
    name        text NOT NULL CHECK (name <> ''),
    method_ref  text NOT NULL CHECK (method_ref <> ''),
    status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (method_ref, name)
);

-- The legal moves of a version, as data. The guard reads this table. src/mdeis/registry/transitions.py
-- holds the same list for the code, and a test compares the two.
CREATE TABLE measure_status_moves (
    from_status text NOT NULL,
    to_status   text NOT NULL,
    PRIMARY KEY (from_status, to_status)
);
INSERT INTO measure_status_moves (from_status, to_status) VALUES
    ('proposed', 'testing'),
    ('proposed', 'rejected'),
    ('proposed', 'retired'),
    ('testing', 'awaiting_review'),
    ('testing', 'rejected'),
    ('testing', 'retired'),
    ('awaiting_review', 'awaiting_demo'),
    ('awaiting_review', 'rejected'),
    ('awaiting_review', 'retired'),
    ('awaiting_demo', 'approved'),
    ('awaiting_demo', 'rejected'),
    ('awaiting_demo', 'retired'),
    ('approved', 'retired');

-- ---------------------------------------------------------------------------------------------
-- Small pure functions the table constraints use. Each one returns true or false, never null, so a
-- constraint that calls it cannot pass by accident on a missing value.
-- ---------------------------------------------------------------------------------------------

-- Every recorded test is an object with a name (unique) and a known source. A test that claims to
-- come from a worked example in the methodology carries the quote it came from.
CREATE FUNCTION registry_tests_ok(tests jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE AS
$$
    SELECT coalesce(
        CASE WHEN jsonb_typeof(tests) = 'array' THEN
            NOT EXISTS (
                SELECT 1 FROM jsonb_array_elements(tests) t
                WHERE jsonb_typeof(t) <> 'object'
                   OR coalesce(t ->> 'name', '') = ''
                   OR coalesce(t ->> 'source', '') NOT IN ('build', 'break_it', 'teacher', 'document_example')
                   OR (t ->> 'source' = 'document_example' AND coalesce(t ->> 'quote', '') = '')
            )
            AND (SELECT count(DISTINCT t ->> 'name') FROM jsonb_array_elements(tests) t)
                = jsonb_array_length(tests)
        ELSE false END,
        false)
$$;

-- At least one test whose expected value did not come from the model.
CREATE FUNCTION registry_has_independent_test(tests jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE AS
$$
    SELECT coalesce(
        CASE WHEN jsonb_typeof(tests) = 'array' THEN
            EXISTS (
                SELECT 1 FROM jsonb_array_elements(tests) t
                WHERE t ->> 'source' IN ('teacher', 'document_example')
            )
        ELSE false END,
        false)
$$;

-- "Tests passed": there is at least one test, every recorded test has a result, and none failed.
CREATE FUNCTION registry_tests_passed(tests jsonb, result jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE AS
$$
    SELECT coalesce(
        CASE WHEN jsonb_typeof(tests) = 'array' AND jsonb_typeof(result) = 'object' THEN
            jsonb_array_length(tests) > 0
            AND result -> 'passed' = 'true'::jsonb
            AND result -> 'ran' = to_jsonb(jsonb_array_length(tests))
            AND result -> 'failed' = '[]'::jsonb
        ELSE false END,
        false)
$$;

CREATE FUNCTION registry_static_passed(static_check jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE AS
$$ SELECT coalesce(static_check -> 'passed' = 'true'::jsonb, false) $$;

-- The automated review let it through, or it held and a person has cleared the hold.
CREATE FUNCTION measure_review_cleared(review_result text, cleared_by text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS
$$ SELECT coalesce(review_result = 'pass' OR (review_result = 'hold' AND cleared_by IS NOT NULL), false) $$;

CREATE TABLE measure_versions (
    measure_id              text NOT NULL REFERENCES measures,
    version                 integer NOT NULL CHECK (version >= 1),
    status                  text NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'testing', 'awaiting_review', 'awaiting_demo',
                          'approved', 'rejected', 'retired')),
    retired_reason          text CHECK (retired_reason IN ('superseded', 'section_removed', 'withdrawn')),
    -- what it is, as the model will see it in list_measures
    description             text NOT NULL CHECK (description <> ''),
    input_scope             text NOT NULL CHECK (input_scope IN ('learner', 'learner_skill')),
    -- the code and where it came from
    source_code             text NOT NULL CHECK (source_code <> ''),
    code_sha256             text NOT NULL CHECK (code_sha256 ~ '^[0-9a-f]{64}$'),
    methodology_document_id text NOT NULL REFERENCES documents,
    section_sha256          text NOT NULL CHECK (section_sha256 ~ '^[0-9a-f]{64}$'),
    clause_ids              text[] NOT NULL CHECK (cardinality(clause_ids) > 0),
    template_version        text NOT NULL CHECK (template_version <> ''),
    template_patch          jsonb,
    rules_quoted            jsonb NOT NULL DEFAULT '[]',
    -- what it returns, declared. The static check rejects a function with none.
    output_kind             text NOT NULL CHECK (output_kind IN ('number', 'label', 'series', 'table')),
    output_schema           jsonb NOT NULL,
    -- the checks
    static_check            jsonb,
    tests                   jsonb NOT NULL DEFAULT '[]',
    test_result             jsonb,
    built_by_run            text,
    -- the automated review: written once, by the system step only. It can hold. It cannot approve.
    review_result           text CHECK (review_result IN ('pass', 'hold')),
    review_reasons          jsonb,
    reviewed_at             timestamptz,
    -- a hold is cleared only by a person recorded as a developer in registry_reviewers
    hold_cleared_by_id      text,
    hold_cleared_name       text,
    hold_cleared_at         timestamptz,
    hold_cleared_note       text,
    -- the human developer review: optional, nothing requires it yet
    dev_reviewer_id         text,
    dev_reviewer_name       text,
    dev_reviewed_at         timestamptz,
    -- the teacher's decision: the one that approves
    demo_session_id         text,
    teacher_id              text,
    teacher_name            text,
    teacher_decision        text CHECK (teacher_decision IN ('confirmed', 'rejected')),
    teacher_note            text,
    teacher_decided_at      timestamptz,
    approved_at             timestamptz,
    created_at              timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (measure_id, version),

    CONSTRAINT output_schema_declares_kind CHECK (
        coalesce(jsonb_typeof(output_schema) = 'object' AND output_schema ->> 'kind' = output_kind, false)
    ),
    CONSTRAINT code_matches_hash CHECK (
        code_sha256 = encode(sha256(convert_to(source_code, 'UTF8')), 'hex')
    ),
    CONSTRAINT tests_well_formed CHECK (registry_tests_ok(tests)),
    CONSTRAINT review_reasons_is_a_list CHECK (
        review_reasons IS NULL OR jsonb_typeof(review_reasons) = 'array'
    ),
    CONSTRAINT review_recorded_whole CHECK (
        (review_result IS NULL) = (reviewed_at IS NULL)
        AND (review_result IS NULL) = (review_reasons IS NULL)
        AND (review_result IS DISTINCT FROM 'hold'
             OR CASE WHEN jsonb_typeof(review_reasons) = 'array'
                     THEN jsonb_array_length(review_reasons) > 0 ELSE false END)
    ),
    CONSTRAINT hold_cleared_whole CHECK (
        (hold_cleared_by_id IS NULL AND hold_cleared_name IS NULL AND hold_cleared_at IS NULL
         AND hold_cleared_note IS NULL)
        OR coalesce(review_result = 'hold' AND hold_cleared_by_id <> '' AND hold_cleared_name IS NOT NULL
                    AND hold_cleared_at IS NOT NULL AND coalesce(hold_cleared_note, '') <> '', false)
    ),
    CONSTRAINT dev_reviewer_whole CHECK (
        (dev_reviewer_id IS NULL AND dev_reviewer_name IS NULL AND dev_reviewed_at IS NULL)
        OR (dev_reviewer_id IS NOT NULL AND dev_reviewer_name IS NOT NULL AND dev_reviewed_at IS NOT NULL)
    ),
    CONSTRAINT teacher_whole CHECK (
        (teacher_id IS NULL AND teacher_name IS NULL AND teacher_decision IS NULL
         AND teacher_decided_at IS NULL)
        OR (teacher_id IS NOT NULL AND teacher_name IS NOT NULL AND teacher_decision IS NOT NULL
            AND teacher_decided_at IS NOT NULL)
    ),
    CONSTRAINT retired_has_reason CHECK (status <> 'retired' OR retired_reason IS NOT NULL),

    -- Code that failed the static check is never tested, reviewed or approved.
    CONSTRAINT testing_needs_static_check CHECK (
        status NOT IN ('testing', 'awaiting_review', 'awaiting_demo', 'approved')
        OR registry_static_passed(static_check)
    ),
    -- Nothing reaches a reviewer before its tests have passed.
    CONSTRAINT review_stage_needs_passed_tests CHECK (
        status NOT IN ('awaiting_review', 'awaiting_demo', 'approved')
        OR registry_tests_passed(tests, test_result)
    ),
    -- Nothing reaches the teacher while the automated review holds it.
    CONSTRAINT demo_needs_review_cleared CHECK (
        status NOT IN ('awaiting_demo', 'approved')
        OR measure_review_cleared(review_result, hold_cleared_by_id)
    ),
    -- The gate in the database too, as for reports (released_needs_approver_and_check).
    CONSTRAINT approved_needs_tests_review_and_teacher CHECK (
        status <> 'approved'
        OR coalesce(
            registry_tests_passed(tests, test_result)
            AND registry_has_independent_test(tests)
            AND registry_static_passed(static_check)
            AND measure_review_cleared(review_result, hold_cleared_by_id)
            AND teacher_id IS NOT NULL
            AND teacher_decision = 'confirmed'
            AND teacher_decided_at IS NOT NULL
            AND approved_at IS NOT NULL,
            false)
    )
);

-- One approved version per measure at a time. Approving a new one retires the old one first, in the
-- same transaction.
CREATE UNIQUE INDEX one_approved_per_measure ON measure_versions (measure_id) WHERE status = 'approved';

-- ---------------------------------------------------------------------------------------------
-- The guard. Runs before every insert, update and delete of a version.
--   insert    a version starts as `proposed`, with no test result, review, decision or approval.
--   delete    refused. A released report must stay reproducible.
--   update    the code and what it was built from never change (a change is a new version); a
--             version that is approved, rejected or retired is closed (an approved one can only be
--             retired); the status moves only along measure_status_moves; tests only grow, and only
--             while testing or at the demo; a review, a cleared hold, a reviewer, a decision and an
--             approval are written once; each person must be recorded in registry_reviewers.
-- The person's name is copied from registry_reviewers, so the row holds the recorded name.
-- ---------------------------------------------------------------------------------------------
CREATE FUNCTION measure_versions_guard() RETURNS trigger LANGUAGE plpgsql AS
$$
DECLARE
    col     text;
    person  registry_reviewers%ROWTYPE;
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION 'measure_versions is never deleted: % is not allowed', TG_OP
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'proposed' OR num_nonnulls(
               NEW.test_result, NEW.review_result, NEW.hold_cleared_by_id, NEW.dev_reviewer_id,
               NEW.teacher_id, NEW.teacher_decision, NEW.approved_at, NEW.retired_reason) > 0 THEN
            RAISE EXCEPTION 'a new version starts as proposed, with no test result, review, decision or approval'
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN NEW;
    END IF;

    FOREACH col IN ARRAY ARRAY[
        'measure_id', 'version', 'description', 'input_scope', 'source_code', 'code_sha256',
        'methodology_document_id', 'section_sha256', 'clause_ids', 'template_version',
        'template_patch', 'rules_quoted', 'output_kind', 'output_schema', 'static_check',
        'built_by_run', 'created_at'] LOOP
        IF (to_jsonb(NEW) -> col) IS DISTINCT FROM (to_jsonb(OLD) -> col) THEN
            RAISE EXCEPTION 'measure_versions: % cannot change after the version is created; make a new version', col
                USING ERRCODE = 'restrict_violation';
        END IF;
    END LOOP;

    IF OLD.status IN ('approved', 'rejected', 'retired')
       AND (to_jsonb(NEW) - ARRAY['status', 'retired_reason'])
           IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status', 'retired_reason']) THEN
        RAISE EXCEPTION 'measure_versions: a version that is % is closed', OLD.status
            USING ERRCODE = 'restrict_violation';
    END IF;

    FOREACH col IN ARRAY ARRAY[
        'review_result', 'review_reasons', 'reviewed_at', 'hold_cleared_by_id', 'hold_cleared_name',
        'hold_cleared_at', 'hold_cleared_note', 'dev_reviewer_id', 'dev_reviewer_name',
        'dev_reviewed_at', 'demo_session_id', 'teacher_id', 'teacher_name', 'teacher_decision',
        'teacher_note', 'teacher_decided_at', 'approved_at', 'retired_reason'] LOOP
        IF (to_jsonb(OLD) -> col) <> 'null'::jsonb
           AND (to_jsonb(NEW) -> col) IS DISTINCT FROM (to_jsonb(OLD) -> col) THEN
            RAISE EXCEPTION 'measure_versions: % is written once and cannot change', col
                USING ERRCODE = 'restrict_violation';
        END IF;
    END LOOP;

    IF NEW.status IS DISTINCT FROM OLD.status AND NOT EXISTS (
        SELECT 1 FROM measure_status_moves WHERE from_status = OLD.status AND to_status = NEW.status
    ) THEN
        RAISE EXCEPTION 'measure_versions: % to % is not a legal move', OLD.status, NEW.status
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.tests IS DISTINCT FROM OLD.tests THEN
        IF OLD.status NOT IN ('testing', 'awaiting_demo') OR NEW.status <> OLD.status THEN
            RAISE EXCEPTION 'measure_versions: tests can only be added while testing or at the demo'
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF jsonb_array_length(NEW.tests) < jsonb_array_length(OLD.tests) OR EXISTS (
            SELECT 1 FROM generate_series(0, jsonb_array_length(OLD.tests) - 1) i
            WHERE (NEW.tests -> i) IS DISTINCT FROM (OLD.tests -> i)
        ) THEN
            RAISE EXCEPTION 'measure_versions: tests can only grow; a recorded test never changes'
                USING ERRCODE = 'restrict_violation';
        END IF;
    END IF;

    IF NEW.test_result IS DISTINCT FROM OLD.test_result AND OLD.status NOT IN ('testing', 'awaiting_demo') THEN
        RAISE EXCEPTION 'measure_versions: a test result can only be recorded while testing or at the demo'
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.hold_cleared_by_id IS NOT NULL AND OLD.hold_cleared_by_id IS NULL THEN
        SELECT * INTO person FROM registry_reviewers
            WHERE user_id = NEW.hold_cleared_by_id AND role = 'developer' AND revoked_at IS NULL;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'measure_versions: only a developer recorded in registry_reviewers can clear a hold'
                USING ERRCODE = 'restrict_violation';
        END IF;
        NEW.hold_cleared_name := person.name;
    END IF;

    IF NEW.dev_reviewer_id IS NOT NULL AND OLD.dev_reviewer_id IS NULL THEN
        SELECT * INTO person FROM registry_reviewers
            WHERE user_id = NEW.dev_reviewer_id AND role = 'developer' AND revoked_at IS NULL;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'measure_versions: the developer is not recorded in registry_reviewers'
                USING ERRCODE = 'restrict_violation';
        END IF;
        NEW.dev_reviewer_name := person.name;
    END IF;

    IF NEW.teacher_id IS NOT NULL AND OLD.teacher_id IS NULL THEN
        SELECT * INTO person FROM registry_reviewers
            WHERE user_id = NEW.teacher_id AND role = 'teacher' AND revoked_at IS NULL;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'measure_versions: the teacher is not recorded in registry_reviewers'
                USING ERRCODE = 'restrict_violation';
        END IF;
        NEW.teacher_name := person.name;
    END IF;

    RETURN NEW;
END
$$;

CREATE TRIGGER measure_versions_guard BEFORE INSERT OR UPDATE OR DELETE ON measure_versions
    FOR EACH ROW EXECUTE FUNCTION measure_versions_guard();
CREATE TRIGGER measure_versions_no_truncate BEFORE TRUNCATE ON measure_versions
    FOR EACH STATEMENT EXECUTE FUNCTION measure_versions_guard();

-- ---------------------------------------------------------------------------------------------
-- Rights. The app role (the MCP server) is the only role that writes, and it cannot delete or
-- truncate. The read-only role the borrowed server uses may read what a measure IS and nothing
-- else: no source code, no tests, no review reasons, no names, no notes. It reads nothing of
-- registry_reviewers. It can read the fixed list of moves. The roles are made by the Postgres init
-- script; if mdeis_ro is not there yet, its part is skipped.
-- ---------------------------------------------------------------------------------------------
REVOKE DELETE, TRUNCATE ON measures, measure_versions, registry_reviewers FROM PUBLIC;
DO $$
BEGIN
    EXECUTE format(
        'REVOKE DELETE, TRUNCATE ON measures, measure_versions, registry_reviewers FROM %I', current_user);
    EXECUTE format(
        'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON measure_status_moves FROM %I', current_user);
END
$$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mdeis_ro') THEN
        REVOKE ALL ON registry_reviewers FROM mdeis_ro;
        REVOKE ALL ON measure_versions FROM mdeis_ro;
        GRANT SELECT ON measures TO mdeis_ro;
        GRANT SELECT (measure_id, version, status, description, input_scope, output_kind,
                      output_schema, clause_ids, template_version, methodology_document_id,
                      section_sha256, code_sha256, approved_at)
            ON measure_versions TO mdeis_ro;
    END IF;
END
$$;
