-- Migration 004: the upload page may see which stage and school year a card is for, so it can group
-- the files by class and year. Additive only. Nothing else about a card is shown to it: not the
-- learner, not a score, not a name. The role is made by the Postgres init script; if it is not
-- there yet, this does nothing.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portal_app') THEN
        GRANT SELECT (document_id, academic_year, stage) ON report_cards TO portal_app;
    END IF;
END
$$;
