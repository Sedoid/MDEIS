-- Migration 002: document intake and the version pins (T-030). docs/INGEST.md, SPEC.md section 6.
-- Additive only. Migration 001 is not edited.
-- No school value lives here (no scale, term count, threshold or range).

CREATE SEQUENCE document_seq;

-- ---------------------------------------------------------------------------------------------
-- Uploaded documents: report cards ('results') and the methodology ('methodology').
-- ---------------------------------------------------------------------------------------------
CREATE TABLE documents (
    document_id      text PRIMARY KEY DEFAULT mdeis_id('D-', nextval('document_seq'), 6)
                     CHECK (document_id ~ '^D-[0-9]{6,}$'),
    kind             text NOT NULL CHECK (kind IN ('results', 'methodology')),
    original_name    text NOT NULL,          -- for display only; always escaped when shown
    stored_name      text NOT NULL,          -- the name on disk, made by the portal
    sha256           text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    size_bytes       bigint NOT NULL CHECK (size_bytes >= 0),
    uploader_id      text NOT NULL CHECK (uploader_id <> ''),
    uploader_name    text,
    status           text NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'processing', 'done', 'needs_review', 'failed')),
    failure_kind     text,
    failure_detail   text,
    failure_page     integer,
    failure_row      integer,
    duplicate_of     text REFERENCES documents,
    attempts         integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    -- Methodology only: the one in force. At most one (index below).
    active           boolean NOT NULL DEFAULT false,
    embedding_status text NOT NULL DEFAULT 'skipped' CHECK (embedding_status IN ('skipped', 'done', 'failed')),
    acknowledged_by  text,
    acknowledged_at  timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    started_at       timestamptz,
    finished_at      timestamptz,
    -- A failed document always says why. It stores no rows (INGEST.md section 2).
    CONSTRAINT failed_has_kind CHECK (status <> 'failed' OR failure_kind IS NOT NULL),
    CONSTRAINT only_methodology_is_active CHECK (NOT active OR kind = 'methodology'),
    CONSTRAINT acknowledged_together CHECK ((acknowledged_by IS NULL) = (acknowledged_at IS NULL))
);
CREATE UNIQUE INDEX documents_one_active_methodology
    ON documents (active) WHERE kind = 'methodology' AND active;
CREATE INDEX documents_status_idx ON documents (status, created_at);
CREATE INDEX documents_sha256_idx ON documents (sha256);

-- What went wrong, or what changed, in one document: one row per finding.
CREATE TABLE document_issues (
    document_issue_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id       text NOT NULL REFERENCES documents,
    kind              text NOT NULL CHECK (kind <> ''),
    clause_id         text,
    page              integer,
    "row"             integer,
    detail            text NOT NULL DEFAULT '',
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX document_issues_doc_idx ON document_issues (document_id);

-- Small pieces of the methodology text, one per numbered section, for citing and for search.
CREATE TABLE method_chunks (
    chunk_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id text NOT NULL REFERENCES documents,
    section_id  text,                        -- 6.4, 4.S3, 6.3.B. NULL for the title block
    title       text,
    page        integer,
    ordinal     integer NOT NULL,
    text        text NOT NULL,
    text_sha256 text NOT NULL,
    embedding   vector(768),                 -- same size as clauses.embedding
    UNIQUE (document_id, ordinal)
);
CREATE INDEX method_chunks_section_idx ON method_chunks (document_id, section_id);
CREATE INDEX method_chunks_embedding_idx ON method_chunks USING hnsw (embedding vector_cosine_ops);

-- ---------------------------------------------------------------------------------------------
-- Report cards: the values the card printed, kept only to cross-check against our own sums.
-- Date of birth and gender are read to confirm the card is whole. They are not stored.
-- ---------------------------------------------------------------------------------------------
ALTER TABLE learners
    ADD COLUMN school_ref   text CHECK (school_ref <> ''),   -- the card's "ID number"
    ADD COLUMN display_name text,
    ADD CONSTRAINT learners_school_ref_unique UNIQUE (school_code, school_ref);

CREATE TABLE report_cards (
    document_id                text PRIMARY KEY REFERENCES documents,
    learner_id                 text NOT NULL REFERENCES learners,
    academic_year              text NOT NULL CHECK (academic_year ~ '^[0-9]{4}/[0-9]{4}$'),
    stage                      text NOT NULL,
    school_name                text,
    printed_total_coefficients numeric,
    printed_total_weighted     numeric,
    printed_average            numeric,
    printed_grade              text,
    absences                   integer,
    class_decision             text
);
CREATE INDEX report_cards_learner_idx ON report_cards (learner_id, academic_year);

CREATE TABLE card_subjects (
    card_subject_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id      text NOT NULL REFERENCES report_cards,
    subject          text NOT NULL CHECK (subject <> ''),
    coefficient      numeric,
    printed_annual   numeric,
    printed_weighted numeric,
    printed_grade    text,
    remarks          text,
    page             integer,
    "row"            integer,
    UNIQUE (document_id, subject)
);

-- ---------------------------------------------------------------------------------------------
-- Each term score now knows where it came from. All new columns may be empty so a record that
-- did not come from a card stays valid. Nothing here fixes the number of terms or the scale:
-- `term` only has to be 1 or more, and the scale is whatever the template says.
-- ---------------------------------------------------------------------------------------------
CREATE SEQUENCE IF NOT EXISTS assessment_seq;   -- already made in 001; kept so this file reads alone

ALTER TABLE assessments
    ADD COLUMN academic_year       text CHECK (academic_year ~ '^[0-9]{4}/[0-9]{4}$'),
    ADD COLUMN stage               text,
    ADD COLUMN term                integer CHECK (term >= 1),
    ADD COLUMN coefficient         numeric,
    ADD COLUMN denominator         numeric,
    ADD COLUMN raw_score           text,            -- exactly as printed, for example '17.00'
    ADD COLUMN source_document_id  text REFERENCES documents,
    ADD COLUMN source_page         integer,
    ADD COLUMN source_row          integer;
-- Running the worker twice on the same card must not add the term scores twice.
CREATE UNIQUE INDEX assessments_one_per_card_term
    ON assessments (source_document_id, subject, term) WHERE source_document_id IS NOT NULL;
CREATE INDEX assessments_source_idx ON assessments (source_document_id);

-- ---------------------------------------------------------------------------------------------
-- The "which version" columns. A report remembers the rulebook version and the methodology it
-- was written under. A later change shows up as out of date; it never changes the report.
-- ---------------------------------------------------------------------------------------------
ALTER TABLE reports
    ADD COLUMN template_version         text REFERENCES templates,
    ADD COLUMN methodology_document_id  text REFERENCES documents,
    -- A released report always names its rulebook version. (The methodology can be absent if none
    -- was ever uploaded, and the report then says so.)
    ADD CONSTRAINT released_has_template_pin CHECK (status <> 'released' OR template_version IS NOT NULL);

-- Is each report still current? stale = the active version is no longer the pinned one.
CREATE VIEW report_pin_status AS
SELECT r.report_id,
       r.status,
       r.template_version,
       t.version_name AS active_template_version,
       r.template_version IS DISTINCT FROM t.version_name AS template_stale,
       r.methodology_document_id,
       m.document_id AS active_methodology_document_id,
       r.methodology_document_id IS DISTINCT FROM m.document_id AS methodology_stale
FROM reports r
LEFT JOIN templates t ON t.active
LEFT JOIN documents m ON m.kind = 'methodology' AND m.active;

-- ---------------------------------------------------------------------------------------------
-- The portal's key may add and read documents, and read their problems. Nothing else.
-- The role is made by the Postgres init script; if it is not there yet, this does nothing.
-- ---------------------------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portal_app') THEN
        GRANT INSERT, SELECT ON documents TO portal_app;
        GRANT SELECT ON document_issues TO portal_app;
        GRANT USAGE ON SEQUENCE document_seq TO portal_app;
    END IF;
END
$$;
