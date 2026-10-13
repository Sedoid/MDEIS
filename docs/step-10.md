# Step 10: the orchestrator and the plain loop

Branch: `step-10-orchestrator` (from `step-09-readers`). Status: written, **not run on the server**.
The loop, the guards and the tool allow-list were run offline with a small stand-in for pytest (27
tests passed). The HTTP service tests were not run at all, and nothing has touched a real model or a
real tool server.

**Goal.** A service the gateway can call: it takes a conversation and the approver's signed token,
asks the open-weights model, runs the tool calls it asks for against our toolbox, and streams the
answer back. This is the plain tool-calling loop. It is the shippable path; the LangGraph version
(Step 12) is built on top and the plain loop stays as the fallback.

## The plan

1. The service: `GET /healthz`, `POST /run` (shared key, server-sent events).
2. The tool servers, reached over MCP with a client made for each run: the approver's token and the
   run number go in headers, never in arguments.
3. The allow-list: the seven tools and a few read-only lookups, nothing else.
4. The model client: OpenRouter, an open-weights model, configured from the environment.
5. The loop, with its guards in code: refuse ranking and pathway requests, hold back a release, keep
   the token away from the model.
6. Two scripts to try it by hand: `make token` and `make ask`.

## Decisions I took (you agreed to my defaults; change any of them and tell me)

| ID | Decision | Why |
| --- | --- | --- |
| D10-1 | The loop uses the `mcp` SDK (already a dependency) for the tool servers and plain `httpx` for the model. It does **not** use `langchain-openai` or `langchain-mcp-adapters`. No new dependency is added in this step | D-015 expected those two. Passing a header per run is simple with the SDK, and I cannot check on this machine that those exact versions exist. Step 12 adds `langgraph` only. If you want the LangChain pieces, say so and name the versions |
| D10-2 | The model's reply is read whole, then sent to the gateway as `token` events in chunks of eight words | Live token streaming adds a parser that I cannot test here; tool calls make the first reply arrive late anyway. `status` events show progress meanwhile |
| D10-3 | The service keeps no state. The gateway sends the whole conversation each time. Only `user` and `assistant` messages are kept: a `system` message sent by the caller is dropped, so the only system prompt is ours (`prompts/system.md`) | A caller cannot rewrite the rules |
| D10-4 | `release_report` is held back in code unless (a) the approver's latest message asks for a release (or names the report) and (b) the draft was not written in this same run | The signed-in token proves who is asking, not that they have read the draft and want it out. "Releasing is a separate turn" (D-015) |
| D10-5 | A request to rank or compare learners, or to suggest a pathway, subject choice or career, is refused with the reason before the model is called. The patterns are data in `prompts/refusals.json` | E-08. A starter list, not a complete one: the system prompt and the citation check are the other defences. Extend the file as real questions show gaps |
| D10-6 | Of the borrowed Postgres server only four lookups are offered: `list_schemas`, `list_objects`, `get_object_details`, `execute_sql` (override with `PG_MCP_ALLOWED_TOOLS`). If that server is down the run goes on without it | Read-only lookups (the server and its database role are read-only twice). Numbers a model gets from SQL are not findings, so `check_citations` rejects them |
| D10-7 | A tool whose parameters include `approver`, `approver_id`, `approver_name`, `user`, `user_id` or `by` is never offered | Non-negotiable 4 |
| D10-8 | The step limit is `ORCHESTRATOR_MAX_STEPS` (default 8 model calls). When it is reached the run ends with an answer saying so, marked `incomplete` | A run cannot loop forever |
| D10-9 | A tool failure is given to the model as the kind of error only (`the tool failed (RuntimeError)`), never the message. The service logs names, never message text, tokens or keys | A message could hold a record |
| D10-10 | `ORCHESTRATOR_MODE=graph` is accepted but runs the plain loop until Step 12; `/healthz` says which mode is running | No pretending |

## What was built

| File | What |
| --- | --- |
| `src/orchestrator/app.py` | the service: `/healthz`, `/run` as server-sent events, 401 without the key, a request checked before a run starts |
| `src/orchestrator/loop.py` | the plain loop and its events |
| `src/orchestrator/tools.py` | the allow-list and the approver-parameter filter |
| `src/orchestrator/guards.py` | the request refusal, the release hold, the token scrub |
| `src/orchestrator/mcp_client.py` | one MCP client per tool server, per run; the headers |
| `src/orchestrator/model.py` | the OpenRouter client; provider order, no fallbacks, no data collection from the environment |
| `src/orchestrator/config.py` | settings, read when a run starts |
| `src/orchestrator/prompts/` | the system prompt (a text file) and the refusal patterns (data) |
| `scripts/mint_token.py`, `scripts/ask_orchestrator.py`, `Makefile` | `make token`, `make ask` |
| `deploy/compose/orchestrator.yml`, `pyproject.toml` | `ORCHESTRATOR_MODE`, `ORCHESTRATOR_MAX_STEPS`; the prompt files are packaged |
| `tests/unit/test_orchestrator_loop.py`, `tests/unit/test_orchestrator_app.py` | a scripted fake model and fake tool servers |

## What the tests prove (once run)

The tool a fake model asks for is called, with its arguments, and its result goes back to the model.
A tool outside the list cannot run even when asked for by name, and `record_verification` is never
offered. A tool with an approver parameter is dropped. The token never appears in anything sent to the
model, and it is passed to `mdeis-mcp` in `X-Approver-Token` and not to the lookup server. Every run
has its own `X-Run-Id` that both servers get. A request without the key gets 401 and opens no tool
server. The events come in order: `status`, `token`, `final`. A ranking or pathway request gets a
refusal and no model call. A draft cannot be released in the run that wrote it. The only place that
names the model service is `src/orchestrator/`, and it never names the signing secret.

## Server test process

```bash
git fetch && git checkout step-10-orchestrator && git pull
make test ARGS="tests/unit/test_orchestrator_loop.py tests/unit/test_orchestrator_app.py -q"
make test ARGS="-q"                  # everything
make lint
```

Then with the stack up and `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` (and `OPENROUTER_PROVIDER`) set in
`.env`:

```bash
make dev-up && make load-criteria
make probe-orchestrator                                  # healthy, "mode": "linear", "model_configured": true
make ask Q="Who is the best in the class?"               # refused with the reason, "tools called: none"
make ask Q="What does rule TR-2 say?"                    # calls get_criterion and answers with [TR-2]
make ask Q="Release report R-000001"                     # without a token: nothing is released
TOKEN=$(make -s token ID=u-ada NAME="Ada Teacher")
make ask Q="How is learner L-0001 doing in English?" TOKEN="$TOKEN"
```

(The learner questions need cards loaded first: `make ingest KIND=results FILE=... AS="..."`.)

**What "right" looks like.** The two unit files are green. The health answer shows the mode and
whether the model is configured. The ranking question is refused at once. The rule question names
`get_criterion` under "tools called". The release question never releases anything. With a token the
learner question calls `ingest_assessments` and `compute_trend` and its answer uses only numbers and
markers that appear in their results; the action log (`make dev-psql`: `SELECT run_id, tool, outcome
FROM action_log ORDER BY event_id DESC LIMIT 20;`) shows those calls under one run number.

**What to paste back if something fails:** the failing test names and the last 40 lines. Most likely
first problems: the MCP SDK's client function names or the way it takes headers (the one place I
could not check without the library), the way FastAPI orders a 401 against body validation, and an
import order that `make lint` wants changed.

## Limits

- The model is not called during any test. Whether the pinned open-weights model follows the prompt
  (cites every sentence, waits for the approver, stops after a failed check) is what the agent-layer
  evals measure (Step 13), not these tests.
- The ranking/pathway screen is a first filter with a short list of patterns. It will miss a
  differently-worded request. The prompt and the citation check are the other defences, and the
  evals must report what the screen misses.
- The reply is not streamed live (D10-2).
- Tracing with Langfuse (optional, by environment variable) is not wired in this step.
- The graph, the redraft limit and the second-check model (Jev) come in Steps 12 and later.

## Needs your edit (contract docs are not edited by me)

- `docs/DECISIONS.md` D-015: this step does not use `langchain-openai` or `langchain-mcp-adapters`
  (D10-1). Say whether to keep that, and whether D-001 should list the LangGraph pins.
- `docs/CONTAINERS.md`: the orchestrator's two new environment variables (`ORCHESTRATOR_MODE`,
  `ORCHESTRATOR_MAX_STEPS`) and the optional `PG_MCP_ALLOWED_TOOLS`.
- `.env.example`: I cannot edit it. Optional additions: `ORCHESTRATOR_MODE=linear`,
  `ORCHESTRATOR_MAX_STEPS=8`. The compose file has defaults, so nothing is required.
