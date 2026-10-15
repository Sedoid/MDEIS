# STARTUP: the starting folder, the environment variables, and the first run

Status on 2026-10-15: the start-up commands (`make env-init`, `make dev-config`, `make dev-up`,
`make migrate`, `make load-criteria`, `make dev-health`) and the document and agent commands
(`make ingest`, `make ingest-dir`, `make ack`, `make ask`, `make evals`, `make class-data`,
`make prod-up`) have been run on the author's server. `make demo`, the one command, is **planned**
until it passes from a fresh clone on a clean machine.

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
├── deploy/
│   ├── compose/              one file per service (postgres, embedder, mdeis-mcp, portal, ...)
│   ├── caddy/                the three proxy files (Caddyfile, Caddyfile.page, Caddyfile.ip)
│   ├── docker/               Dockerfiles for the images we build (mdeis-mcp, gateway, orchestrator, portal)
│   ├── postgres/             init scripts: databases, roles, the vector extension
│   └── n8n/                  n8n start script
├── scripts/                  helpers the Makefile calls (env, model download, health, probes)
├── src/
│   ├── mdeis/                our MCP server: core/ (calculations), tools/, ingest/, db/ (migrations)
│   ├── gateway/              the FastAPI gateway between Open WebUI and the orchestrator
│   ├── orchestrator/         the LangGraph agent: the only code that calls a model
│   └── portal/               the sign-in and upload page
├── templates/                the rulebooks (criteria templates)
├── synthetic/  data/         synthetic data generator and its output
├── tests/  evals/            tests, and the committed results of real eval runs
├── site/                     the public about page (one static file)
├── workflows/                n8n workflow exports (empty: none built)
└── models/                   created by `make models`. Holds the embedding model. Not tracked
```

Three things to know:

- `.env` is the only file you edit by hand to configure the stack. Compose files hold no values.
- `models/` appears on first run and holds one file of about 274 MB. It is ignored by git.
- Some folders are not tracked (see `.gitignore`), so they are not on the server. `make check`
  says so and skips the structure check there.

## 2. First run

```bash
git clone <repo URL> mdeis && cd mdeis
make env-init       # creates .env with random secrets. Prints which values you still set by hand
nano .env           # set OPENROUTER_API_KEY, OPENROUTER_MODEL, OPENROUTER_PROVIDER
make dev-config     # checks the compose files merge and no required variable is missing
make dev-up         # downloads the model, builds, starts everything, applies migrations, waits until healthy
make load-criteria  # loads the rulebooks from templates/
make dev-health     # checks ports, secrets, database roles and the links between containers
```

After `git pull` on an existing server, run `make env-sync` first: it adds any variable that
`.env.example` has and `.env` lacks, and never changes an existing line. Then `make dev-up`
again, which applies any new migration.

On a pull, untracked files in `evals/results/` that have the same name as a committed result
stop git. Move them aside, pull, and compare.

### Reaching the pages

Published ports bind to `HOST_BIND` (default `127.0.0.1`). Two ways to reach them:

**An SSH tunnel** (the default, nothing public):

```bash
ssh -L 3000:127.0.0.1:3000 -L 8080:127.0.0.1:8080 <user>@<server>
```

Open `http://127.0.0.1:8080` for the page you sign in to and upload on, and
`http://127.0.0.1:3000` for the chat on its own. Sign in with `WEBUI_ADMIN_EMAIL` and
`WEBUI_ADMIN_PASSWORD` from your `.env`. The portal and the chat share one sign-in only if both are
reached by the **same host name** (cookies are kept by host name, not by port), so set
`WEBUI_PUBLIC_URL` to the host name you type.

**The proxy on a bare IP address** (what the author's demo uses). In `.env`:

```
CADDYFILE=Caddyfile.ip
PROXY_BIND=0.0.0.0
WEBUI_SESSION_COOKIE_SECURE=false
```

Then `make prod-up`. One port serves everything: `http://<ip>/` is the chat, `http://<ip>/portal/`
is the upload page and `http://<ip>/mdeis-about/` is the static page. This is plain http, so the
password and the chat text travel unencrypted. It is a demo setting. For anything more, use a
domain name and `CADDYFILE=Caddyfile` (https), and set `WEBUI_SESSION_COOKIE_SECURE=true`.
Run `make prod-check HOST=<ip-or-name>` from another machine to check what is reachable.

The first start downloads several GB of images and the model, so allow several minutes.

### Putting in the first documents

Sign in at the upload page and use the boxes, or from the server:

```bash
make ingest KIND=methodology FILE=path/to/methodology.pdf AS="Your Name"
make ingest-dir KIND=results DIR=path/to/report-cards AS="Your Name" RECURSIVE=1
```

See `docs/INGEST.md` for what is read and what can fail. A card that will not read can be
inspected with `make card-lines FILE=path/to/card.pdf`.

### Daily commands

| Command | What it does |
| --- | --- |
| `make dev-up` / `make dev-down` | Start / stop. The data is kept |
| `make dev-ps` | State and health of each container |
| `make dev-logs SERVICE=orchestrator` | Follow one service's log (leave `SERVICE` off for all) |
| `make dev-restart SERVICE=gateway` | Restart one service |
| `make dev-psql` | Open `psql` in the `mdeis` database |
| `make migrate` | Apply database migrations (safe to run twice) |
| `make load-criteria` / `make rules-status` | Load the rulebooks / show what is loaded |
| `make test ARGS="tests/unit -q"` / `make lint` | Run pytest / ruff inside a container |
| `make check` | Secret scan (and the structure check when `docs/STRUCTURE.md` is present) |
| `make ask Q="..."` | Ask the orchestrator one question, no gateway |
| `make evals` / `make evals ONLY=E-01` | Run the agent evals (five runs each) and save the real result |
| `make probe-embed`, `probe-mcp`, `probe-pg-mcp`, `probe-orchestrator`, `probe-portal`, `probe-signin`, `probe-gateway` | Check one link |
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
| `COMPOSE_PROJECT_NAME` | default | Compose project name. Prefixes networks and volumes |
| `STACK_SUFFIX` | default | Added to the end of every container name: `postgres-mdeis` |
| `HOST_BIND` | default | Address the published ports bind to. Keep `127.0.0.1` and use an SSH tunnel, or set it on purpose |
| `WEBUI_PORT` | default | Server port for Open WebUI (3000). Change it if 3000 is taken |
| `PORTAL_PORT` | default | Server port for the upload page (8080). Change it if 8080 is taken |
| `N8N_EDITOR_PORT` | default | Server port for the n8n editor (5678). Published only while `DEV_EDITOR=1` |
| `TZ` | default | Time zone inside containers |

### Images

Exact tags, never `latest`. Change one only on purpose, and re-run the checks after.

| Variable | Pinned to |
| --- | --- |
| `PGVECTOR_IMAGE` | `pgvector/pgvector:0.8.7-pg16-bookworm` (Postgres 16) |
| `LLAMA_CPP_IMAGE` | llama.cpp server by digest (build b11541, amd64 and arm64) |
| `N8N_IMAGE` | `docker.n8n.io/n8nio/n8n:2.42.6` |
| `WEBUI_IMAGE` | `ghcr.io/open-webui/open-webui:v0.11.4` |
| `PG_MCP_IMAGE` | `crystaldba/postgres-mcp:0.3.0`, the borrowed read-only server |
| `PYTHON_IMAGE` | `python:3.12.15-slim-bookworm`, base of the images we build |

### Postgres

One Postgres server, three databases, and a role for each service (`docs/CONTAINERS.md` section 6).

| Variable | Tag | What it is |
| --- | --- | --- |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | default, generate, default | Superuser and its maintenance database. Used by the init script and admin commands only |
| `MDEIS_DB_NAME` | default | Database for learner records, rules, rule vectors, the action log |
| `MDEIS_APP_USER`, `MDEIS_APP_PASSWORD` | default, generate | Role that owns the schema. Used by `mdeis-mcp`, `ingestd` and the migration step |
| `MDEIS_RO_USER`, `MDEIS_RO_PASSWORD` | default, generate | Read-only role used by the borrowed MCP server |
| `ORCHESTRATOR_DB_NAME`, `ORCHESTRATOR_DB_USER`, `ORCHESTRATOR_DB_PASSWORD` | default, default, generate | The orchestrator's own database (conversation checkpoints). The role cannot connect to `mdeis` |
| `PORTAL_DB_USER`, `PORTAL_DB_PASSWORD` | default, generate | Portal role. In `mdeis` it can add and read document rows and read three columns of `report_cards` |
| `N8N_DB_NAME`, `N8N_DB_USER`, `N8N_DB_PASSWORD` | default, default, generate | n8n's own database and role. It cannot connect to `mdeis` |
| `N8N_AUTO_DB_USER`, `N8N_AUTO_DB_PASSWORD` | default, generate | Read-only role reserved for released-report views. The views are not built, so it reads nothing |

**Warning.** The init script runs only on an empty data volume. Changing a database name, role
name or password in `.env` later does not change the existing database. Run `make dev-reset`
(this deletes the data), then `make dev-up`.

### Embedder

The model is nomic-embed-text-v1.5 (Apache-2.0, English), served by llama.cpp. It turns each
rule into 768 numbers for the rule search.

| Variable | What it is |
| --- | --- |
| `MODELS_DIR`, `EMBED_MODEL_FILE` | Server folder and file name of the model |
| `EMBED_MODEL_URL` | Where `make models` downloads it, pinned to one commit |
| `EMBED_MODEL_SHA256` | Expected checksum. `make models` refuses a file that does not match |
| `EMBED_DIM` | Vector size, 768. The database column must have the same size |
| `EMBEDDER_MEM_LIMIT` | Memory limit for the container |

### Identity and the gate

| Variable | Tag | What it is | Held by |
| --- | --- | --- | --- |
| `APPROVER_TOKEN_SECRET` | generate | Signs and verifies the approver token | `gateway`, `mdeis-mcp` |
| `GATEWAY_API_KEY` | generate | Shared key between Open WebUI and the gateway | `webui`, `gateway` |
| `ORCHESTRATOR_API_KEY` | generate | Shared key between the gateway and the orchestrator | `gateway`, `orchestrator` |
| `PORTAL_SESSION_SECRET` | generate | Signs the upload page's CSRF tokens | `portal` |

### The upload page

| Variable | Tag | What it is |
| --- | --- | --- |
| `WEBUI_PUBLIC_URL` | default | Where the browser opens Open WebUI, for the chat link and frame. Use the same host name you type for the upload page |
| `PORTAL_PUBLIC_URL` | optional | The address people type for the upload page, if different from where the page is reached (behind the proxy) |
| `MAX_UPLOAD_MB` | default | Largest PDF the page takes (10). A Markdown methodology is capped at 1 MB |
| `PORTAL_FRAME_CHAT` | default | `true` shows the chat inside the upload page, `false` only a link |

### The model (held by the orchestrator only)

| Variable | Tag | What it is |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | you | Your key from openrouter.ai |
| `OPENROUTER_MODEL` | you | The pinned open-weights model, written `vendor/model-name` |
| `OPENROUTER_PROVIDER` | you | The provider to pin. The model is never moved to another one |
| `OPENROUTER_BASE_URL` | default | API address. Change it to use a local model |
| `OPENROUTER_ALLOW_FALLBACKS` | default | Stays `false` |
| `OPENROUTER_DATA_COLLECTION` | default | Stays `deny` |
| `JEV_MODE`, `JEV_MODEL`, `JEV_HOLD_THRESHOLD` | default, optional, optional | Placeholders for a second-check model that is **not built**. Leave `JEV_MODE=off`; nothing reads these yet |

No other container gets an `OPENROUTER` variable. `make dev-health` checks this.

### Agent behaviour

| Variable | Tag | What it is |
| --- | --- | --- |
| `ORCHESTRATOR_MODE` | default | `graph` is the LangGraph agent, which the evals and the demo use. `linear` is the plain tool loop, kept as a fallback. The example file says `linear`; set `graph` |
| `ORCHESTRATOR_MAX_STEPS` | default | Most model calls in one run before the agent stops and says so (12) |
| `ORCHESTRATOR_MAX_REDRAFTS` | default | How many times a draft that fails the citation check is written again (2) |
| `CLASS_VIEW_ENABLED` | default | Stays `false`. The counts-only class view is locked until a named person unlocks it |

### n8n

| Variable | Tag | What it is |
| --- | --- | --- |
| `N8N_ENCRYPTION_KEY` | generate | Encrypts credentials n8n stores. **If you change it, stored credentials become unreadable** |
| `N8N_WEBHOOK_URL`, `N8N_WEBHOOK_KEY` | default, generate | Reserved for a workflow the orchestrator could call. None is built |
| `N8N_OPENROUTER_API_KEY` | optional | n8n's own model key, kept apart from the orchestrator's |
| `N8N_SECURE_COOKIE` | default | `false` over plain HTTP. Set `true` only behind HTTPS |

### Open WebUI

| Variable | Tag | What it is |
| --- | --- | --- |
| `WEBUI_SECRET_KEY` | generate | Signs Open WebUI sessions |
| `WEBUI_ADMIN_EMAIL` | default | Login of the first administrator, created on first start. Change it to the approver's email |
| `WEBUI_ADMIN_NAME` | default | That person's display name. It is the name the action log records as approver |
| `WEBUI_ADMIN_PASSWORD` | generate | Their password. Read it from `.env`, then change it in the app |
| `WEBUI_SESSION_COOKIE_SECURE` | default | `true` only when Open WebUI is served over https |

The first three are used on the first start only. Sign-up is closed afterwards. The compose file
also sets the display name to "Lucent AI", hides the file, connector and tool controls, and turns
off prompt suggestions. These are set from the environment on every start, so the values in the
compose file win over anything changed in the Open WebUI settings screen.

### The public proxy (profile `prod`)

| Variable | What it is |
| --- | --- |
| `APP_HOST` | The host name the proxy answers to. `localhost` uses Caddy's own local certificate |
| `CADDYFILE` | `Caddyfile` (a domain and https), `Caddyfile.page` (the static page only) or `Caddyfile.ip` (a bare IP address, plain http) |
| `PROXY_BIND` | `127.0.0.1` is not public. Set `0.0.0.0` on the server on purpose, and only then |

### Tracing (optional)

`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`. Leave empty. Tracing is off by
default and no Langfuse container is provided; the Postgres action log is the audit record either
way.

### Who holds which secret

This is what `make dev-health` checks.

| Secret | Container(s) |
| --- | --- |
| `OPENROUTER_*`, `JEV_*` | `orchestrator` only |
| `APPROVER_TOKEN_SECRET` | `gateway`, `mdeis-mcp` only |
| `GATEWAY_API_KEY` | `webui`, `gateway` |
| `ORCHESTRATOR_API_KEY` | `gateway`, `orchestrator` |
| `MDEIS_APP_PASSWORD` | `mdeis-mcp`, `ingestd`, the migration step, `postgres` (init) |
| `MDEIS_RO_PASSWORD` | `pg-mcp-ro`, `postgres` (init) |
| `ORCHESTRATOR_DB_PASSWORD` | `orchestrator`, `postgres` (init) |
| `PORTAL_DB_PASSWORD`, `PORTAL_SESSION_SECRET` | `portal` (and `postgres` for the password, at init) |
| `N8N_DB_PASSWORD`, `N8N_ENCRYPTION_KEY`, `N8N_WEBHOOK_KEY` | `n8n` (and `postgres` for the password, at init) |
| `WEBUI_SECRET_KEY`, `WEBUI_ADMIN_*` | `webui` |

## 4. Containers at a glance

Every name below ends with `-<STACK_SUFFIX>`. Design and reasons: `docs/CONTAINERS.md`.

| Container | Job | Published port |
| --- | --- | --- |
| `webui` | Open WebUI, the chat screen | `WEBUI_PORT` (3000) |
| `portal` | Sign-in, upload, files, methodology | `PORTAL_PORT` (8080) |
| `gateway` | Looks like an OpenAI endpoint to Open WebUI. Signs the approver token | none |
| `orchestrator` | Runs the agent. Makes the model calls | none |
| `mdeis-mcp` | Our MCP server. Every calculation. No model calls | none |
| `pg-mcp-ro` | Borrowed read-only Postgres MCP server | none |
| `ingestd` | Reads uploaded documents into records | none |
| `embedder` | llama.cpp serving the embedding model | none |
| `postgres` | Postgres with pgvector | none |
| `n8n` | Running, no workflows built | `N8N_EDITOR_PORT` (5678, dev editor only) |
| `migrate` | Runs once at start to apply migrations, then exits | none |
| `proxy` | One front door (profile `prod`, `make prod-up`) | 80 and 443 on `PROXY_BIND` |

## 5. If something fails

| Symptom | Likely cause and next step |
| --- | --- |
| `make dev-config` names a variable | It is missing in `.env`. Run `make env-sync`, then compare with `.env.example` |
| `make models` says the checksum does not match | The file was deleted. Run it again. If the server blocks Hugging Face, download the file into `models/` by hand and run `make models` |
| `embedder` restarts in a loop | `make dev-logs SERVICE=embedder`. Usually the model file is missing |
| `make dev-up` stops at `migrate` | `make dev-logs SERVICE=migrate`. A migration that was edited after it was applied stops here on purpose |
| `load-criteria` stops on a checksum | A rulebook template that was loaded has been edited. Add a new version file; do not edit the loaded one |
| Open WebUI cannot sign in | The admin is created on an empty volume only. `make dev-reset`, then `make dev-up` |
| The upload page signs in but the chat asks to sign in again | The two addresses use different host names. Make `WEBUI_PUBLIC_URL` use the host name you type for the upload page |
| A port is already taken | Change `WEBUI_PORT` or `PORTAL_PORT` in `.env` and run `make dev-up` again |
| A page still shows the old name or settings after a change | Rebuild with `make dev-up`; Open WebUI settings are read from the compose file on each start |
| A report card is rejected | The upload page lists the page and row. `make card-lines FILE=...` shows how the reader sees it |
| Changed a database password and it does not work | See the Postgres warning above |
| `git pull` stops on files in `evals/results/` | Move your untracked copies aside, pull, then compare |
