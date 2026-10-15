-- Migration 003: small fixes found in review. Additive: 001 and 002 are not edited.
-- No school value lives here.

-- ---------------------------------------------------------------------------------------------
-- A draft report's status is `pending_review`, the word SPEC.md section 5.5 uses.
-- (No reports exist yet, so there is nothing to carry over; the UPDATE is there for safety.)
-- ---------------------------------------------------------------------------------------------
ALTER TABLE reports DROP CONSTRAINT reports_status_check;
UPDATE reports SET status = 'pending_review' WHERE status = 'pending';
ALTER TABLE reports ALTER COLUMN status SET DEFAULT 'pending_review';
ALTER TABLE reports
    ADD CONSTRAINT reports_status_check CHECK (status IN ('pending_review', 'released'));

-- ---------------------------------------------------------------------------------------------
-- A document finding is about a template rule (TR-1) or a methodology section (M-6.4), so the
-- column is called ref_id, not clause_id.
-- ---------------------------------------------------------------------------------------------
ALTER TABLE document_issues RENAME COLUMN clause_id TO ref_id;
COMMENT ON COLUMN document_issues.ref_id IS
    'The template rule (for example TR-1) or methodology section (for example M-6.4) this is about';

-- ---------------------------------------------------------------------------------------------
-- The diary's lock is per schema. It was one lock for the whole database, so a test that wrote
-- diary lines in a scratch schema could hold up the real diary. Nothing else changes: the same
-- chain, the same hashes, the same refusal of edits.
-- ---------------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION action_log_chain() RETURNS trigger LANGUAGE plpgsql AS
$$
DECLARE
    last_hash bytea;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('mdeis.action_log:' || current_schema()));
    SELECT row_hash INTO last_hash FROM action_log ORDER BY event_id DESC LIMIT 1;
    NEW.event_id  := nextval('action_log_seq');
    NEW.prev_hash := coalesce(last_hash, sha256(convert_to('mdeis action log genesis', 'UTF8')));
    NEW.row_hash  := action_log_hash(NEW.event_id, NEW.at, NEW.run_id, NEW.tool, NEW.inputs,
                                     NEW.outputs, NEW.outcome, NEW.approver_id, NEW.approver_name,
                                     NEW.prev_hash);
    RETURN NEW;
END
$$;

-- ---------------------------------------------------------------------------------------------
-- The borrowed read-only tool no longer reads the diary, and no longer reads learners' names.
-- This is a first step. The full answer (named views for it, and for n8n) comes with the n8n views.
-- The role is made by the Postgres init script; if it is not there yet, this does nothing.
-- ---------------------------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mdeis_ro') THEN
        REVOKE SELECT ON action_log FROM mdeis_ro;
        REVOKE SELECT ON learners FROM mdeis_ro;
        GRANT SELECT (learner_id, school_code, school_ref, created_at) ON learners TO mdeis_ro;
    END IF;
END
$$;
