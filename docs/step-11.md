# Step 11: the front door

Branch: `step-11-gateway` (from `step-10-orchestrator`). Status: written, **not run**. The gateway
tests need FastAPI and httpx, which are not on this machine, so none of them has been run, here or
on the server. Only compile and line-length checks were possible.

**Goal.** Open WebUI talks to our gateway as if it were a model. The gateway knows who is signed in,
signs the approver's token, passes the conversation to the orchestrator, and turns what comes back
into an ordinary chat reply. It has no model code in it.

## The plan

1. `POST /v1/chat/completions` (whole and streamed) and `GET /v1/models`, behind the shared key.
2. Identity from the user headers Open WebUI forwards; no user means 401.
3. Sign the five-minute token (the module from Step 8) and send it to the orchestrator.
4. Map the orchestrator's events into the OpenAI reply; say failures in plain words.
5. A probe you can run from inside the stack (`make probe-gateway`).

## Decisions I took (you agreed to my defaults; change any of them and tell me)

| ID | Decision | Why |
| --- | --- | --- |
| D11-1 | The signed-in user is read from `X-OpenWebUI-User-Id` and `X-OpenWebUI-User-Name` (what `ENABLE_FORWARD_USER_INFO_HEADERS` sends; the names can be overridden by `GATEWAY_USER_ID_HEADER` and `GATEWAY_USER_NAME_HEADER`). A missing id is a 401. A missing name falls back to the id | The identity never comes from the message text. Only Open WebUI and the portal can reach the gateway, and they also need its key |
| D11-2 | Any signed-in Open WebUI user can be the approver of their own requests | The spec names "the signed-in user"; finer roles are future work (the README says so) |
| D11-3 | The token is signed per request and travels only in the body field `approver_token` to the orchestrator. The gateway's own key is never forwarded; the orchestrator gets its own | Each hop has its own key |
| D11-4 | The chat id Open WebUI sends (`X-OpenWebUI-Chat-Id`) becomes the `conversation_id`; without it a new one is made | The orchestrator keeps no state, so this is only a label |
| D11-5 | If the orchestrator cannot be reached, answers with an error, or the stream ends without an answer, the chat gets a plain sentence ("not available ... Nothing was changed"). Never a made-up answer, and never the error body | Defensibility |
| D11-6 | The orchestrator's `status` events become SSE comments (`: working`), which keep the connection open and which Open WebUI ignores; `token` events become content deltas | The user sees the answer arrive, not the plumbing |
| D11-7 | A request whose last user message starts with `### Task:` (how Open WebUI writes its own title, tag and follow-up requests) is answered with an empty reply and does not wake the agent | These are switched off in Open WebUI's settings already; this is the safety net |
| D11-8 | `/healthz` is open and reports the orchestrator's health and whether a signing secret is set; it is 503 when either is missing | The container is only healthy when it can do its job |
| D11-9 | Logs hold header **names**, a request number and nothing else: no header values, keys, user ids or message text | The rule for this service |

## What was built

| File | What |
| --- | --- |
| `src/gateway/app.py` | the whole gateway (replaces the stub) |
| `tests/unit/test_gateway.py` | the orchestrator mocked with `httpx.MockTransport` |
| `scripts/probe_gateway.py`, `Makefile` | `make probe-gateway` |

The token module (`src/mdeis/core/approver_token.py`) and its tests came in Step 8.

## What the tests prove (once run)

No user headers: 401, and the orchestrator is never called. No key or a wrong key: 401 on both
endpoints. The token verifies to the header identity, and a name written in the chat changes
nothing; the signing secret and the gateway's key never travel. A whole reply and a streamed reply
have the OpenAI shape, in order, ending in `[DONE]`. An orchestrator that is down, errors or answers
401 gives a plain message in the chat. Open WebUI's housekeeping requests do not reach the agent.
The logs hold names only. There is no model code in `src/gateway/`.

## Server test process

```bash
git fetch && git checkout step-11-gateway && git pull
make test ARGS="tests/unit/test_gateway.py -q"
make test ARGS="-q"
make lint
```

Then with the stack up (`make dev-up && make load-criteria`, model keys in `.env`):

```bash
make probe-gateway                 # four PASS lines and GATEWAY OK
```

Then in the browser: open `http://127.0.0.1:3000`, sign in with the admin account from `.env`, choose
the model `mdeis-assistant`, and ask "What does rule TR-2 say?". The answer should cite `[TR-2]`.
Check the gateway log shows header names and no values:

```bash
make dev-logs SERVICE=gateway      # look for "headers ['accept', 'authorization', ...]"
```

**What "right" looks like.** The test file is green. `make probe-gateway` ends with `GATEWAY OK`. The
chat answers, and the action log (`make dev-psql`, `SELECT run_id, tool, approver_name FROM
action_log ORDER BY event_id DESC LIMIT 10;`) shows the tool calls with the name of the account you
signed in with as the approver on the acting ones.

**What to paste back if something fails:** the failing test names and the last 40 lines. Most likely
first problems: Open WebUI's header names differing from D11-1 (the log will show which headers
arrive), the way FastAPI orders a 401 against reading the body, and an import order `make lint` wants
changed.

## Limits

- Open WebUI's header names are taken from its documentation; `GATEWAY_USER_*_HEADER` overrides them
  if the version in use sends others. Step 13 is where this is first seen working.
- The gateway trusts the user headers because only Open WebUI and the portal can reach it, and both
  need its key. If the gateway were ever exposed to the host, that trust would be wrong. Compose
  publishes no port for it, and `make dev-health` checks that.
- No report downloads and no direct mode (removed by D-015).

## Needs your edit (contract docs are not edited by me)

- `docs/CONTAINERS.md`: the two optional header-name variables, if you want them listed.
