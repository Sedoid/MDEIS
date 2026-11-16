-- Migration 007: a plain-language note on each learner's batch, written by the database so the
-- assistant relays it instead of composing it. Additive only: one column added to the end of the
-- view from 006 (CREATE OR REPLACE keeps its owner and its grants). Nothing is stored.
--
-- Why in SQL: what a batch means is a fact about how it is made, not a judgement, so the same
-- sentence must come out every run and can be tested. The note names the first card's stage and
-- school year as the card says them. It never tests the stage against a rule (which stage is the
-- first is the school's business, so it is not decided here): it states the facts and the limit.
--
-- A batch is derived each time it is read, never stored. It is what the cards held now show, not an
-- enrolment date, so uploading an earlier card later moves the learner (and every one of their
-- cards) to the earlier batch. A learner belongs to one school, so a batch is within one school.
CREATE OR REPLACE VIEW learner_batches AS
SELECT l.learner_id,
       l.school_ref,
       l.display_name,
       f.academic_year AS batch,
       f.stage         AS first_stage,
       z.academic_year AS last_year,
       z.stage         AS last_stage,
       z.cards,
       'The first report card on file for this learner is for ' || f.stage || ', '
           || f.academic_year || '. Their batch is that school year; it is not a recorded '
           || 'enrolment date.'                              AS batch_note
FROM learners l
JOIN learner_first_card f ON f.learner_id = l.learner_id
JOIN (
    SELECT DISTINCT ON (learner_id) learner_id, academic_year, stage,
           count(*) OVER (PARTITION BY learner_id) AS cards
    FROM report_cards
    ORDER BY learner_id, academic_year DESC, document_id DESC
) z ON z.learner_id = l.learner_id;
