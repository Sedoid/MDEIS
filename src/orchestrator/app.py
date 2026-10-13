"""The orchestrator service (docs/CONTAINERS.md section 2): the agent behind the gateway.

`POST /run` takes a conversation and the approver's signed token, runs the loop, and streams the
events (`status`, `token`, `final`, `error`) as server-sent events. It makes the run's id, opens a
client for each tool server with the token and the id in the headers, and holds the only model key
in the agent path. It never holds the secret that signs tokens: it can pass one on, not make one.

It logs the names of things and never message text, tokens or keys.
"""

import hmac
import json
import os
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Annotated

import httpx
import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import loop
from .config import load_settings
from .mcp_client import McpServer, headers_for
from .model import OpenRouterModel
from .tools import MDEIS, PG

MAX_MESSAGES = 60
MAX_CHARS = 20_000
NOT_CONFIGURED = (
    "The model is not set up yet, so I cannot answer. The administrator needs to set "
    "OPENROUTER_API_KEY and OPENROUTER_MODEL."
)


class Message(BaseModel):
    role: str
    content: str = Field(max_length=MAX_CHARS)


class RunRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=200)
    messages: list[Message] = Field(min_length=1, max_length=MAX_MESSAGES)
    approver_token: str | None = Field(default=None, max_length=4000)


def require_key(authorization: Annotated[str | None, Header()] = None) -> None:
    """The shared key, checked before anything else happens. No key set means locked."""
    expected = load_settings().api_key
    if not expected or not hmac.compare_digest(authorization or "", f"Bearer {expected}"):
        raise HTTPException(status_code=401, detail="missing or wrong key")


def sse(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@asynccontextmanager
async def open_servers(settings, token, run_id):
    """The tool servers for one run. The Postgres lookup server is optional: without it the run
    goes on with the school's own tools."""
    async with AsyncExitStack() as stack:
        mdeis = McpServer(MDEIS, settings.mdeis_url, headers_for(MDEIS, token, run_id))
        servers = {MDEIS: await stack.enter_async_context(mdeis)}
        lookup = McpServer(PG, settings.pg_url, headers_for(PG, None, run_id), sse=True)
        try:
            servers[PG] = await stack.enter_async_context(lookup)
        except Exception:  # the lookup server is down: go on without it
            pass
        yield servers


def create_app(model_factory=None, servers_factory=None) -> FastAPI:
    """The service. The two factories exist so that tests can put a fake model and fake tool
    servers in their place; the real service uses the defaults."""
    model_factory = model_factory or OpenRouterModel
    servers_factory = servers_factory or open_servers
    app = FastAPI(title="MDEIS orchestrator")

    @app.get("/healthz")
    def healthz(response: Response) -> dict:
        problems = _problems()
        if problems:
            response.status_code = 503
        settings = load_settings()
        return {
            "status": "unavailable" if problems else "ok",
            "problems": problems,
            "mode": "linear",
            "model_configured": settings.model_ready,
        }

    @app.post("/run", dependencies=[Depends(require_key)])
    async def run(body: RunRequest) -> StreamingResponse:
        settings = load_settings()
        run_id = f"RUN-{uuid.uuid4()}"

        async def stream():
            if not settings.model_ready:
                yield sse("error", {"text": NOT_CONFIGURED})
                return
            try:
                async with servers_factory(settings, body.approver_token, run_id) as servers:
                    async for item in loop.run(
                        messages=[m.model_dump() for m in body.messages],
                        token=body.approver_token,
                        run_id=run_id,
                        settings=settings,
                        model=model_factory(settings),
                        servers=servers,
                    ):
                        yield sse(item["event"], item["data"])
            except Exception as error:  # (the class only: never the message)
                yield sse("error", {"text": f"I could not finish ({type(error).__name__})."})

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


def _problems() -> list:
    found = []
    try:
        psycopg.connect(os.environ["ORCHESTRATOR_DATABASE_URL"], connect_timeout=3).close()
    except Exception as e:  # (any failure means not healthy; the class name is enough)
        found.append(f"own database: {type(e).__name__}")
    try:
        base = load_settings().mdeis_url.rsplit("/mcp", 1)[0]
        httpx.get(f"{base}/healthz", timeout=3).raise_for_status()
    except Exception as e:
        found.append(f"mdeis-mcp: {type(e).__name__}")
    return found


app = create_app()
