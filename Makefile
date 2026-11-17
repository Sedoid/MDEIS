# MDEIS: one Makefile for the whole stack. It runs on the server. Design: docs/CONTAINERS.md.
#
# Why this file exists: it builds the `docker compose -f ...` list from the files in
# deploy/compose/, so adding a service means adding one file. Do not call `docker compose` by hand.
#
# First run on a new machine:
#   make env-init        create .env with random secrets, then edit the [you] values
#   make dev-config      check that the compose files merge and every variable is set
#   make dev-up          download the model, build, start, wait until healthy
#   make dev-health      prove the wiring and the safety properties
# The one command for a newcomer, once .env is filled in:
#   make demo            check .env and Docker, start, migrate, load data, check health, smoke test
#   make clean           stop and delete the data volumes (then `make demo` starts from nothing)
# See `make help` for everything else.

SHELL := /bin/bash
.DEFAULT_GOAL := help

ENV_FILE   ?= .env
DEV_EDITOR ?= 1
UP_FLAGS   ?= --wait
SERVICE    ?=
SINCE      ?= 24h
FORCE      ?= 0
STRICT     ?= 0
ARGS       ?=

SERVICE_FILES := $(sort $(wildcard deploy/compose/*.yml))
ifneq ($(DEV_EDITOR),1)
SERVICE_FILES := $(filter-out %.dev.yml,$(SERVICE_FILES))
endif
COMPOSE := docker compose --env-file $(ENV_FILE) -f docker-compose.yml $(addprefix -f ,$(SERVICE_FILES))

.PHONY: help env-init env-sync check-env check-docker models dev-config dev-build dev-up dev-down dev-src up down dev-ps \
        dev-logs dev-restart dev-psql dev-reset dev-health migrate load-criteria rules-status search-rules ingest ingest-dir card-lines ack token ask evals probe-gateway prod-config prod-up prod-down prod-check \
        test test-contract gate lint check practice-data class-data usage \
        demo demo-data smoke clean \
        probe-embed probe-mcp probe-pg-mcp probe-n8n probe-orchestrator probe-portal probe-signin

help: ## Show this list
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

# ── Setup ────────────────────────────────────────────────────────────

$(ENV_FILE):
	@echo "There is no $(ENV_FILE) yet. Run: make env-init" >&2; exit 1

env-init: ## Create .env from .env.example with random secrets (never overwrites)
	@python3 scripts/env_init.py

env-sync: $(ENV_FILE) ## Add any variable that .env.example has and .env lacks (never changes existing lines)
	@python3 scripts/env_sync.py

check-env: $(ENV_FILE) ## Check .env is complete (STRICT=1 also requires your own values)
	@python3 scripts/check_env.py $(if $(filter 1,$(STRICT)),--strict,)

check-docker: ## Check Docker is installed and running, and the ports we publish are free
	@python3 scripts/check_docker.py $(if $(filter 1,$(DEV_EDITOR)),--editor,)

models: $(ENV_FILE) ## Download the embedding model and verify its SHA-256
	@python3 scripts/fetch_model.py

dev-config: $(ENV_FILE) ## Validate the merged compose files (prints nothing when fine)
	@$(COMPOSE) config --quiet && echo "OK: compose files merge and every required variable is set"

# ── Run ──────────────────────────────────────────────────────────────

dev-build: $(ENV_FILE) ## Build the images we own
	$(COMPOSE) build

dev-up: check-env check-docker models ## Build and start everything, wait until healthy
	$(COMPOSE) up -d --build --remove-orphans $(UP_FLAGS)
	@$(COMPOSE) ps

dev-down: $(ENV_FILE) ## Stop everything, keep the data
	$(COMPOSE) down

# Runs the app services from ./src (deploy/dev-src.yml), so new code is live without an image rebuild.
# For development only: `make demo` and `make dev-up` use the code baked into the images.
dev-src: check-env ## Run the app services from ./src with no image rebuild: migrate, load rulebooks, recreate
	@echo "== 1/3 Applying the database migrations from ./src"
	$(COMPOSE) -f deploy/dev-src.yml run --rm migrate
	@echo "== 2/3 Loading the rulebook versions in ./templates (the last one listed becomes active)"
	$(COMPOSE) -f deploy/dev-src.yml --profile tools run --rm rules
	@echo "== 3/3 Recreating the app services with the mounted source"
	$(COMPOSE) -f deploy/dev-src.yml up -d --no-deps --force-recreate mdeis-mcp orchestrator gateway portal
	@$(COMPOSE) -f deploy/dev-src.yml ps

up: dev-up ## Alias of dev-up
down: dev-down ## Alias of dev-down

dev-ps: $(ENV_FILE) ## State and health of each container
	@$(COMPOSE) ps -a

dev-logs: $(ENV_FILE) ## Follow logs (SERVICE=n8n for one service)
	$(COMPOSE) logs -f --tail=100 $(SERVICE)

dev-restart: $(ENV_FILE) ## Restart one service (SERVICE=gateway)
	@test -n "$(SERVICE)" || { echo "Say which one: make dev-restart SERVICE=gateway" >&2; exit 1; }
	$(COMPOSE) restart $(SERVICE)

dev-psql: $(ENV_FILE) ## Open psql in the mdeis database
	$(COMPOSE) exec postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$MDEIS_DB_NAME"'

dev-reset: $(ENV_FILE) ## Stop and DELETE all data volumes (asks first; FORCE=1 skips)
	@if [ "$(FORCE)" != "1" ]; then read -r -p "This deletes the database, n8n and Open WebUI data. Type yes: " a; [ "$$a" = "yes" ] || { echo "Cancelled."; exit 1; }; fi
	$(COMPOSE) down -v --remove-orphans

dev-health: $(ENV_FILE) ## Prove the wiring: health, ports, secrets, roles, hops
	@python3 scripts/check_network.py

# ── The one command, and the way back ────────────────────────────────

demo: ## Check .env and Docker, start, migrate, load data, health, smoke test, print addresses
	@echo "== 1/8 Checking .env (every required variable, including your own keys)"
	@python3 scripts/check_env.py --strict
	@echo "== 2/8 Checking Docker and the ports"
	@$(MAKE) --no-print-directory check-docker
	@echo "== 3/8 Starting the stack (the first run downloads several GB of images and a model: allow a long time)"
	@$(MAKE) --no-print-directory dev-up
	@echo "== 4/8 Applying the database migrations"
	@$(MAKE) --no-print-directory migrate
	@echo "== 5/8 Loading the template, the methodology document and the demo sheets"
	@$(MAKE) --no-print-directory demo-data
	@echo "== 6/8 Checking every service from the inside"
	@$(MAKE) --no-print-directory dev-health
	@echo "== 7/8 Smoke test: one tool, one fixture learner"
	@$(MAKE) --no-print-directory smoke
	@echo "== 8/8 Done"
	@python3 scripts/demo_banner.py

# The data step of `make demo`: the rulebook, the methodology, and the practice school's report cards. Every
# part is safe to run twice (the same file again adds nothing). It loads through the same admin
# commands a person would run, never by direct SQL.
DEMO_METHODOLOGY ?= data/synthetic/methodology/methodology-v2.0.md
DEMO_CARDS       ?= data/synthetic/cards
DEMO_BY          ?= Demo loader

demo-data: $(ENV_FILE) ## Load the rulebook, the methodology and the practice cards (the data step of make demo)
	@echo "-- rulebook"
	@$(MAKE) --no-print-directory load-criteria
	@echo "-- methodology: $(DEMO_METHODOLOGY)"
	@$(MAKE) --no-print-directory ingest KIND=methodology FILE="$(DEMO_METHODOLOGY)" AS="$(DEMO_BY)"
	@echo "-- report cards: $(DEMO_CARDS)"
	@$(MAKE) --no-print-directory ingest-dir KIND=results DIR="$(DEMO_CARDS)" AS="$(DEMO_BY)"

smoke: $(ENV_FILE) ## Call one tool for one fixture learner, from inside the network
	@$(COMPOSE) exec -T mdeis-mcp python - < scripts/smoke.py

clean: $(ENV_FILE) ## Stop the stack and DELETE its data volumes, no question asked (the model file stays)
	@python3 scripts/check_docker.py --no-ports
	$(COMPOSE) down -v --remove-orphans

# ── Database, tests, checks ──────────────────────────────────────────

migrate: $(ENV_FILE) ## Apply database migrations (safe to run twice)
	$(COMPOSE) run --rm migrate

load-criteria: $(ENV_FILE) ## Load both rulebook versions (v1 active) and their vectors (BY="Your Name" records who)
	$(COMPOSE) --profile tools run --rm --build $(if $(BY),-e RULEBOOK_BY="$(BY)") rules

rules-status: $(ENV_FILE) ## Show the loaded rulebook versions, their rule counts and vectors
	@$(COMPOSE) --profile tools run --rm rules python -m mdeis.rulebook status

search-rules: $(ENV_FILE) ## Find rules by meaning:  make search-rules Q="expected reading level for Form 1"
	@test -n "$(Q)" || { echo 'usage: make search-rules Q="your question"' >&2; exit 1; }
	@$(COMPOSE) --profile tools run --rm rules python -m mdeis.rulebook search "$(Q)"

ingest: $(ENV_FILE) ## Read one document:  make ingest KIND=results FILE=data/synthetic/cards/x.pdf AS="Ada Teacher"
	@test -n "$(FILE)" -a -n "$(AS)" || { echo 'usage: make ingest KIND=results|methodology FILE=path AS="Your Name"' >&2; exit 1; }
	@test -f "$(FILE)" || { echo "There is no file at $(FILE)." >&2; exit 1; }
	@$(COMPOSE) --profile tools run --rm --build -v "$$(cd "$$(dirname "$(FILE)")" && pwd)/$$(basename "$(FILE)"):/upload/$$(basename "$(FILE)"):ro" admin ingest --kind "$(or $(KIND),results)" --file "/upload/$$(basename "$(FILE)")" --as "$(AS)"

ingest-dir: $(ENV_FILE) ## Read every PDF in a folder (RECURSIVE=1 for sub-folders):  make ingest-dir KIND=results DIR=data/synthetic/cards AS="Ada Teacher"
	@test -n "$(DIR)" -a -n "$(AS)" || { echo 'usage: make ingest-dir KIND=results DIR=folder AS="Your Name"' >&2; exit 1; }
	@test -d "$(DIR)" || { echo "There is no folder at $(DIR)." >&2; exit 1; }
	@$(COMPOSE) --profile tools run --rm --build -v "$$(cd "$(DIR)" && pwd):/upload:ro" admin ingest-dir --kind "$(or $(KIND),results)" --dir /upload --as "$(AS)" $(if $(RECURSIVE),--recursive)

card-lines: $(ENV_FILE) ## Show how the reader sees a card that will not read:  make card-lines FILE="path/to/card.pdf"
	@test -f "$(FILE)" || { echo "There is no file at $(FILE)." >&2; exit 1; }
	@$(COMPOSE) --profile tools run --rm --build --no-deps --entrypoint python -v "$$(cd "$$(dirname "$(FILE)")" && pwd)/$$(basename "$(FILE)"):/upload/card.pdf:ro" admin -m mdeis.ingest.report_card --lines /upload/card.pdf

ack: $(ENV_FILE) ## Put a methodology that needs review in force:  make ack DOC=D-000012 BY="Ada Teacher" NOTE="checked 6.4"
	@test -n "$(DOC)" -a -n "$(BY)" || { echo 'usage: make ack DOC=D-000012 BY="Your Name" [NOTE="..."]' >&2; exit 1; }
	@$(COMPOSE) --profile tools run --rm admin ack-methodology "$(DOC)" --by "$(BY)" --note "$(NOTE)"

test: $(ENV_FILE) ## Run pytest in a container (ARGS="-m integration" to pass options)
	$(COMPOSE) --profile test run --rm --build test pytest $(ARGS)

test-contract: $(ENV_FILE) ## Run only the tool contract tests (Steps 7 and 8). They all must pass; `make test` runs them too
	$(COMPOSE) --profile test run --rm --build test pytest -m contract tests/acceptance/test_tools_contract.py $(ARGS)

gate: $(ENV_FILE) ## Run the clarity gate on a methodology, no database:  make gate FILE=path.md [TEMPLATE=templates/v1.yaml]
	@test -n "$(FILE)" || { echo 'usage: make gate FILE=path/to/methodology.md|pdf [TEMPLATE=templates/v1.yaml]' >&2; exit 1; }
	@test -f "$(FILE)" || { echo "There is no file at $(FILE)." >&2; exit 1; }
	@$(COMPOSE) --profile test run --rm --no-deps -T -v "$$(cd "$$(dirname "$(FILE)")" && pwd)/$$(basename "$(FILE)"):/upload/$$(basename "$(FILE)"):ro" test python -m mdeis.ingest.methodology --gate "/upload/$$(basename "$(FILE)")" $(if $(TEMPLATE),--template "$(TEMPLATE)")

lint: $(ENV_FILE) ## Run ruff in a container
	$(COMPOSE) --profile test run --rm --build test ruff check src tests synthetic

practice-data: $(ENV_FILE) ## Make the practice school (data/synthetic) in a container. Commit it only when the generator, a template or the seed changed
	$(COMPOSE) --profile test run --rm --build --user "$$(id -u):$$(id -g)" practice-data

class-data: $(ENV_FILE) ## Make a card for every learner of the practice school in data/full-class (not committed)
	$(COMPOSE) --profile test run --rm --build --user "$$(id -u):$$(id -g)" practice-data python synthetic/generate.py --seed 42 --out data/full-class --cards all

check: ## Secret scan, and the structure check when docs/STRUCTURE.md is present
	@python3 scripts/scan_secrets.py
	@if [ -f docs/STRUCTURE.md ]; then python3 scripts/check_structure.py; else echo "skipped: structure check (docs/STRUCTURE.md is not on this machine)"; fi

# ── Probes: run from inside the network, through the mdeis-mcp container ──

probe-embed: $(ENV_FILE) ## Embedder: vector size and repeatability
	@$(COMPOSE) exec -T mdeis-mcp python - < scripts/embed_probe.py

probe-mcp: $(ENV_FILE) ## mdeis-mcp: list its tools
	@$(COMPOSE) exec -T mdeis-mcp python - http://mdeis-mcp:8100/mcp http < scripts/mcp_probe.py

probe-pg-mcp: $(ENV_FILE) ## Borrowed Postgres MCP server: list its tools
	@$(COMPOSE) exec -T mdeis-mcp python - http://pg-mcp-ro:8200/sse sse < scripts/mcp_probe.py

probe-n8n: $(ENV_FILE) ## n8n: POST to the echo webhook
	@$(COMPOSE) exec -T mdeis-mcp python - < scripts/n8n_probe.py

probe-orchestrator: $(ENV_FILE) ## Orchestrator: its health answer (database and mdeis-mcp checks)
	@$(COMPOSE) exec -T mdeis-mcp python -c "import urllib.request; print(urllib.request.urlopen('http://orchestrator:8300/healthz', timeout=10).read().decode())"
# Tokens, model calls and cost, from the orchestrator's usage log (one JSON line per finished run), and the
# reports released and lesson plans approved in the window, from the database (SELECT only, the session is
# read-only). Nothing is written to disk: the log goes to the script on stdin, the outcomes through bash.
usage: $(ENV_FILE) ## Tokens, calls and cost per run and per completed outcome:  make usage [SINCE=24h|7d|2026-11-01]
	@CUT=$$(python3 scripts/usage_report.py --print-cutoff --since "$(SINCE)") || exit 1; \
	 FAILED=0; OUTARG=""; \
	 if ! LOG=$$($(COMPOSE) exec -T orchestrator sh -c 'cat "$${USAGE_LOG_FILE:-/var/log/mdeis/usage.jsonl}"' 2>/dev/null); then \
	   echo "No usage file to read yet: no run has finished since the orchestrator started with USAGE_LOG_FILE set, or the orchestrator is not running."; \
	   LOG=""; \
	 fi; \
	 if OUT=$$($(COMPOSE) exec -T postgres sh -c 'PGOPTIONS="-c default_transaction_read_only=on" psql -U "$$POSTGRES_USER" -d "$$MDEIS_DB_NAME" -At -q -v ON_ERROR_STOP=1 -v cut="$$1"' _ "$$CUT" < scripts/usage_outcomes.sql); then \
	   OUTARG=yes; \
	 else \
	   echo "Could not read completed outcomes from the database; the per-run report follows without them." >&2; FAILED=1; \
	 fi; \
	 if [ -n "$$OUTARG" ]; then \
	   printf '%s\n' "$$LOG" | python3 scripts/usage_report.py - --since "$(SINCE)" --outcomes <(printf '%s' "$$OUT") $(if $(FORMAT),--format $(FORMAT)) || exit $$?; \
	 else \
	   printf '%s\n' "$$LOG" | python3 scripts/usage_report.py - --since "$(SINCE)" $(if $(FORMAT),--format $(FORMAT)) || exit $$?; \
	 fi; \
	 exit $$FAILED


token: $(ENV_FILE) ## A five-minute approver token for testing by hand:  make token ID=u-ada NAME="Ada Teacher"
	@test -n "$(ID)" -a -n "$(NAME)" || { echo 'usage: make token ID=u-ada NAME="Ada Teacher"' >&2; exit 1; }
	@$(COMPOSE) exec -T mdeis-mcp python - "$(ID)" "$(NAME)" < scripts/mint_token.py

evals: $(ENV_FILE) ## Run the agent evals (5 runs each; ONLY=E-01 for one) against the live stack; saves the real result in evals/results/
	@REFS=$$($(COMPOSE) exec -T postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$MDEIS_DB_NAME" -At -c "SELECT json_object_agg(school_ref, learner_id) FROM learners"') || exit 1; \
	 MAP=$$(python3 scripts/eval_map.py "$$REFS") || exit 1; \
	 TOKEN=$$($(COMPOSE) exec -T mdeis-mcp python - u-eval "Eval Approver" 7200 < scripts/mint_token.py) || exit 1; \
	 OUT=evals/results/agent-$$(date -u +%Y-%m-%d-%H%M).json; \
	 $(COMPOSE) exec -T -e EVAL_ONLY="$(ONLY)" orchestrator python - "$$MAP" "$$TOKEN" "$$(git rev-parse --short HEAD)" < evals/run_agent_evals.py > "$$OUT" || exit 1; \
	 echo "saved $$OUT"; python3 scripts/summarise_evals.py "$$OUT"; \
	 echo "reports released during the run (expect 0):"; \
	 $(COMPOSE) exec -T postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$MDEIS_DB_NAME" -At -c "SELECT count(*) FROM reports WHERE status = '"'"'released'"'"'"'

ask: $(ENV_FILE) ## Ask the orchestrator directly, no gateway:  make ask Q="How is L-0001 doing in English?" [TOKEN=...] [NEW=1]
	@test -n "$(Q)" || { echo 'usage: make ask Q="your question" [TOKEN=from make token] [NEW=1 for a new conversation]' >&2; exit 1; }
	@$(COMPOSE) exec -T orchestrator python - "$(Q)" "$(TOKEN)" "$(NEW)" < scripts/ask_orchestrator.py

probe-gateway: $(ENV_FILE) ## Gateway: no key and no user are refused, a signed-in user gets an answer
	@$(COMPOSE) exec -T gateway python - < scripts/probe_gateway.py

prod-config: $(ENV_FILE) ## Check the public proxy: the merged compose files and the Caddyfile
	@$(COMPOSE) --profile prod config --quiet && echo "OK: compose files merge with the prod profile"
	@$(COMPOSE) --profile prod run --rm --no-deps proxy caddy validate --config /etc/caddy/Caddyfile

prod-up: $(ENV_FILE) ## Start the public reverse proxy (needs APP_HOST in .env; PROXY_BIND=0.0.0.0 makes it public)
	@$(COMPOSE) --profile prod up -d proxy
	@$(COMPOSE) --profile prod ps proxy

prod-down: $(ENV_FILE) ## Stop the public reverse proxy (the rest of the stack keeps running)
	@$(COMPOSE) --profile prod stop proxy
	@$(COMPOSE) --profile prod rm -f proxy

prod-check: ## From ANOTHER machine: check the public address:  make prod-check HOST=demo.example.org
	@test -n "$(HOST)" || { echo 'usage: make prod-check HOST=demo.example.org' >&2; exit 1; }
	@python3 scripts/check_public.py "$(HOST)"

probe-portal: $(ENV_FILE) ## Portal: its health answer, from inside the network
	@$(COMPOSE) exec -T gateway python -c "import urllib.request; print(urllib.request.urlopen('http://portal:8080/healthz', timeout=10).read().decode())"

probe-signin: $(ENV_FILE) ## Does the upload page's sign-in check work with the real Open WebUI?
	@python3 scripts/probe_signin.py
