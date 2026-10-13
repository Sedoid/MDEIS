# Step 12: the agent as a graph

Branch: `step-12-graph` (from `step-11-gateway`). Status: written, **not run on the server**. The graph
logic was run offline with a small stand-in for pytest on the plain engine (18 tests passed). The
LangGraph wiring itself has not run anywhere: the library is not on this machine.

**Goal.** Make the ask-and-resolve flow, the citation check, the redraft limit and the release guard
explicit in code, as a graph a reviewer can read in ten minutes. The model still chooses tools and
writes the words; the graph decides, in code, the things that must not depend on it. The plain loop
from Step 10 stays as the fallback (`ORCHESTRATOR_MODE=linear`).

## The map

```
START -> screen --refused--------------------------------------> finish -> END
            |
            v
          agent <-------------------------------------------------+
            | model asks for tools                                |
            v                                                     |
           act ----- a problem blocks a subject --> ask -> finish |
            |                                                     |
            | a draft was written                                 |
            v                                                     |
          check ---- failed, redrafts left (default 2) -----------+
            |  \---- failed, none left -> finish (plain stop)
            | passed
            v
          verify (pass-through now; the second-check model plugs in here) -> agent (no tools) -> finish
```

`agent` goes to `finish` when the model answers without a tool call, when the step limit is reached,
or when the model service fails.

## Decisions I took (you agreed to my defaults; change any of them and tell me)

| ID | Decision | Why |
| --- | --- | --- |
| D12-1 | No checkpointer. The state is the conversation itself: the question put to the approver and the line "The draft R-... has passed the citation check" are messages that Open WebUI keeps and the gateway sends back each turn. `langgraph-checkpoint-postgres` and the `orchestrator` database are not used | A question survives a restart because the chat does. Fewer parts, nothing to lose between turns. D-015 expected a checkpoint; this is a deviation that needs your yes |
| D12-2 | The machine (nodes and routes) is plain Python, defined once. LangGraph wires it when installed; otherwise `run_plain` runs the same machine. A test checks that both give the same events | The behaviour does not depend on a library I could not run here, and the offline tests exercise the real logic |
| D12-3 | When a tool finds a problem that blocks a subject, the **graph** writes the question for the approver, from the tool's own wording, with the answers the tool says are possible, and ends the turn. The model is not asked to phrase it. The same problem is asked about once: if an earlier assistant message already carries its number, it is not asked again | The question is exact, and the loop cannot ask forever. The approver can decline by saying so |
| D12-4 | After every `draft_summary`, the graph runs `check_citations` itself, so the check cannot be skipped. A failure goes back to the model with the problems listed, at most `ORCHESTRATOR_MAX_REDRAFTS` times (default 2); then the turn stops with a plain list of what is still wrong, and the draft stays unreleasable | SPEC section 7 step 5 |
| D12-5 | Release guard: `release_report` is not offered to the model unless an earlier turn ended with a draft that passed the check; it can then only be run for that draft, and only when the approver's latest message asks for a release (the Step 10 rule). A draft written in this turn never qualifies | "The graph ends at draft ready" |
| D12-6 | The words "The draft R-... has passed the citation check and is waiting for your release" are added by the graph after the model's text, never by the model | The marker the guard reads comes from code |
| D12-7 | `ingest_assessments` now lists, for each problem, the answers that fit it (`possible_answers`), so the question can say what the approver may answer | The orchestrator must not import the toolbox's rules |
| D12-8 | The graph adds a short note to the system prompt (`prompts/graph_note.md`) saying which steps the system does itself | The model should not do them twice |
| D12-9 | The default stays `ORCHESTRATOR_MODE=linear` until the graph has run on the server. Switch with `ORCHESTRATOR_MODE=graph` in `.env` | Do not make the unproven path the default |
| D12-10 | `langgraph` is not pinned to an exact version in this step. After the first successful image build, run `make dev-ps` then `docker compose ... exec orchestrator pip freeze \| grep -i langgraph` and pin what it shows | I cannot check which versions exist from here |

## What was built

| File | What |
| --- | --- |
| `src/orchestrator/graph.py` | the nodes, the routes, the two runners and the entry point |
| `src/orchestrator/prompts/graph_note.md` | the prompt note for graph mode |
| `src/orchestrator/app.py` | `graph` mode selects the graph; `/healthz` says which mode and engine run |
| `src/orchestrator/loop.py`, `config.py` | `execute` can limit which reports may be released; `ORCHESTRATOR_MAX_REDRAFTS` |
| `src/mdeis/tools/_shared.py` | `possible_answers` on each problem (D12-7) |
| `scripts/ask_orchestrator.py`, `Makefile` | `make ask` keeps a conversation (`NEW=1` starts again) |
| `tests/unit/test_orchestrator_graph.py`, `orchestrator_fakes.py` | the graph tests; shared fakes |

## What the tests prove (once run)

A blocking problem ends the turn with a plain question and the model is not called to word it; the
next message resumes and the same problem is not asked again. A blocked subject found by
`compute_trend` is asked about too. The check runs after every draft without the model asking; once
it passes the model is given no tools. A failing check goes back at most twice, then a plain stop
that names the problems. `release_report` is absent before a checked draft, present after one plus a
newer message, limited to the draft that was shown, and held back unless the approver asked for it. A
draft written in a turn cannot be released in it. A ranking request is refused at the first node. The
state and every update hold no approver and no token. Two runs of the same script make the same tool
calls. With LangGraph installed, it gives the same events as the plain engine.

## Server test process

```bash
git fetch && git checkout step-12-graph && git pull
make test ARGS="tests/unit/test_orchestrator_graph.py tests/unit/test_orchestrator_loop.py tests/unit/test_orchestrator_app.py -q"
make test ARGS="-q"
make test-contract ARGS="-q"         # the added field on each problem must not break the tool tests
make lint
```

Then switch the mode and try it. In `.env` set `ORCHESTRATOR_MODE=graph`, then:

```bash
make dev-up && make load-criteria && make probe-orchestrator     # "mode": "graph", "engine": "langgraph"
make ingest KIND=results FILE=data/synthetic/cards/<a card>.pdf AS="Your Name"
TOKEN=$(make -s token ID=u-ada NAME="Ada Teacher")
make ask NEW=1 TOKEN="$TOKEN" Q="Write a report on learner <L-id> for English"     # a clean learner
make ask NEW=1 TOKEN="$TOKEN" Q="Write a report on a messy learner's Chemistry"    # a blocking problem
make ask TOKEN="$TOKEN" Q="It was not assessed. Leave it out."                     # the answer; the run goes on
make ask TOKEN="$TOKEN" Q="I have read it. Release it."                            # a separate turn
```

**What "right" looks like.** The tests are green. The clean learner ends with a draft, the line
"has passed the citation check and is waiting for your release", and "tools called" including
`check_citations` although the model never asked for it. The messy learner ends the first turn with
a question that names the problem number (`I-...`) and the answers you may give; the next turn calls
`resolve_issue` with your answer. The release turn releases that draft and no other, and the action
log shows your name as the approver on that line.

**What to paste back if something fails:** the failing test names, and for a run the `make ask` output
and the last 40 lines of `make dev-logs SERVICE=orchestrator`. Most likely first problems: how
LangGraph treats a node that returns only events, the way it reports a conditional edge, and the
model not calling `draft_summary` with the findings' hashes (an agent-layer matter, not a graph one).

## Limits

- A forged "ready" line: the guard reads the line from earlier assistant messages. A model could write
  such a sentence itself. It would still be refused by the server, which releases only a draft whose
  check passed and whose pins are current, and only for the signed-in approver.
- The graph has not met a real model. Whether the open-weights model follows the prompt and the
  redraft hints is what the evals measure.
- The second-check model (Jev) is not built; `verify` is a pass-through.
- No Langfuse tracing yet.

## Needs your edit (contract docs are not edited by me)

- `docs/DECISIONS.md` D-015: this step does not use a checkpointer (D12-1). Say whether to keep that.
- `docs/SPEC.md` 5.1: `possible_answers` on each problem returned by `ingest_assessments`.
- `docs/CONTAINERS.md`: `ORCHESTRATOR_MAX_REDRAFTS`, and the orchestrator database role unused for now.
