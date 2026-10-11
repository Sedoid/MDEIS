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
# See `make help` for everything else.

SHELL := /bin/bash
.DEFAULT_GOAL := help

ENV_FILE   ?= .env
DEV_EDITOR ?= 1
UP_FLAGS   ?= --wait
SERVICE    ?=
FORCE      ?= 0
STRICT     ?= 0
ARGS       ?=

SERVICE_FILES := $(sort $(wildcard deploy/compose/*.yml))
ifneq ($(DEV_EDITOR),1)
SERVICE_FILES := $(filter-out %.dev.yml,$(SERVICE_FILES))
endif
COMPOSE := docker compose --env-file $(ENV_FILE) -f docker-compose.yml $(addprefix -f ,$(SERVICE_FILES))

.PHONY: help env-init check-env models dev-config dev-build dev-up dev-down up down dev-ps dev-logs \
        dev-restart dev-psql dev-reset dev-health migrate test lint check \
        probe-embed probe-mcp probe-pg-mcp probe-n8n

help: ## Show this list
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

# ── Setup ────────────────────────────────────────────────────────────

$(ENV_FILE):
	@echo "There is no $(ENV_FILE) yet. Run: make env-init" >&2; exit 1

env-init: ## Create .env from .env.example with random secrets (never overwrites)
	@python3 scripts/env_init.py

check-env: $(ENV_FILE) ## Check .env is complete (STRICT=1 also requires your own values)
	@python3 scripts/check_env.py $(if $(filter 1,$(STRICT)),--strict,)

models: $(ENV_FILE) ## Download the embedding model and verify its SHA-256
	@python3 scripts/fetch_model.py

dev-config: $(ENV_FILE) ## Validate the merged compose files (prints nothing when fine)
	@$(COMPOSE) config --quiet && echo "OK: compose files merge and every required variable is set"

# ── Run ──────────────────────────────────────────────────────────────

dev-build: $(ENV_FILE) ## Build the images we own
	$(COMPOSE) build

dev-up: check-env models ## Build and start everything, wait until healthy
	$(COMPOSE) up -d --build --remove-orphans $(UP_FLAGS)
	@$(COMPOSE) ps

dev-down: $(ENV_FILE) ## Stop everything, keep the data
	$(COMPOSE) down

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

# ── Database, tests, checks ──────────────────────────────────────────

migrate: $(ENV_FILE) ## Apply database migrations (safe to run twice)
	$(COMPOSE) run --rm migrate

test: $(ENV_FILE) ## Run pytest in a container (ARGS="-m integration" to pass options)
	$(COMPOSE) --profile test run --rm --build test pytest $(ARGS)

lint: $(ENV_FILE) ## Run ruff in a container
	$(COMPOSE) --profile test run --rm --build test ruff check src tests

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
