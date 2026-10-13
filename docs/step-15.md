# Step 15: the class view (locked)

Branch: `step-15-class-view` (from `step-12-graph`). Status: written, **not run**, and **locked**: the
code is in the repository but the class view is off, and the server does not list it, unless a named
person switches it on.

**A conflict you need to settle.** The build plan says to build the class view "off by default". The
project rules (`CLAUDE.md`, and `docs/PLAN.md` which still says "locked") say it stays locked until
`docs/PLAN.md` says it is unlocked, which needs E-01 to E-10 passing and an approver named for the
view. I built it behind the switch, so that nothing changes while it is locked, and I did **not**
unlock it. If you would rather it was not in the repository until PLAN.md unlocks it, say so and this
step is dropped; nothing else depends on it.

## What it is

`stage_gap_counts(school_id, stage, skill, term)` answers: of this school's learners who have records
at this stage, how many sit below, within and above the stage's expected range for this learning
area, as of the end of this term; and how many have too little evidence or are blocked.

## How it is kept safe

| Rule | Where it is enforced |
| --- | --- |
| Off unless `CLASS_VIEW_ENABLED=true`. When off the server does not list the tool, the model is not offered it, and a call is refused and logged (`class_view_locked`) | `mdeis/tools/class_view.py`, `mdeis/server.py`, `orchestrator/tools.py` |
| Needs a signed-in approver; every call, allowed or refused, is in the diary | the same wrapper as every tool |
| One school only (`school_id` is the school code); no comparison between schools or learners | the query |
| Counts only: no names, no learner numbers, no record numbers | the shape of the answer, and a test that searches it for every name and number in the practice school |
| A group smaller than the rulebook's minimum (GM-1: 10 in v1, 15 in v2-strict) is not shown: no counts and not even the size | `Rules.min_group`, read from the rulebook |
| A learner is placed exactly where `compute_trend` would place them (same functions, same blocked-subject rule) | `mdeis/core/trend.py` |

## Decisions I took (you agreed to my defaults; change any of them and tell me)

| ID | Decision | Why |
| --- | --- | --- |
| D15-1 | "As of term t" means: each learner's records up to the end of term `t` of the latest school year they were at that stage | `term` is one number in the contract (SPEC 5.8); this reads it the same way for every learner |
| D15-2 | A learner with a blocking problem anywhere in a subject's records is counted as `blocked` for areas using that subject, as everywhere else | One rule across the product |
| D15-3 | The suppression rule is the SPEC's: the **group** must reach the minimum. The counts inside a shown group are not suppressed one by one | Matches the contract. See "Needs your decision" for the weakness |
| D15-4 | `Rules` gained `min_group` and the clause id of GM-1, read from the rulebook. A rulebook without a group minimum refuses the call | No school number in code |

## What was built

| File | What |
| --- | --- |
| `src/mdeis/tools/class_view.py` | the tool and the switch |
| `src/mdeis/server.py` | lists the tool only when the switch is on |
| `src/mdeis/core/rules.py` | the group minimum |
| `src/orchestrator/tools.py`, `config.py`, `loop.py`, `graph.py` | offered to the model only when the switch is on |
| `src/orchestrator/prompts/system.md` | one line: never use group counts to point at a learner |
| `deploy/compose/mdeis-mcp.yml`, `orchestrator.yml` | `CLASS_VIEW_ENABLED`, default `false` |
| `tests/acceptance/test_class_view.py`, `tests/unit/test_orchestrator_loop.py` | E-11 and the allow-list |

## What the tests prove (once run)

With the switch off the call is refused and logged and the server lists seven tools; with it on, eight.
With it on and nobody signed in, it is refused and logged. The counts equal what the practice school's
answer-key code gives for Form 1, Form 2 and Form 3, under both rulebooks. The answer holds no
learner's name, number or record. A made-up school with exactly the minimum number of learners is
shown and one with one fewer is not, under both minimums, and the suppressed answer has no counts and
no size. Another school's learners are never counted. The model is offered the tool only when the
switch is on.

## Server test process

```bash
git fetch && git checkout step-15-class-view && git pull
make test ARGS="tests/acceptance/test_class_view.py tests/unit/test_orchestrator_loop.py -q"
make test-contract ARGS="-q"          # the seven-tool tests must still pass: the view is off here
make test ARGS="-q"
make lint
```

The class view is **not** switched on by any of this. To try it by hand (only if you decide to):
set `CLASS_VIEW_ENABLED=true` in `.env`, `make dev-up`, sign in, and ask the assistant for the number
of Form 3 learners within the expected range for literacy in term 3. Set it back to `false` after.

**What "right" looks like.** All green. `make test-contract` still reports seven tools.

## To unlock it for real (a human step)

1. E-01 to E-10 pass on the server, with real output kept in `EVALS.md`.
2. An approver for the view is named.
3. You change the "Class view" row of `docs/PLAN.md` to unlocked.
4. `CLASS_VIEW_ENABLED=true` in `.env`, then restart `mdeis-mcp` and `orchestrator`.

## Needs your decision

- **Small cells.** A group of 20 with "below: 1" points at one child to a teacher who knows the class.
  The contract only hides small groups. Hiding any category count below the minimum would be safer and
  would hide most of what a class of 20 shows. Your call; the change is a few lines in
  `class_view.py`.
- **The lock** (see the top of this page).

## Limits

- Not run anywhere. The expected counts come from the answer-key code, not from the tool itself.
- No record references through an approver-only call (SPEC 5.8 mentions one): not built.
- The orchestrator's system prompt mentions the counts rule even when the tool is off.
