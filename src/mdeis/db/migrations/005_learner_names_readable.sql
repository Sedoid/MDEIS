-- Migration 005: the read-only role may read a learner's name, so the assistant can find a learner by
-- name. Additive only: one column. Nothing else about a learner becomes readable.
--
-- This is a deliberate trade. The assistant's lookup runs through a model that is reached over the
-- internet, so every name the lookup returns is sent to the model's provider. That is acceptable only
-- if the school accepts it, or runs its own model. The role is made by the Postgres init script; if it
-- is not there yet, this does nothing.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mdeis_ro') THEN
        GRANT SELECT (display_name) ON learners TO mdeis_ro;
    END IF;
END
$$;
