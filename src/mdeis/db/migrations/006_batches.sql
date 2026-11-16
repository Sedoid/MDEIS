-- Migration 006: batches. A learner's batch is the school year of the first report card held for
-- them. It is a filing label made from the cards, not a school rule, so it lives here as a view and
-- not in the template. Additive only: three views, no table changes, nothing stored twice.
--
-- What "first card held" means, and its limit: a learner whose earliest card is not Form 1 (they
-- joined later, or the earlier cards were never uploaded) is filed under the year of the first card
-- we have. `first_stage` is there so a person or the assistant can see that and say so. A learner
-- who repeats a stage stays in the batch they started with, because repeating never changes the
-- earliest card.
--
--   learner_first_card  the earliest card of each learner. A helper: the upload page's role is not
--                       granted it.
--   learner_batches     one row per learner who has a card. For the assistant's read-only role.
--                       It carries the learner's name, on the same terms as migration 005: a name
--                       it returns is sent to the model's provider.
--   card_batches        one row per card: the document and its batch. For the upload page's role.
--                       It carries no learner, no name and no score.
--
-- The views run with their owner's rights, so the two roles need no access to the tables under
-- them. The read-only role can already read every new object the app role makes in `public`
-- (default privileges in the init script), so its grant below only states what is intended. The
-- upload page's role has no default, so its grant is the only way it reads a view. The roles are
-- made by the Postgres init script; if one is not there yet, its grant is skipped.
CREATE VIEW learner_first_card AS
SELECT DISTINCT ON (learner_id) learner_id, academic_year, stage
FROM report_cards
ORDER BY learner_id, academic_year, document_id;

CREATE VIEW learner_batches AS
SELECT l.learner_id,
       l.school_ref,
       l.display_name,
       f.academic_year AS batch,
       f.stage         AS first_stage,
       z.academic_year AS last_year,
       z.stage         AS last_stage,
       z.cards
FROM learners l
JOIN learner_first_card f ON f.learner_id = l.learner_id
JOIN (
    SELECT DISTINCT ON (learner_id) learner_id, academic_year, stage,
           count(*) OVER (PARTITION BY learner_id) AS cards
    FROM report_cards
    ORDER BY learner_id, academic_year DESC, document_id DESC
) z ON z.learner_id = l.learner_id;

CREATE VIEW card_batches AS
SELECT rc.document_id, f.academic_year AS batch
FROM report_cards rc
JOIN learner_first_card f ON f.learner_id = rc.learner_id;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mdeis_ro') THEN
        GRANT SELECT ON learner_batches TO mdeis_ro;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portal_app') THEN
        GRANT SELECT ON card_batches TO portal_app;
    END IF;
END
$$;
