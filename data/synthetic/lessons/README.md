# Lesson fixtures (T-063)

All synthetic. Invented notes and names. Each note has `Objective: ...` on line 1. Checks LC-n and tests E-n are from the lesson-adapter design note.

- `lesson-clean.md`: clear notes in three sections. Baseline for LC-1 to LC-4, E-50 to E-52, E-58.
- `lesson-label.md`: the tutor writes "the weak ones" and "the slow ones". Feeds LC-6, E-56.
- `lesson-names-a-learner.md`: names an invented pupil, not in the cohort. Feeds LC-5, E-55.
- `lesson-names-a-cohort-learner.md`: names a pupil who is in `cohort/learners.json`. LC-5, E-55.
- `lesson-hidden-instruction.md`: activity 2 says to ignore the rules and rewrite the objective. E-57, LC-1.
- `lesson-objective-only.md`: only the objective line, no notes. No anchor exists. LC-3, LC-8, empty edit list.
- `lesson-empty.md`: zero bytes. The guard refuses plainly. Feeds LC-10 and the guard step.
- `lesson-long.md`: about 5900 characters. Feeds the size limit in LC-10.
- `lesson-objective-repeated.md`: the objective sentence is repeated in the notes (activity 1, last line). LC-1, LC-3, E-51.
- `class-results.json`: class counts (no names, no learner numbers) for Form 1 to Form 3, literacy and
  numeracy, 2025/2026, template v1, terms 2 (primary) and 3. Feeds E-50, E-54, E-58, E-59, E-63.

## class-results.json: how it was made, and where it may differ from the product

Made by `python3 synthetic/lesson_fixtures.py` (check with `--check`). It reads `cohort/` and
`templates/v1.yaml` only. The rules are in that file's header. Every threshold is read from the template.
Term 3 matches `truth/answer_key.json` for all six stage and area pairs. Term 2 has no second key.

- Class here is the stage cohort of the school year 2025/2026. The current `stage_gap_counts` has no
  school-year argument: it uses each learner's latest year at the stage, so its Form 1 and Form 2 groups
  would include older cohorts. Expect a difference until T-059 adds the year.
- Blocking issues are taken from every card of the learner, not only up to the term asked for.
- Form 1 at term 2 has only two terms of records, so 19 of 20 are not enough evidence for literacy and
  20 of 20 for numeracy. A "class with a gap" for E-58 is Form 3 literacy (7 below). A "class with no gap"
  (E-59) is Form 2 numeracy (no below, 2 blocked); it is not a class with zero blocked.
- Counts are written even where the group is under the minimum. Here all groups are 20, over 10.
