"""Settings, read from the environment when a run starts (never at import, never from code).

Every value the model service and the tool servers need comes from `.env` through the compose file.
The model's name, the provider order and the two privacy switches are configuration, not code
(.claude/rules/deploy.md). Nothing here is a school rule.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_MAX_STEPS = 12  # model calls in one run before the loop stops
DEFAULT_MAX_REDRAFTS = 2  # times a draft that fails the citation check is written again
DEFAULT_MDEIS_URL = "http://mdeis-mcp:8100/mcp"
DEFAULT_PG_URL = "http://pg-mcp-ro:8200/sse"
DEFAULT_OPENROUTER_URL = "https://openrouter.ai/api/v1"
# The read-only lookups of the borrowed Postgres server that the model may see. A name not listed
# here is never offered, even if the server has it.
DEFAULT_PG_TOOLS = "list_schemas,list_objects,get_object_details,execute_sql"
MODES = ("linear", "graph")


def _flag(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    api_key: str
    mode: str
    max_steps: int
    max_redrafts: int
    mdeis_url: str
    pg_url: str
    pg_tools: tuple
    model_key: str
    model: str
    model_url: str
    provider_order: tuple
    allow_fallbacks: bool
    data_collection: str

    @property
    def model_ready(self) -> bool:
        return bool(self.model_key and self.model)


def load_settings(env=None) -> Settings:
    env = os.environ if env is None else env

    def names(key, default=""):
        return tuple(n.strip() for n in env.get(key, default).split(",") if n.strip())

    mode = env.get("ORCHESTRATOR_MODE", "linear").strip().lower() or "linear"
    return Settings(
        api_key=env.get("ORCHESTRATOR_API_KEY", ""),
        mode=mode if mode in MODES else "linear",
        max_steps=int(env.get("ORCHESTRATOR_MAX_STEPS") or DEFAULT_MAX_STEPS),
        max_redrafts=int(env.get("ORCHESTRATOR_MAX_REDRAFTS") or DEFAULT_MAX_REDRAFTS),
        mdeis_url=env.get("MDEIS_MCP_URL") or DEFAULT_MDEIS_URL,
        pg_url=env.get("PG_MCP_URL") or DEFAULT_PG_URL,
        pg_tools=names("PG_MCP_ALLOWED_TOOLS", DEFAULT_PG_TOOLS),
        model_key=env.get("OPENROUTER_API_KEY", ""),
        model=env.get("OPENROUTER_MODEL", ""),
        model_url=(env.get("OPENROUTER_BASE_URL") or DEFAULT_OPENROUTER_URL).rstrip("/"),
        provider_order=names("OPENROUTER_PROVIDER"),
        allow_fallbacks=_flag(env.get("OPENROUTER_ALLOW_FALLBACKS", "false")),
        data_collection=env.get("OPENROUTER_DATA_COLLECTION", "deny").strip() or "deny",
    )
