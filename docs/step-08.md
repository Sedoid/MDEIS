# Step 8: the toolbox

Branch: `step-08-tools` (from `step-07-tests-first`). Status: written, **not run**. Only static checks
were possible here (compile, line length, the "no school number in the code" rule). Nothing below has been
proven on the server.

**Goal.** Build the seven tools and the real MCP server, so that `make test-contract` goes from all red
(Step 7) to all green. The tools are small, plain functions; the numbers come from `mdeis.core` and the
rulebook, never from the model.

## The plan

1. The pure core, with no database: exact arithmetic, the rules read from the rulebook version, the
   problem finder, the trend/level/gap, and the effect of the approver's answers.
2. The approver token (sign and verify), standard library only.
3. The tools, each a plain function `tool(ctx, **arguments)`, all going through one wrapper that writes
   the diary line and then commits.
4. The MCP server: seven tools, no placeholder, the approver and the run read from headers.
5. Unit tests for the pieces that need no database; the contract tests (Step 7) are the acceptance.

## Decisions I took (you agreed to my defaults; change any of them and tell me)

| ID | Decision | Why |
| --- | --- | --- |
| D8-1 | One wrapper (`tools/_call.py`) runs every tool: body, then the diary line (outcome `ok`, `refused`, `blocked` or `error`, approver from the context), then the commit. If the diary line cannot be written the work is rolled back and the call fails | A tool cannot forget the diary, and a call that is not in the diary did not happen |
| D8-2 | A refusal is a normal result `{"status": "refused", "reason": ...}`, not an exception. A bug is an `error` line and the exception goes on | The caller (the orchestrator) can read a refusal and ask the person; a bug must not hide |
| D8-3 | The server reads the approver from the `X-Approver-Token` header and the run from `X-Run-Id`. A bad, forged or expired token is the same as no token. A missing run header gets a fresh `RUN-<uuid>` | The approver is never an argument. The secret is `APPROVER_TOKEN_SECRET` (already in the gateway and server compose files) |
| D8-4 | Every call opens its own database connection | One call never sees another's half-finished work |
| D8-5 | Problems get a stable number: the same problem in the same card gets the same `I-nnnnnn` each time, through a fingerprint. Answers are added to `resolutions`; the latest answer wins | Reading a learner twice does not create two copies; a changed mind is a new row, not an edit |
| D8-6 | The answers that fit each kind of problem: missing score: `confirm_absent` or `use_value`; score outside the scale: `exclude_record` or `use_value`; duplicated card: `keep_as_is` (use the first card) or `exclude_record` (use the later card); a rulebook-unknown subject: `exclude_record`; any warning: `keep_as_is`. `use_value` needs a value between 0 and the card's scale; no other answer takes one | The smallest set that unblocks each case without inventing a number |
| D8-7 | An answer never edits a stored score. It only changes which scores the tools use (`mdeis/core/effective.py`) | "Nothing is fixed in silence" |
| D8-8 | `draft_summary` takes the `result_hash` values of the findings the text is built from, refuses a hash no tool returned in this run, and pins the draft to the rulebook version those findings used (and to the active methodology document, if there is one) | A draft that cites nothing real cannot be saved; release can tell if the rules moved |
| D8-9 | `check_citations` rules: every sentence needs a `[source]` marker; `[A-000123]` must be a record a tool returned in the run; `[TR-2]` a rule a tool returned in the run; every number in the text must appear (ignoring sign) in the findings the draft was built from or in a rule `get_criterion` returned; no word the pinned rulebook bans. Only a clean pass sets `citations_ok` | Matches D7-3 and the spec. Strict on purpose: a sentence with no source is rejected, even a harmless one |
| D8-10 | `release_report` checks, in this order: someone is signed in; the draft exists; it is still `pending_review`; the citation check passed; the rulebook and methodology it was pinned to are still the active ones (`stale_pin`). Then it records the approver's id, name and the time | One gate, with the reason for each refusal in plain words |
| D8-11 | `get_criterion` takes a rule ID (`TR-2`) by SQL, otherwise searches by meaning with the Step 5 embedder, and falls back to matching the words if the embedder cannot be reached. A rule that does not exist returns an empty list | The ID path needs no embedder; the search path degrades instead of failing |
| D8-12 | `result_hash` is the SHA-256 of the result's content, leaving out anything the database numbers (issue numbers, the diary line) and any time | The same input gives the same hash, run after run |
| D8-13 | `scripts/smoke.py` now calls `get_criterion` for `TR-2`: it needs the rulebook loaded, no learner data | The smoke test works on a clean stack before any card is uploaded |

Open questions from Step 6 keep the key's answers (a duplicated school year blocks every subject; the
gap compares each end with its own stage's range; a window cites as many records as the rulebook's
window).

## What was built

| File | What |
| --- | --- |
| `src/mdeis/core/numbers.py` | exact arithmetic, two-decimal text, gap, level name, grade |
| `src/mdeis/core/rules.py` | the rules of one rulebook version, read from the database |
| `src/mdeis/core/records.py` | a learner's stored cards and term scores |
| `src/mdeis/core/issues.py` | finds and names every problem; nothing is guessed |
| `src/mdeis/core/effective.py` | which scores count once the approver's answers are applied |
| `src/mdeis/core/trend.py` | a subject's trend; an area's level, gap and direction |
| `src/mdeis/core/store.py` | saving problems, reading answers |
| `src/mdeis/core/approver_token.py` | sign and verify the short-lived approver token |
| `src/mdeis/tools/` | `context`, the wrapper `_call`, shared helpers, and one file for each of the seven tools |
| `src/mdeis/server.py` | the MCP server: the seven tools, no placeholder |
| `scripts/smoke.py` | calls a real tool |
| `tests/unit/test_approver_token.py`, `test_numbers.py`, `test_tool_citations.py`, `test_rules_from_template.py` | tests of the pieces that need no database, with hand-worked values |
| `pyproject.toml`, `Makefile` | the `addopts` line that left the contract tests out is removed; `make test` now includes them |

## Server test process

```bash
git fetch && git checkout step-08-tools && git pull
make test ARGS="tests/unit -q"                    # the new unit tests, and the older ones
make test-contract ARGS="-q"                      # expect: every contract test passes
make test ARGS="-q"                               # the whole suite, contract tests included
make lint
make dev-up && make load-criteria                 # then the smoke test, with the stack running
make smoke
```

**What "right" looks like.** `make test-contract` is all green (about 71 tests). `make test` is green and
shows no skipped contract test. `make lint` is clean. `make smoke` ends with `SMOKE OK`.

**What to paste back if something fails:** the failing test names and the last 40 lines of that command.
I have not run any of this, so a first failure is likely to be a real bug in the code or in a comparison,
not a surprise. Tests are not weakened to pass: if a contract test looks wrong, say so and we decide.

## Limits

- Nothing has run. The most likely first problems are a mistake in the SQL (a column name), the way the
  server reads the HTTP headers (`request.request_context.request`), and a comparison against the key
  that differs by a sign or a decimal.
- The numbers a draft may state come from the findings it names. A writer who wants to state a number
  from another finding has to name that finding too. This is stricter than "any number in the run".
- Methodology sections (`[M-6.4]`) are accepted as sources only once a tool returns them (Step 9).
- The wrapper commits after the diary line is written, not before. A crash between the two leaves a
  diary line for work that was not saved; the line says what was attempted, which is the safer way round.

## Needs your edit (contract docs are not edited by me)

- `docs/SPEC.md` 5.6: name the problem codes (`unknown_number`, `unknown_record`, `unknown_clause`,
  `uncited_sentence`, `banned_word`) and the refusal reasons (`stale_pin`).
- `docs/TESTS.md`: the contract tests could be listed as core tests.
- `docs/SPEC.md` section 3: the run header `X-Run-Id` is new (D8-3).
