-- Migration 009: lesson plans (T-060). Additive: no earlier table is changed. No school value here.
--
-- A plan is one tutor's lesson notes for one stage, area and term, the edits proposed for the class,
-- the class evidence they rest on, and the tutor's decision. The notes and the objective are never
-- changed after the row is made. The edits are checked by code before the row exists, so a row holds
-- only edits that passed, and at least one. Notes that failed the learner-name guard are never stored,
-- and nothing is stored when there is not enough class evidence.
--
-- The path of a plan:
--   proposed > approved | rejected
--   approved > superseded
-- `approved` is refused by the database unless ALL of these hold:
--   1. the check passed (objective unchanged, text outside the edits unchanged);
--   2. an approver is recorded, with a name and a time, and is the author (the tutor approves their
--      own plan);
--   3. at least one edit was accepted, and every accepted edit is one of the kept edits;
--   4. a final text exists.
-- The same rule is in code (src/mdeis/lessons). Here it is the last lock.
--
-- Honest limit: `mdeis_app` owns this table. The constraints and the guard stop a bug and a careless
-- write. They do not stop someone who holds that role's password. The action log shows such a change.
-- The database cannot check that `final_text` is the accepted edits applied to the original; that is
-- code, and a test recomputes it.

CREATE SEQUENCE lesson_plan_seq;

-- The legal moves of a plan, as data. The guard reads this table. `superseded` is never written by
-- the first version of the code; the move is here so a later "revise an approved plan" needs no new
-- migration.
CREATE TABLE lesson_plan_moves (
    from_status text NOT NULL,
    to_status   text NOT NULL,
    PRIMARY KEY (from_status, to_status)
);
INSERT INTO lesson_plan_moves (from_status, to_status) VALUES
    ('proposed', 'approved'),
    ('proposed', 'rejected'),
    ('approved', 'superseded');

-- Each function returns a value, never null, so a constraint cannot pass on a missing one.

CREATE FUNCTION lesson_check_passed(check_result jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE AS
$$
    SELECT coalesce(
        jsonb_typeof(check_result) = 'object'
        AND check_result -> 'passed' = 'true'::jsonb
        AND check_result -> 'objective_unchanged' = 'true'::jsonb
        AND check_result -> 'outside_unchanged' = 'true'::jsonb,
        false)
$$;

CREATE FUNCTION lesson_kept_count(edits jsonb) RETURNS integer
LANGUAGE sql IMMUTABLE AS
$$
    SELECT coalesce(
        CASE WHEN jsonb_typeof(edits) = 'object' AND jsonb_typeof(edits -> 'kept') = 'array'
             THEN jsonb_array_length(edits -> 'kept') END,
        0)
$$;

-- Every kept edit cites at least one finding, and every cited finding is in the stored snapshot.
CREATE FUNCTION lesson_edits_cite_evidence(edits jsonb, evidence jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE AS
$$
    SELECT coalesce(
        CASE WHEN jsonb_typeof(edits) = 'object' AND jsonb_typeof(edits -> 'kept') = 'array'
                  AND jsonb_typeof(evidence) = 'array' THEN
            NOT EXISTS (
                SELECT 1 FROM jsonb_array_elements(edits -> 'kept') e
                WHERE CASE WHEN jsonb_typeof(e -> 'finding_ids') = 'array' THEN
                        jsonb_array_length(e -> 'finding_ids') = 0
                        OR EXISTS (
                            SELECT 1 FROM jsonb_array_elements_text(e -> 'finding_ids') f
                            WHERE NOT EXISTS (
                                SELECT 1 FROM jsonb_array_elements(evidence) x
                                WHERE x ->> 'finding_id' = f))
                      ELSE true END
            )
        ELSE false END,
        false)
$$;

-- The accepted edit ids are distinct and each is a kept edit.
CREATE FUNCTION lesson_accepted_ok(edits jsonb, accepted text[]) RETURNS boolean
LANGUAGE sql IMMUTABLE AS
$$
    SELECT coalesce(
        CASE WHEN jsonb_typeof(edits -> 'kept') = 'array' AND accepted IS NOT NULL THEN
            NOT EXISTS (
                SELECT 1 FROM unnest(accepted) AS u(x)
                WHERE NOT EXISTS (
                    SELECT 1 FROM jsonb_array_elements(edits -> 'kept') e WHERE e ->> 'id' = u.x))
            AND (SELECT count(DISTINCT u.x) FROM unnest(accepted) AS u(x)) = cardinality(accepted)
        ELSE false END,
        false)
$$;

CREATE TABLE lesson_plans (
    plan_id           text PRIMARY KEY DEFAULT mdeis_id('LP-', nextval('lesson_plan_seq'), 6)
                      CHECK (plan_id ~ '^LP-[0-9]{6,}$'),
    status            text NOT NULL DEFAULT 'proposed'
                      CHECK (status IN ('proposed', 'approved', 'superseded', 'rejected')),
    -- what the tutor chose (the school comes from the session, never from an argument)
    school_code       text NOT NULL CHECK (school_code <> ''),
    stage             text NOT NULL CHECK (stage <> ''),
    area              text NOT NULL CHECK (area <> ''),
    term              integer NOT NULL CHECK (term >= 1),
    -- who wrote it: from the verified session
    author_id         text NOT NULL CHECK (author_id <> ''),
    author_name       text NOT NULL CHECK (author_name <> ''),
    -- the tutor's text and the locked objective, as submitted
    original_text     text NOT NULL CHECK (original_text <> ''),
    original_sha256   text NOT NULL CHECK (original_sha256 ~ '^[0-9a-f]{64}$'),
    objective_text    text NOT NULL CHECK (objective_text <> ''),
    objective_sha256  text NOT NULL CHECK (objective_sha256 ~ '^[0-9a-f]{64}$'),
    -- the evidence the edits rest on, and what it was read against
    template_version  text NOT NULL REFERENCES templates (version_name),
    evidence          jsonb NOT NULL,   -- array of class findings: finding_id, stage, area, term, group
                                        -- size, shown counts, clause ids, and the evidence sentence
    pins              jsonb NOT NULL,   -- template_version, template_sha256, and the other pins
    -- the edits after the checks: {"kept": [...], "dropped": [{"id", "lc", "reason"}]}
    edits             jsonb NOT NULL,
    check_result      jsonb NOT NULL,   -- passed, objective_unchanged, outside_unchanged, notices
    revised_text      text NOT NULL,    -- original with ALL kept edits applied
    revised_sha256    text NOT NULL CHECK (revised_sha256 ~ '^[0-9a-f]{64}$'),
    diff              jsonb NOT NULL,
    -- the edits come from a model, so a plan records which model and which prompt
    model             text NOT NULL CHECK (model <> ''),
    prompt_sha256     text NOT NULL CHECK (prompt_sha256 ~ '^[0-9a-f]{64}$'),
    run_id            text NOT NULL CHECK (run_id <> ''),
    -- the tutor's decision
    accepted_edit_ids text[],
    final_text        text,             -- original with ONLY the accepted edits applied
    approver_id       text,
    approver_name     text,
    approved_at       timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT original_matches_hash CHECK (
        original_sha256 = encode(sha256(convert_to(original_text, 'UTF8')), 'hex')),
    CONSTRAINT objective_matches_hash CHECK (
        objective_sha256 = encode(sha256(convert_to(objective_text, 'UTF8')), 'hex')),
    CONSTRAINT revised_matches_hash CHECK (
        revised_sha256 = encode(sha256(convert_to(revised_text, 'UTF8')), 'hex')),
    CONSTRAINT shapes_are_right CHECK (
        jsonb_typeof(evidence) = 'array' AND jsonb_typeof(pins) = 'object'
        AND jsonb_typeof(edits) = 'object' AND jsonb_typeof(check_result) = 'object'
        AND jsonb_typeof(diff) IN ('object', 'array')),
    CONSTRAINT pins_match_column CHECK (
        coalesce(pins ->> 'template_version' = template_version
                 AND coalesce(pins ->> 'template_sha256', '') <> '', false)),
    CONSTRAINT plan_has_kept_edit CHECK (lesson_kept_count(edits) >= 1),
    CONSTRAINT edits_cite_stored_evidence CHECK (lesson_edits_cite_evidence(edits, evidence)),
    -- the decision is all there or all absent; absent while proposed or rejected
    CONSTRAINT decision_whole CHECK (
        (status IN ('proposed', 'rejected')) =
        (accepted_edit_ids IS NULL AND final_text IS NULL AND approver_id IS NULL
         AND approver_name IS NULL AND approved_at IS NULL)),
    -- the gate in the database, as for reports and the registry
    CONSTRAINT approved_needs_approver_and_check CHECK (
        status NOT IN ('approved', 'superseded')
        OR coalesce(
            lesson_check_passed(check_result)
            AND approver_id <> '' AND approver_name <> '' AND approved_at IS NOT NULL
            AND approver_id = author_id
            AND final_text IS NOT NULL AND final_text <> ''
            AND cardinality(accepted_edit_ids) >= 1
            AND lesson_accepted_ok(edits, accepted_edit_ids),
            false))
);
CREATE INDEX lesson_plans_author_idx ON lesson_plans (author_id, created_at DESC);

-- The guard. Runs before every insert, update and delete.
--   insert    a plan starts as proposed, with no decision.
--   delete    refused. An approved plan is never recomputed and never removed.
--   update    what the tutor wrote, what the edits rest on and what they were checked against never
--             change; the status moves only along lesson_plan_moves; a closed plan (rejected,
--             superseded) cannot change at all; an approved plan can only become superseded.
CREATE FUNCTION lesson_plans_guard() RETURNS trigger LANGUAGE plpgsql AS
$$
DECLARE
    col text;
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION 'lesson_plans is never deleted: % is not allowed', TG_OP
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'proposed' OR num_nonnulls(
               NEW.accepted_edit_ids, NEW.final_text, NEW.approver_id, NEW.approver_name,
               NEW.approved_at) > 0 THEN
            RAISE EXCEPTION 'a new lesson plan starts as proposed, with no decision'
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN NEW;
    END IF;

    FOREACH col IN ARRAY ARRAY[
        'plan_id', 'school_code', 'stage', 'area', 'term', 'author_id', 'author_name',
        'original_text', 'original_sha256', 'objective_text', 'objective_sha256',
        'template_version', 'evidence', 'pins', 'edits', 'check_result', 'revised_text',
        'revised_sha256', 'diff', 'model', 'prompt_sha256', 'run_id', 'created_at'] LOOP
        IF (to_jsonb(NEW) -> col) IS DISTINCT FROM (to_jsonb(OLD) -> col) THEN
            RAISE EXCEPTION 'lesson_plans: % cannot change after the plan is created', col
                USING ERRCODE = 'restrict_violation';
        END IF;
    END LOOP;

    IF OLD.status IN ('rejected', 'superseded') AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
        RAISE EXCEPTION 'lesson_plans: a plan that is % is closed', OLD.status
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.status = 'approved'
       AND (to_jsonb(NEW) - 'status') IS DISTINCT FROM (to_jsonb(OLD) - 'status') THEN
        RAISE EXCEPTION 'lesson_plans: an approved plan can only be superseded'
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status AND NOT EXISTS (
        SELECT 1 FROM lesson_plan_moves WHERE from_status = OLD.status AND to_status = NEW.status
    ) THEN
        RAISE EXCEPTION 'lesson_plans: % to % is not a legal move', OLD.status, NEW.status
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER lesson_plans_guard BEFORE INSERT OR UPDATE OR DELETE ON lesson_plans
    FOR EACH ROW EXECUTE FUNCTION lesson_plans_guard();
CREATE TRIGGER lesson_plans_no_truncate BEFORE TRUNCATE ON lesson_plans
    FOR EACH STATEMENT EXECUTE FUNCTION lesson_plans_guard();

-- ---------------------------------------------------------------------------------------------
-- Rights. The app role (the MCP server) is the only role that writes, and it cannot delete or
-- truncate. The init script gives every new table SELECT to the read-only role by default; that is
-- taken away here, because lesson text is not for the borrowed SQL tool. The portal role has no
-- default grant and gets none. The roles are made by the Postgres init script; if mdeis_ro is not
-- there yet, its part is skipped.
-- ---------------------------------------------------------------------------------------------
REVOKE DELETE, TRUNCATE ON lesson_plans, lesson_plan_moves FROM PUBLIC;
DO $$
BEGIN
    EXECUTE format('REVOKE DELETE, TRUNCATE ON lesson_plans FROM %I', current_user);
    EXECUTE format('REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON lesson_plan_moves FROM %I',
                   current_user);
END
$$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mdeis_ro') THEN
        REVOKE ALL ON lesson_plans FROM mdeis_ro;
        GRANT SELECT ON lesson_plan_moves TO mdeis_ro;
    END IF;
END
$$;
