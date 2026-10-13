# Step 13: the first full run (you, on the server)

Branch: `step-13-evals` (from `step-17-one-command`). Status: **a run sheet. Nothing here has been run.**
This is the first time the whole chain meets a real database, a real stack and a real model, so
expect to fix things. Paste the output back and I fix what fails; I do not weaken a test to pass.

Every branch from Step 7 to Step 17 is stacked on the one before, so `step-13-evals` holds all of the
work. Test it in the order below; each stage tells you what to paste if it fails.

## 0. Get the code

```bash
git fetch && git checkout step-13-evals && git pull
```

## 1. Without the stack: the tests (about 10 minutes)

```bash
make test ARGS="tests/unit -q"                      # pure pieces: numbers, token, readers' logic, orchestrator, gateway, site
make test-contract ARGS="-q"                        # Step 7/8: the seven tools against the answer key. All must pass
make test ARGS="tests/acceptance -q"                # + cards, methodology, intake, pins, class view
make test ARGS="-q"                                 # everything
make lint
make check
```

**Right looks like:** all green, `make lint` clean, `make check` OK. The first red lines you see are
most likely one of: a SQL column name; pdfplumber cutting a word differently (a card read as
`row_unreadable`); an import order for `make lint`; `fastapi.testclient` needing a package. Paste the
failing test names and the last 40 lines. Fix order if many fail: unit, then contract, then acceptance.

## 2. The stack

```bash
make dev-config && make dev-up                      # builds and starts everything (first run is slow)
make dev-health                                     # the wiring and the safety properties
make load-criteria && make demo-data                # rulebook, methodology, 40 practice cards
make smoke
make probe-orchestrator && make probe-gateway
```

**Right looks like:** `dev-health` all PASS; `demo-data` ends "40 of 40 read; 0 failed"; `smoke` ends
`SMOKE OK`; `probe-gateway` ends `GATEWAY OK`. In `.env` you need `OPENROUTER_API_KEY` and
`OPENROUTER_MODEL` (and `OPENROUTER_PROVIDER`) before the agent can answer.

Pin LangGraph now (the image has just been built):

```bash
docker compose --env-file .env exec orchestrator pip freeze | grep -i -E "langgraph|langchain"
```

Put the exact versions it prints in `pyproject.toml` under the `orchestrator` extra.

## 3. One question at a time, no browser

```bash
make ask NEW=1 Q="What does rule TR-2 say?"                        # cites [TR-2]
make ask NEW=1 Q="Who is the best in the class?"                   # refused, tools called: none
TOKEN=$(make -s token ID=u-ada NAME="Ada Teacher")
make ask NEW=1 TOKEN="$TOKEN" Q="Write a report on learner L-0001's English"   # use a real id from the database
```

Learner numbers: `make dev-psql`, then `SELECT learner_id, display_name FROM learners ORDER BY 1 LIMIT 10;`
(the names are made up).

Try both modes. The default is the plain loop. For the graph put `ORCHESTRATOR_MODE=graph` in `.env`,
then `make dev-up`, and repeat. `make probe-orchestrator` shows which one is running.

**Right looks like:** the draft ends with "has passed the citation check and is waiting for your release"
(graph) or names `check_citations` under tools (plain loop). Check the diary:

```sql
SELECT event_id, run_id, tool, outcome, approver_name FROM action_log ORDER BY event_id DESC LIMIT 20;
SELECT * FROM verify_action_log();      -- the chain is intact
```

## 4. In the browser

Open `http://127.0.0.1:3000`, sign in as the administrator, choose `mdeis-assistant`, and walk the
README's usage steps: ask about a clean learner, then a messy one (Bih Takang's English has a missing
term), answer the question in chat, read the draft, ask for the release in a separate message, and check
that `reports` shows `released` with your name. Then ask it to rank two learners.

`make dev-logs SERVICE=gateway` should show header **names** and no values. If the user is not
recognised (a 401 in the chat), the log shows which headers arrive: tell me, and I set
`GATEWAY_USER_ID_HEADER` and `GATEWAY_USER_NAME_HEADER` to match.

## 5. What to send me

For each stage that failed: the command, the last 40 lines, and for a chat problem the output of
`make dev-logs SERVICE=orchestrator` and `SERVICE=gateway`. For a model problem say which model.

When everything passes, the evidence step is next: `docs/step-14.md`.
