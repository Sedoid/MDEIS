# Step 18: the video and the submission (you)

Status: a checklist. I cannot record, publish or submit. Nothing may be committed after the deadline
timestamp, **15 Oct 2026**.

## Before you record

- [ ] Steps 13 and 14 are done: tests green, `make demo` has passed once from a fresh clone (the README
      stranger test), `EVALS.md` holds real runs including one failure.
- [ ] The README status table matches reality. Change **Planned** to done only for what has been run.
- [ ] `make check` passes; `git log -p | grep -i -E "sk-or-v1-|sk-lf-|AKIA|PRIVATE KEY"` prints nothing.
- [ ] `git ls-files | grep -E '^(CLAUDE.md|\.claude/|docs/(PLAN|WORKFLOW|PUBLISH|LOG|log|tasks))'` prints nothing.
- [ ] The repository is public, carries `LICENSE` (Apache-2.0), and the "About" box has a one-line description.
- [ ] Everything that pre-dates the build window is listed in the README (or the README says nothing did).

## The video (about 4 minutes)

Record the screen of the running stack. Made-up data only; no key or password on screen.

| Time | Show | Say (plain words) |
| --- | --- | --- |
| 0:00 | the README top | the problem: a learner moves up without the basics, and nobody sees it |
| 0:30 | Open WebUI, ask about a clean learner | numbers come from code; every sentence has a source marker |
| 1:15 | a messy learner (a missing term) | it asks instead of guessing; you answer in chat |
| 2:00 | the draft and `check_citations` | the check is plain code; a draft that fails is not released |
| 2:30 | ask to rank two learners | refused, with the reason |
| 2:50 | release in a separate message | only the signed-in approver can release; show the diary line with the name |
| 3:20 | swap `v1` for `v2-strict`, same learner | the rules are in the school's file, not in the code |
| 3:45 | `EVALS.md` | what was tested, and the failure we did not fix |

## The submission form

- [ ] One sub-theme: longitudinal strength tracking, with plain-language reporting inside it.
- [ ] Team: up to three, all 18 or over and resident in Africa; names match the README.
- [ ] Repository URL, the video, the live demo address (`https://APP_HOST/mdeis-about/` and `/`) and a
      demo account created for the judges (given privately, never in the repository).
- [ ] The open-weights model used for the full task, where it runs, and what a run costs (from OpenRouter).
- [ ] The named approver and her short quote (Step 14).
- [ ] The one failure we did not fix, and what you would try next.

## The freeze

1. Run the stranger test once more on the final commit.
2. Merge the stacked branches to `main` in order (or merge the last one), using a pull request or a
   fast-forward. Tag it if you like: `git tag submission-2026-10-15`.
3. Check the public clone: `git clone <url> /tmp/final && cd /tmp/final && make dev-config`.
4. **Stop committing.** Repos are cloned at the deadline timestamp, and later commits disqualify.
