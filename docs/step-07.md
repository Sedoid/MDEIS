# Step 7: write the tests before the tools

Branch: `step-07-tests-first` (from `synthetic/practice-school`). Status: written, **not run**. Nothing
below has been proven on the server.

**Goal.** Decide what "correct" means before the tools exist, so Step 8 builds to pass them. The tests
fail now, each with the plain reason "the tool is not built yet". That is the pass condition for this
step. They are measured against the answer key from Step 6, which was worked out by hand and by
separate code, so the tools never mark their own work.

## The plan

1. Define the interface the tools will have, in one place (the test file's docstring).
2. Build the stage: a throwaway schema, both rulebooks, and the practice cohort loaded the way the card
   reader will load it (a `documents` row per card, the printed values, one `assessments` row per term
   score with its card, page and row).
3. Write one group of tests for each of the six things the plan asks for, plus the checks that the
   tools are built the way the eight promises need.
4. Keep them out of a normal `make test` (marker `contract`), and add `make test-contract` to run them.

## Decisions I took (you agreed to my defaults; change any of them and tell me)

| ID | Decision | Why |
| --- | --- | --- |
| D7-1 | Tools are plain functions `tool(ctx, **arguments)`. The MCP server only wraps them. `ctx` is `ToolContext(conn, run_id, approver=None, database_url=None, commit=True)` | Testable without a network. One place to read the approver and the run number from |
| D7-2 | Numbers come back as text with two decimals, rounded half up (`"-2.00"`) | The answer key uses the same form, so there is no float comparison |
| D7-3 | A number in a draft is accepted when it equals (ignoring the sign) a number a tool returned in the same run for the findings the draft cites. The window records are cited, not every record the learner has | Matches `check_citations` in the spec, and the plain way a teacher writes "fell by 2.00" |
| D7-4 | A tool writes its own diary line (refused, blocked and error lines included) and does not commit `ctx.conn` when `ctx.commit` is false | The diary survives a rollback (Step 4), and the tests can roll back |
| D7-5 | `compute_trend` never works out a number for a subject with an unresolved blocking problem, **even if `ingest_assessments` was not called first** | A blocked subject must not be reachable by skipping a step |
| D7-6 | `check_citations` gives each problem a short code: `unknown_number`, `unknown_record`, `unknown_clause`, `uncited_sentence`, `banned_word` | Tests and the model can act on a code, not on prose |
| D7-7 | A score is stored as printed (`score`, `raw_score`, `denominator`). Converting to the rulebook's scale is the tools' job | The parser must not clean or convert anything (Step 9) |
| D7-8 | A rule is found by its ID with SQL (`get_criterion(query="TR-2")`); finding by meaning uses the Step 5 search and is tested in Step 8 | The ID path needs no embedder, so these tests run anywhere |
| D7-9 | Severity is the key's: missing score, score above 20 and duplicate card **block**; wrong printed totals, a remark that disagrees, and a score out of another denominator **warn** | `SPEC` names the kinds but not the severities (see `data/synthetic/FIXTURES.md`) |

The three open questions from Step 6 keep the key's answers: a duplicated school year blocks every
subject, the gap compares each end with its own stage's range, and a window of 3 cites 3 records.

## What was built

| File | What |
| --- | --- |
| `tests/acceptance/test_tools_contract.py` | the contract: about 71 tests (13 fixtures x 2 rulebooks, the whole cohort, messy data, the gate, the citation check, rule lookup, and static checks on the tool code) |
| `tests/acceptance/tool_env.py` | builds the schema, loads the rulebooks and the cohort |
| `tests/acceptance/conftest.py` | fixtures `tool_schema` and `make_ctx` |
| `pyproject.toml` | marker `contract`; a normal run leaves them out (`addopts`) |
| `Makefile` | `make test-contract` |

The six groups from the plan: same input gives the same output; thresholds come from the rulebook and the
strict rulebook changes the answer; each kind of messy data becomes a named problem and nothing is fixed
in silence; too little evidence is said plainly with no number; actions are refused with nobody signed in
and a made-up approver name is not an argument; a report with a number or source no tool returned is
rejected, and release is the one gate (no approver, no passed check, a changed rulebook).

## Server test process

```bash
git fetch && git checkout step-07-tests-first && git pull
make test-contract ARGS="-q"                  # expect: every test FAILS
make test ARGS="tests/acceptance -q"          # expect: the Step 6 tests still pass; the contract tests are left out
```

**What "right" looks like.** About 71 failures and no passes, and every failure reads like one of:

- `the tool 'compute_trend' is not built yet (ImportError: ...)`
- `mdeis.tools.context is not built yet (...)`
- `mdeis.server is not built yet`, or the assertion that the server offers `ping` and not the seven tools
- `there is no tool code to check yet`

**What "wrong" looks like**, and what to paste back if you see it:

- an `ERROR` at set-up (not a `FAILED`): the throwaway schema, a rulebook or the cohort did not load, so
  the stage is broken and the failures mean nothing yet;
- a collection error (`Interrupted: 1 error during collection`);
- a test that **passes**: it is not testing anything yet.

Paste back the last 30 lines of the first command, and the full output if anything above looks wrong.
Do not change a test to make it fail the "right" way.

## How Step 8 uses this

Step 8 builds `mdeis.tools` and the real MCP server until `make test-contract` is all green. Then it
deletes the `addopts` line in `pyproject.toml`, so these tests join `make test`.

## Limits

- The tests have never run. A mistake in the loader or in a comparison shows up as a set-up error or a
  test that is wrong, not only as a missing tool.
- Where the key and the spec disagree or are silent, the key's choice is what the tools are held to (the
  decisions above). If you disagree with one, change it here before Step 8.
- Contract docs are not edited. `docs/TESTS.md` could list these as E-01 to E-10 core tests, and
  `docs/SPEC.md` 5.6 could name the problem codes: both are yours to edit.
