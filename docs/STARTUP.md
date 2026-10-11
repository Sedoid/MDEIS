# STARTUP: the starting folder, the environment variables, and the first run

Status on 2026-10-11: **written, not yet run.** The commands below are untested until the first
run on the server. The result of that run goes in `docs/log/T-004.md`.

Everything runs on the server. Nothing needs to be installed on your own machine except `git`.
The server needs Docker with the Compose plugin, `make`, `python3` and `curl`.

## 1. The starting folder

Run every command from the repository root, the folder that holds `Makefile`. Do not run
`docker compose` by hand: the `Makefile` joins the right compose files in the right order.

```
MDEIS/
├── Makefile                  every command you need: run `make help`
├── docker-compose.yml        networks and volumes only (no services)
├── .env.example              the list of variables, with placeholders. Tracked
├── .env                      your real values. Created by `make env-init`. NEVER tracked
├── pyproject.toml            Python package and the approved libraries
├── .dockerignore             keeps secrets and bulk out of the images
├── deploy/
│   ├── compose/              one file per service (postgres, embedder, mdeis-mcp, ...)
│   ├── docker/               Dockerfiles for the two images we build (mdeis-mcp, gateway)
│   ├── postgres/init/        runs once: creates databases, roles, the vector extension
│   └── n8n/                  n8n start script and the small echo workflow
├── scripts/                  helpers the Makefile calls (env, model download, health, probes)
├── src/
│   ├── mdeis/                our MCP server: core/ (calculations), tools/, db/ (migrations)
│   └── gateway/              the FastAPI gateway between Open WebUI and n8n
├── templates/                criteria templates (empty until T-001)
├── workflows/                n8n workflow exports (empty until T-011)
├── synthetic/  data/         synthetic data generator and its output (empty until T-002)
├── tests/  evals/            tests and committed eval results (empty for now)
├── models/                   created by `make models`. Holds the embedding model. Not tracked
└── docs/                     plans and contracts. Kept on the owner's machine, not on the server
```

Three things to know:

- `.env` is the only file you edit by hand to configure the stack. Compose files hold no values.
- `models/` appears on first run and holds one file of about 274 MB. It is ignored by git.
- `docs/` and `.claude/` are not tracked (see `.gitignore`), so they are not on the server.
  `make check` says so and skips the structure check there.

## 2. First run

```bash
git clone <repo URL> mdeis && cd mdeis
make env-init       # creates .env with random secrets. Prints which values you still set by hand
nano .env           # set OPENROUTER_API_KEY, OPENROUTER_MODEL, OPENROUTER_PROVIDER (optional for now)
make dev-config     # checks the compose files merge and no required variable is missing
make dev-up         # downloads the model, builds, starts everything, waits until healthy
make dev-health     # checks ports, secrets, database roles and the links between containers
```

Open WebUI listens on the server's `127.0.0.1:3000`. From your own computer:

```bash
ssh -L 3000:127.0.0.1:3000 -L 5678:127.0.0.1:5678 <user>@<server>
```

Then open `http://127.0.0.1:3000`. Sign in with `WEBUI_ADMIN_EMAIL` and `WEBUI_ADMIN_PASSWORD`
from your `.env`. The n8n editor is at `http://127.0.0.1:5678`. It asks you to create an owner
account the first time; no default password is committed.

The first start downloads several GB of images and the model, so allow several minutes.

### Daily commands

| Command | What it does |
| --- | --- |
| `make dev-up` / `make dev-down` | Start / stop. The data is kept |
| `make dev-ps` | State and health of each container |
| `make dev-logs SERVICE=n8n` | Follow one service's log (leave `SERVICE` off for all) |
| `make dev-restart SERVICE=gateway` | Restart one service |
| `make dev-psql` | Open `psql` in the `mdeis` database |
| `make migrate` | Apply database migrations (safe to run twice) |
| `make test` / `make lint` | Run pytest / ruff inside a container |
| `make check` | Secret scan (and the structure check when `docs/` is present) |
| `make probe-embed` | Embedder returns 768 numbers, the same on repeat |
| `make probe-mcp` / `probe-pg-mcp` / `probe-n8n` | List tools / list tools / call the echo webhook |
| `make dev-reset` | **Deletes all data** (asks first). Needed after changing a database password |

Options: `make dev-up DEV_EDITOR=0` leaves the n8n editor port out. `make check-env STRICT=1`
also requires the values only you can supply.

## 3. Environment variables

`.env.example` lists every variable. `.env` is your copy. A comment above each variable starts
with a tag:

| Tag | Meaning |
| --- | --- |
| `[generate]` | `make env-init` fills in a random value. You do not need to touch it |
| `[you]` | Only you can supply it. It stays `change-me` until you do |
| `[default]` | Works as it is. Change it only if you need to |
| `[optional]` | May stay empty |

`make check-env` fails if a `[generate]` value is missing and names it. It only notes the `[you]`
values, because the stack can start without a model key. `STRICT=1` makes them required.
It never prints a value.

Some values are not in `.env` because Compose builds them from the ones that are. For example
the database addresses are made from the role names, passwords and database name. Fixed internal
addresses such as `http://mdeis-mcp:8100/mcp` and `http://embedder:8081` are in the compose files.

### Stack identity and ports

| Variable | Tag | What it is |
| --- | --- | --- |
| `COMPOSE_PROJECT_NAME` | default | Compose project name. Prefixes networks and volumes (`mdeis_data`, ...) |
| `STACK_SUFFIX` | default | Added to the end of every container name: `postgres-mdeis`, `n8n-mdeis` |
| `HOST_BIND` | default | Address the published ports bind to. Keep `127.0.0.1` and use an SSH tunnel |
| `WEBUI_PORT` | default | Server port for Open WebUI (3000). Change it if 3000 is taken |
| `N8N_EDITOR_PORT` | default | Server port for the n8n editor (5678). Published only while `DEV_EDITOR=1` |
| `TZ` | default | Time zone inside containers |

### Images

Exact tags, never `latest`. Change one only on purpose, and re-run the checks after.

| Variable | Tag | Pinned to |
| --- | --- | --- |
| `PGVECTOR_IMAGE` | default | `pgvector/pgvector:0.8.7-pg16-bookworm` (Postgres 16) |
| `LLAMA_CPP_IMAGE` | default | llama.cpp server by digest (build b11541, amd64 and arm64) |
| `N8N_IMAGE` | default | `docker.n8n.io/n8nio/n8n:2.42.6` |
| `WEBUI_IMAGE` | default | `ghcr.io/open-webui/open-webui:v0.11.4` |
| `PG_MCP_IMAGE` | default | `crystaldba/postgres-mcp:0.3.0`, the borrowed read-only server |
| `PYTHON_IMAGE` | default | `python:3.12.15-slim-bookworm`, base of the images we build |

### Postgres

One Postgres server, two databases, three roles (`docs/CONTAINERS.md` section 6).

| Variable | Tag | What it is |
| --- | --- | --- |
| `POSTGRES_USER` | default | Superuser name. Used by the init script and admin commands only |
| `POSTGRES_PASSWORD` | generate | Superuser password |
| `POSTGRES_DB` | default | The maintenance database the image creates |
| `MDEIS_DB_NAME` | default | Database for learner records, rules, clause index, action log |
| `MDEIS_APP_USER` | default | Role that owns the schema. Used by `mdeis-mcp` and the migration step |
| `MDEIS_APP_PASSWORD` | generate | Its password |
| `MDEIS_RO_USER` | default | Read-only role. Used by the borrowed MCP server |
| `MDEIS_RO_PASSWORD` | generate | Its password |
| `N8N_DB_NAME` | default | n8n's own database |
| `N8N_DB_USER` | default | n8n's role. It cannot connect to the `mdeis` database |
| `N8N_DB_PASSWORD` | generate | Its password |

**Warning.** The init script runs only on an empty data volume. Changing a database name, role
name or password in `.env` later does not change the existing database. Run `make dev-reset`
(this deletes the data), then `make dev-up`.

### Embedder

The model is nomic-embed-text-v1.5 (Apache-2.0, English), served by llama.cpp. It turns each
criteria clause into 768 numbers for the clause search.

| Variable | Tag | What it is |
| --- | --- | --- |
| `MODELS_DIR` | default | Server folder that holds the model file (`./models`) |
| `EMBED_MODEL_FILE` | default | Model file name |
| `EMBED_MODEL_URL` | default | Where `make models` downloads it, pinned to one commit |
| `EMBED_MODEL_SHA256` | default | Expected checksum. `make models` deletes a file that does not match |
| `EMBED_DIM` | default | Vector size, 768. The database column must have the same size |
| `EMBEDDER_MEM_LIMIT` | default | Memory limit for the container |

### Identity and the gate

| Variable | Tag | What it is | Held by |
| --- | --- | --- | --- |
| `APPROVER_TOKEN_SECRET` | generate | Signs and verifies the approver token | `gateway`, `mdeis-mcp` |
| `GATEWAY_API_KEY` | generate | Shared key between Open WebUI and the gateway | `webui`, `gateway` |

`n8n` never receives `APPROVER_TOKEN_SECRET`. It can pass a token along but cannot make one.

### The model (held by n8n only)

| Variable | Tag | What it is |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | you | Your key from openrouter.ai |
| `OPENROUTER_MODEL` | you | The pinned open-weights model, written `vendor/model-name` |
| `OPENROUTER_PROVIDER` | you | The provider to pin. The model is never moved to another one |
| `OPENROUTER_BASE_URL` | default | API address. Change it to use a local model |
| `OPENROUTER_ALLOW_FALLBACKS` | default | Stays `false` |
| `OPENROUTER_DATA_COLLECTION` | default | Stays `deny` |

No other container gets an `OPENROUTER` variable. `make dev-health` checks this.

### n8n

| Variable | Tag | What it is |
| --- | --- | --- |
| `N8N_ENCRYPTION_KEY` | generate | Encrypts credentials n8n stores. **If you change it, stored credentials become unreadable** |
| `N8N_WEBHOOK_URL` | default | The address the gateway calls. The path is set by the workflow (T-011) |
| `N8N_SECURE_COOKIE` | default | `false` over plain HTTP and an SSH tunnel. Set `true` only behind HTTPS |

### Open WebUI

| Variable | Tag | What it is |
| --- | --- | --- |
| `WEBUI_SECRET_KEY` | generate | Signs Open WebUI sessions |
| `WEBUI_ADMIN_EMAIL` | default | Login of the first administrator, created on first start. Change it to the approver's email |
| `WEBUI_ADMIN_NAME` | default | That person's display name. It is the name the action log records as approver |
| `WEBUI_ADMIN_PASSWORD` | generate | Their password. Read it from `.env`, then change it in the app |

These three are used on the first start only. Sign-up is closed afterwards.

### Tracing (optional)

`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`. Leave empty. Tracing is off by
default; the Postgres action log is the audit record either way.

### Who holds which secret

This is what `make dev-health` checks.

| Secret | Container(s) |
| --- | --- |
| `OPENROUTER_*` | `n8n` only |
| `APPROVER_TOKEN_SECRET` | `gateway`, `mdeis-mcp` only |
| `GATEWAY_API_KEY` | `webui`, `gateway` |
| `MDEIS_APP_PASSWORD` | `mdeis-mcp`, the migration step, `postgres` (init) |
| `MDEIS_RO_PASSWORD` | `pg-mcp-ro`, `postgres` (init) |
| `N8N_DB_PASSWORD`, `N8N_ENCRYPTION_KEY` | `n8n` (and `postgres` for the password, at init) |
| `WEBUI_SECRET_KEY`, `WEBUI_ADMIN_*` | `webui` |

## 4. Containers at a glance

Every name below ends with `-<STACK_SUFFIX>`. Design and reasons: `docs/CONTAINERS.md`.

| Container | Job | Published port |
| --- | --- | --- |
| `webui` | Open WebUI, the chat screen | `127.0.0.1:3000` |
| `gateway` | Looks like an OpenAI endpoint to Open WebUI. Signs the approver token | none |
| `n8n` | Runs the agent. Makes the model calls | `127.0.0.1:5678` (editor, dev only) |
| `mdeis-mcp` | Our MCP server. Every calculation. No model calls | none |
| `pg-mcp-ro` | Borrowed read-only Postgres MCP server | none |
| `embedder` | llama.cpp serving the embedding model | none |
| `postgres` | Postgres with pgvector | none |
| `migrate` | Runs once at start to apply migrations, then exits | none |

`mdeis-mcp` and `gateway` are placeholders until the real code lands (T-006, T-029). They start
and answer `/healthz`, which is enough to test the rest of the wiring.

## 5. If something fails

| Symptom | Likely cause and next step |
| --- | --- |
| `make dev-config` names a variable | It is missing in `.env`. Compare with `.env.example` |
| `make models` says the checksum does not match | The file was deleted. Run it again. If the server blocks Hugging Face, download the file into `models/` by hand and run `make models` |
| `embedder` restarts in a loop | `make dev-logs SERVICE=embedder`. Usually the model file is missing or the healthcheck tool is absent |
| `make dev-up` stops at `migrate` | `make dev-logs SERVICE=migrate`. Then try `make dev-up UP_FLAGS=` and watch `make dev-ps` |
| n8n webhook answers 404 | The workflow is imported but not active. Open the editor and activate it (spike T-024) |
| Open WebUI cannot sign in | The admin is created on an empty volume only. `make dev-reset`, then `make dev-up` |
| Changed a database password and it does not work | See the Postgres warning above |

The assumptions that are still guesses are listed in "Open issues" in `docs/log/T-004.md`.
