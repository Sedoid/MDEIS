"""The orchestrator service: the agent behind the gateway.

`POST /run` takes a conversation and the approver's signed token, runs the loop, and streams the
events (`status`, `token`, `final`, `error`) as server-sent events. It makes the run's id, opens a
client for each tool server with the token and the id in the headers, and holds the only model key
in the agent path. It never holds the secret that signs tokens: it can pass one on, not make one.

`POST /lessons` runs the lesson graph (`lessons/graph.py`) for one lesson and answers with JSON. It
takes only what the tutor chose and wrote (stage, term, area, objective, notes) and the signed
token. The author and the school are the token's, so a body that names an author, a school or an
approver is refused. `GET /lessons/{plan_id}` reads one saved plan, with no model. There is no route
here that approves or rejects a plan: that is a human act on another path.

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
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from . import graph, loop
from .config import load_settings
from .lessons import READ_TOOL
from .lessons import graph as lesson_graph
from .mcp_client import McpServer, headers_for
from .model import OpenRouterModel
from .tools import MDEIS, PG

MAX_MESSAGES = 60
MAX_CHARS = 20_000
PLAN_ID = r"^LP-[0-9]{6,}$"  # the form the database makes for a lesson plan number
LESSONS_OFF = "The class view is switched off, so lessons cannot be adapted. Nothing was saved."
NOT_CONFIGURED = (
    "The model is not set up yet, so I cannot answer. The administrator needs to set "
    "OPENROUTER_API_KEY and OPENROUTER_MODEL."
)


class Message(BaseModel):
    role: str
    content: str = Field(max_length=MAX_CHARS)


class LessonRequest(BaseModel):
    """What the tutor chose and wrote, and the signed token. Any other field is refused, so a body
    cannot name an author, a school or an approver."""

    model_config = ConfigDict(extra="forbid")
    approver_token: str = Field(min_length=1, max_length=4000)
    stage: str = Field(min_length=1, max_length=MAX_CHARS)
    area: str = Field(min_length=1, max_length=MAX_CHARS)
    term: int = Field(strict=True)
    objective: str = Field(min_length=1, max_length=MAX_CHARS)
    notes: str = Field(min_length=1, max_length=MAX_CHARS)


class RunRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=200)
    messages: list[Message] = Field(min_length=1, max_length=MAX_MESSAGES)
    approver_token: str | None = Field(default=None, max_length=4000)


def require_key(authorization: Annotated[str | None, Header()] = None) -> None:
    """The shared key, checked before anything else happens. No key set means locked."""
    expected = load_settings().api_key
    if not expected or not hmac.compare_digest(authorization or "", f"Bearer {expected}"):
        raise HTTPException(status_code=401, detail="missing or wrong key")


def engine_name(settings) -> str:
    """What runs the agent: the plain loop, or the graph with or without LangGraph installed."""
    if settings.mode != "graph":
        return "plain loop"
    return "langgraph" if graph.langgraph_available() else "graph without langgraph"


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


@asynccontextmanager
async def open_lesson_servers(settings, token, run_id):
    """The one tool server a lesson needs. No lookup server: the lesson graph has no use for it."""
    async with McpServer(MDEIS, settings.mdeis_url, headers_for(MDEIS, token, run_id)) as mdeis:
        yield {MDEIS: mdeis}


def lesson_answer(outcome: str, statement: str, run_id: str) -> dict:
    """An answer in the shape the graph returns, for a request that never reached it."""
    return {"outcome": outcome, "statement": statement, "run_id": run_id, "plan": None, "steps": []}


def create_app(model_factory=None, servers_factory=None, lesson_servers_factory=None) -> FastAPI:
    """The service. The factories exist so that tests can put a fake model and fake tool servers in
    their place; the real service uses the defaults."""
    model_factory = model_factory or OpenRouterModel
    servers_factory = servers_factory or open_servers
    lesson_servers_factory = lesson_servers_factory or open_lesson_servers
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
            "mode": settings.mode,
            "engine": engine_name(settings),
            "model_configured": settings.model_ready,
        }

    @app.post("/run", dependencies=[Depends(require_key)])
    async def run(body: RunRequest) -> StreamingResponse:
        settings = load_settings()
        run_id = f"RUN-{uuid.uuid4()}"

        runner = graph.run if settings.mode == "graph" else loop.run

        async def stream():
            if not settings.model_ready:
                yield sse("error", {"text": NOT_CONFIGURED})
                return
            try:
                async with servers_factory(settings, body.approver_token, run_id) as servers:
                    async for item in runner(
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

    @app.post("/lessons", dependencies=[Depends(require_key)])
    async def lessons(body: LessonRequest) -> JSONResponse:
        settings = load_settings()
        run_id = f"RUN-{uuid.uuid4()}"
        if not settings.class_view:
            return JSONResponse(lesson_answer("refused", LESSONS_OFF, run_id))
        if not settings.model_ready:
            return JSONResponse(
                lesson_answer("error", NOT_CONFIGURED, run_id), status_code=503
            )
        try:
            async with lesson_servers_factory(settings, body.approver_token, run_id) as servers:
                answer = await lesson_graph.run(
                    stage=body.stage,
                    area=body.area,
                    term=body.term,
                    objective=body.objective,
                    notes=body.notes,
                    run_id=run_id,
                    settings=settings,
                    model=model_factory(settings),
                    servers=servers,
                )
        except Exception as error:  # (the class only: never the message, which could hold notes)
            said = f"I could not finish ({type(error).__name__}). Nothing was saved."
            return JSONResponse(lesson_answer("error", said, run_id), status_code=502)
        return JSONResponse(answer)

    @app.get("/lessons/{plan_id}", dependencies=[Depends(require_key)])
    async def lesson(
        plan_id: Annotated[str, Path(pattern=PLAN_ID)],
        x_approver_token: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        """One saved plan, for its author. The token is the session's; the plan number is all the
        caller chooses. No model is involved."""
        if not x_approver_token:
            raise HTTPException(status_code=401, detail="no one is signed in")
        run_id = f"RUN-{uuid.uuid4()}"
        try:
            async with lesson_servers_factory(
                load_settings(), x_approver_token, run_id
            ) as servers:
                result = await servers[MDEIS].call(READ_TOOL, {"plan_id": plan_id})
        except Exception as error:
            said = f"I could not read the plan ({type(error).__name__})."
            return JSONResponse({"status": "error", "statement": said}, status_code=502)
        return JSONResponse(result)

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
