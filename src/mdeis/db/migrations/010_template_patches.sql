-- Migration 010: template patches and the numbers of measure parameters (T-071). Additive: no
-- earlier table is changed. No school value lives here.
--
-- A patch is data: the `measure_params` clauses a build proposed for one measure version, the
-- methodology document and the section hashes they came from, and the version of the template they
-- were checked against. It is not a template. A confirmed patch becomes part of a NEW template
-- version, loaded by the admin step `apply-template-patch` through the ordinary rulebook loader. A
-- loaded template version never changes, and neither does a patch.
--
-- The path of a patch:
--   proposed > applied | rejected
-- `applied` is refused by the database unless ALL of these hold:
--   1. the measure version it belongs to has a teacher decision of `confirmed`, made by a person
--      recorded in `registry_reviewers` (the registry's own constraint holds that person's name);
--   2. who ran the admin step, when, and the template version it was loaded as are all recorded;
--   3. the name written into the new clauses is the teacher's, copied here from the registry row by
--      the guard, never from an argument.
-- The patch text and its sha256 are written once; a changed text is a different patch.
--
-- The numbers of the clauses (MP-n) come from a sequence, so two builds never pick the same one. A
-- gap is fine: a build that fails leaves its numbers unused.
--
-- Honest limit: `mdeis_app` owns this table. The constraints and the guard stop a bug and a careless
-- write. They do not stop someone who holds that role's password. The action log shows such a change.

CREATE SEQUENCE template_patch_seq;
CREATE SEQUENCE measure_param_seq;

CREATE TABLE template_patches (
    patch_id              text PRIMARY KEY DEFAULT mdeis_id('TP-', nextval('template_patch_seq'), 6)
                          CHECK (patch_id ~ '^TP-[0-9]{6,}$'),
    status                text NOT NULL DEFAULT 'proposed'
                          CHECK (status IN ('proposed', 'applied', 'rejected')),
    -- what it was built for
    measure_id            text NOT NULL,
    version               integer NOT NULL,
    source_document_id    text NOT NULL REFERENCES documents,
    base_template_version text NOT NULL CHECK (base_template_version <> ''),
    section_hashes        jsonb NOT NULL,   -- {section id: sha256 of its text} for each section quoted
    -- the patch itself: canonical JSON text, its hash, and the clauses it holds
    patch_text            text NOT NULL CHECK (patch_text <> ''),
    patch_sha256          text NOT NULL CHECK (patch_sha256 ~ '^[0-9a-f]{64}$'),
    clauses               jsonb NOT NULL,   -- the proposed measure_params rules, each confirmed_by null
    created_at            timestamptz NOT NULL DEFAULT now(),
    -- the admin step that loaded it
    applied_by            text CHECK (applied_by <> ''),      -- who ran the step (a typed name)
    applied_at            timestamptz,
    applied_as            text REFERENCES templates (version_name),  -- the template version it became
    confirmed_by_name     text,             -- the teacher's name, copied from the registry by the guard
    closed_note           text,             -- why a patch was rejected

    FOREIGN KEY (measure_id, version) REFERENCES measure_versions (measure_id, version),
    CONSTRAINT patch_matches_hash CHECK (
        patch_sha256 = encode(sha256(convert_to(patch_text, 'UTF8')), 'hex')),
    CONSTRAINT shapes_are_right CHECK (
        jsonb_typeof(section_hashes) = 'object' AND jsonb_typeof(clauses) = 'array'
        AND jsonb_array_length(clauses) > 0),
    CONSTRAINT applied_whole CHECK (
        (status = 'applied')
        = (num_nonnulls(applied_by, applied_at, applied_as, confirmed_by_name) = 4)
        AND (status = 'applied' OR num_nonnulls(applied_by, applied_at, applied_as,
                                                 confirmed_by_name) = 0)),
    CONSTRAINT rejected_has_a_reason CHECK (
        status <> 'rejected' OR coalesce(closed_note, '') <> '')
);
CREATE INDEX template_patches_measure_idx ON template_patches (measure_id, version);

-- The guard. Runs before every insert, update and delete.
--   insert    a patch starts as proposed, with nothing about being applied.
--   delete    refused. A template version built from a patch must stay explainable.
--   update    what the patch is never changes; a patch that is applied or rejected is closed; a
--             proposed patch can become applied only for a confirmed measure version, and then the
--             teacher's name is copied from the registry row.
CREATE FUNCTION template_patches_guard() RETURNS trigger LANGUAGE plpgsql AS
$$
DECLARE
    col       text;
    decided   measure_versions%ROWTYPE;
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION 'template_patches is never deleted: % is not allowed', TG_OP
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'proposed' OR num_nonnulls(
               NEW.applied_by, NEW.applied_at, NEW.applied_as, NEW.confirmed_by_name,
               NEW.closed_note) > 0 THEN
            RAISE EXCEPTION 'a new patch starts as proposed, with nothing about being applied'
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN NEW;
    END IF;

    FOREACH col IN ARRAY ARRAY[
        'patch_id', 'measure_id', 'version', 'source_document_id', 'base_template_version',
        'section_hashes', 'patch_text', 'patch_sha256', 'clauses', 'created_at'] LOOP
        IF (to_jsonb(NEW) -> col) IS DISTINCT FROM (to_jsonb(OLD) -> col) THEN
            RAISE EXCEPTION 'template_patches: % cannot change after the patch is created', col
                USING ERRCODE = 'restrict_violation';
        END IF;
    END LOOP;

    IF OLD.status <> 'proposed' AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
        RAISE EXCEPTION 'template_patches: a patch that is % is closed', OLD.status
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.status = 'applied' AND OLD.status = 'proposed' THEN
        SELECT * INTO decided FROM measure_versions
            WHERE measure_id = NEW.measure_id AND version = NEW.version
              AND teacher_decision = 'confirmed';
        IF NOT FOUND THEN
            RAISE EXCEPTION 'template_patches: a patch is applied only for a measure version that a registered teacher confirmed'
                USING ERRCODE = 'restrict_violation';
        END IF;
        NEW.confirmed_by_name := decided.teacher_name;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER template_patches_guard BEFORE INSERT OR UPDATE OR DELETE ON template_patches
    FOR EACH ROW EXECUTE FUNCTION template_patches_guard();
CREATE TRIGGER template_patches_no_truncate BEFORE TRUNCATE ON template_patches
    FOR EACH STATEMENT EXECUTE FUNCTION template_patches_guard();

-- ---------------------------------------------------------------------------------------------
-- Rights. The app role (the MCP server and the admin step) is the only role that writes, and it
-- cannot delete or truncate. The init script gives every new table SELECT to the read-only role by
-- default; that is taken away here, because the table holds who ran the admin step. The roles are
-- made by the Postgres init script; if mdeis_ro is not there yet, its part is skipped.
-- ---------------------------------------------------------------------------------------------
REVOKE DELETE, TRUNCATE ON template_patches FROM PUBLIC;
DO $$
BEGIN
    EXECUTE format('REVOKE DELETE, TRUNCATE ON template_patches FROM %I', current_user);
END
$$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mdeis_ro') THEN
        REVOKE ALL ON template_patches FROM mdeis_ro;
    END IF;
END
$$;
