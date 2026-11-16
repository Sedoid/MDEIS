"""The gateway between Open WebUI and the orchestrator.

Open WebUI uses it as its model endpoint (`/v1/chat/completions`, OpenAI style). It does three
things and has no model code in it:

1. It knows who is signed in. Open WebUI forwards the signed-in user's id and name in headers. No
   user means 401: there is no anonymous approver. The identity comes from those headers only, never
   from anything written in the chat.
2. It signs the approver token (five minutes) with the secret that only this service and the
   toolbox hold, and sends it to the orchestrator beside the conversation. The token also carries
   the school code this gateway is set up with (`GATEWAY_SCHOOL_CODE`), when there is one. The
   school never comes from the request.
3. It turns the orchestrator's events (`status`, `token`, `final`, `error`) into OpenAI replies,
   streaming or whole. If the orchestrator fails it says so in the chat in plain words; it never
   makes up an answer.

`POST /lessons` and `GET /lessons/{plan_id}` are a pass-through for the lesson adapter. They take
the same key and the same signed-in user as chat, sign the same token (with the school), and
forward what the tutor chose and wrote to the orchestrator. The body is closed (`extra="forbid"`):
a field that names an author, a school or an approver is refused here. There is no route in this
file that approves or rejects a plan.

It logs the names of headers and the request number, never their values, keys or message text.
"""

import hmac
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from mdeis.core import approver_token

MODEL_ID = "mdeis-assistant"
MODEL_NAME = "Lucent AI"  # what a person sees in the model list; the id above is not shown
USER_ID_HEADER = "X-OpenWebUI-User-Id"
USER_NAME_HEADER = "X-OpenWebUI-User-Name"
CHAT_ID_HEADER = "X-OpenWebUI-Chat-Id"
BACKGROUND_PREFIX = "### Task:"  # how Open WebUI starts its own title/tag/follow-up requests
READ_TIMEOUT_SECONDS = 600
CONNECT_TIMEOUT_SECONDS = 5
HEALTH_TIMEOUT_SECONDS = 3
UNAVAILABLE = (
    "The assistant service is not available right now, so I cannot answer. "
    "Nothing was changed. Please try again in a moment."
)
INTERRUPTED = (
    "The assistant stopped before it finished, so there is no answer. Nothing was changed."
)

PLAN_ID = r"^LP-[0-9]{6,}$"  # the form the database makes for a lesson plan number
APPROVER_TOKEN_HEADER = "X-Approver-Token"
LESSON_UNAVAILABLE = (
    "The lesson service is not available right now. Nothing was saved. "
    "Please try again in a moment."
)

log = logging.getLogger("gateway")


class LessonBody(BaseModel):
    """What the tutor chose and wrote. Nothing else is accepted."""

    model_config = ConfigDict(extra="forbid")
    stage: str = Field(min_length=1)
    area: str = Field(min_length=1)
    term: int = Field(strict=True)
    objective: str = Field(min_length=1)
    notes: str = Field(min_length=1)


@dataclass(frozen=True)
class Settings:
    api_key: str
    secret: str
    orchestrator_url: str
    orchestrator_key: str
    user_id_header: str
    user_name_header: str
    school: str = ""  # the school code this gateway serves (GATEWAY_SCHOOL_CODE); "" means none


def load_settings(env=None) -> Settings:
    env = os.environ if env is None else env
    return Settings(
        api_key=env.get("GATEWAY_API_KEY", ""),
        secret=env.get("APPROVER_TOKEN_SECRET", ""),
        orchestrator_url=(env.get("ORCHESTRATOR_URL") or "http://orchestrator:8300").rstrip("/"),
        orchestrator_key=env.get("ORCHESTRATOR_API_KEY", ""),
        user_id_header=env.get("GATEWAY_USER_ID_HEADER") or USER_ID_HEADER,
        user_name_header=env.get("GATEWAY_USER_NAME_HEADER") or USER_NAME_HEADER,
        school=(env.get("GATEWAY_SCHOOL_CODE") or "").strip(),
    )


def require_key(authorization: Annotated[str | None, Header()] = None) -> None:
    """The key Open WebUI holds. No key set means the service is locked."""
    expected = load_settings().api_key
    if not expected or not hmac.compare_digest(authorization or "", f"Bearer {expected}"):
        raise HTTPException(status_code=401, detail="missing or wrong key")


def text_of(content) -> str:
    """A message's text: Open WebUI may send a list of parts instead of a string."""
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content if isinstance(content, str) else ""


SEEN = "[a chart was shown here]"
PICTURE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def clean_messages(messages: list) -> list:
    """The messages as plain text. A chart we showed is a long embedded picture: it is taken out of
    the history here, so it is not sent on again with every later question."""
    return [
        {"role": m["role"], "content": PICTURE.sub(SEEN, text_of(m.get("content")))}
        for m in messages
        if isinstance(m, dict) and isinstance(m.get("role"), str)
    ]


def is_background(messages: list) -> bool:
    """Open WebUI's own housekeeping requests (titles, tags, follow-ups) are not questions."""
    last = next((m for m in reversed(messages) if m["role"] == "user"), None)
    return bool(last) and last["content"].lstrip().startswith(BACKGROUND_PREFIX)


def completion(request_id: str, text: str) -> dict:
    return {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
    }


def chunk(request_id: str, delta: dict, finish: str | None = None) -> str:
    body = {
        "id": request_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"


async def events_from(response: httpx.Response):
    """The (name, data) events of the orchestrator's server-sent stream, in order."""
    name = None
    async for line in response.aiter_lines():
        if line.startswith("event: "):
            name = line[len("event: ") :]
        elif line.startswith("data: ") and name:
            yield name, json.loads(line[len("data: ") :])
            name = None


def create_app(client_factory=None) -> FastAPI:
    """The service. `client_factory` lets tests put a mock orchestrator in place of the real one."""
    app = FastAPI(title="MDEIS gateway")
    make_client = client_factory or (
        lambda: httpx.AsyncClient(
            timeout=httpx.Timeout(READ_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)
        )
    )

    @app.get("/healthz")
    async def healthz(response: Response) -> dict:
        settings = load_settings()
        problems = []
        try:
            async with make_client() as client:
                reply = await client.get(
                    f"{settings.orchestrator_url}/healthz", timeout=HEALTH_TIMEOUT_SECONDS
                )
            if reply.status_code != httpx.codes.OK:
                problems.append(f"orchestrator: status {reply.status_code}")
        except httpx.HTTPError as e:
            problems.append(f"orchestrator: {type(e).__name__}")
        if not settings.secret:
            problems.append("no signing secret")
        if problems:
            response.status_code = httpx.codes.SERVICE_UNAVAILABLE
        return {"status": "unavailable" if problems else "ok", "problems": problems}

    @app.get("/v1/models", dependencies=[Depends(require_key)])
    def models() -> dict:
        model = {"id": MODEL_ID, "name": MODEL_NAME, "object": "model", "owned_by": "mdeis"}
        return {"object": "list", "data": [model]}

    @app.post("/v1/chat/completions", dependencies=[Depends(require_key)])
    async def chat(request: Request):
        settings = load_settings()
        request_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"
        log.info("request %s, headers %s", request_id, sorted(request.headers.keys()))
        user_id = request.headers.get(settings.user_id_header, "").strip()
        user_name = request.headers.get(settings.user_name_header, "").strip() or user_id
        if not user_id:
            return JSONResponse(
                status_code=401,
                content={"error": {"message": "nobody is signed in", "type": "unauthorized"}},
            )
        try:
            body = await request.json()
            messages = clean_messages(body["messages"])
            stream = bool(body.get("stream"))
        except (ValueError, KeyError, TypeError):
            return bad_request()
        if not messages:
            return bad_request()
        if is_background(messages):
            return reply_whole(request_id, "") if not stream else stream_text(request_id, "")
        try:
            token = approver_token.sign(
                settings.secret, user_id, user_name, school=settings.school or None
            )
        except approver_token.TokenError:
            return JSONResponse(
                status_code=500,
                content={"error": {"message": "the gateway cannot sign tokens", "type": "server"}},
            )
        run = {
            "conversation_id": request.headers.get(CHAT_ID_HEADER) or f"chat-{uuid.uuid4().hex}",
            "messages": messages,
            "approver_token": token,
        }
        events = orchestrator_events(make_client, settings, run)
        if stream:
            return StreamingResponse(
                stream_events(request_id, events), media_type="text/event-stream"
            )
        return reply_whole(request_id, await whole_answer(events))

    def signed_in(request: Request, settings: Settings):
        """(token, None) for the signed-in user, or (None, the 401 reply)."""
        user_id = request.headers.get(settings.user_id_header, "").strip()
        user_name = request.headers.get(settings.user_name_header, "").strip() or user_id
        if not user_id:
            return None, JSONResponse(
                status_code=401,
                content={"error": {"message": "nobody is signed in", "type": "unauthorized"}},
            )
        try:
            token = approver_token.sign(
                settings.secret, user_id, user_name, school=settings.school or None
            )
        except approver_token.TokenError:
            return None, JSONResponse(
                status_code=500,
                content={"error": {"message": "the gateway cannot sign tokens", "type": "server"}},
            )
        return token, None

    async def to_orchestrator(settings: Settings, send):
        """Send one request to the orchestrator; hand its JSON back, or say plainly it is down."""
        try:
            async with make_client() as client:
                reply = await send(client, {"Authorization": f"Bearer {settings.orchestrator_key}"})
            return JSONResponse(status_code=reply.status_code, content=reply.json())
        except (httpx.HTTPError, ValueError):
            return JSONResponse(
                status_code=httpx.codes.BAD_GATEWAY,
                content={"outcome": "error", "statement": LESSON_UNAVAILABLE, "plan": None},
            )

    @app.post("/lessons", dependencies=[Depends(require_key)])
    async def lessons(request: Request, body: LessonBody):
        settings = load_settings()
        token, refusal = signed_in(request, settings)
        if refusal:
            return refusal
        run = {**body.model_dump(), "approver_token": token}

        def send(client, headers):
            return client.post(f"{settings.orchestrator_url}/lessons", json=run, headers=headers)

        return await to_orchestrator(settings, send)

    @app.get("/lessons/{plan_id}", dependencies=[Depends(require_key)])
    async def lesson(request: Request, plan_id: Annotated[str, Path(pattern=PLAN_ID)]):
        settings = load_settings()
        token, refusal = signed_in(request, settings)
        if refusal:
            return refusal
        headers_in = {APPROVER_TOKEN_HEADER: token}

        def send(client, headers):
            return client.get(
                f"{settings.orchestrator_url}/lessons/{plan_id}", headers={**headers, **headers_in}
            )

        return await to_orchestrator(settings, send)

    return app


def bad_request() -> JSONResponse:
    message = {"message": "a chat needs a list of messages", "type": "invalid"}
    return JSONResponse(status_code=httpx.codes.BAD_REQUEST, content={"error": message})


def reply_whole(request_id: str, text: str) -> JSONResponse:
    return JSONResponse(completion(request_id, text))


def stream_text(request_id: str, text: str) -> StreamingResponse:
    async def one():
        yield chunk(request_id, {"role": "assistant", "content": text})
        yield chunk(request_id, {}, "stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(one(), media_type="text/event-stream")


async def orchestrator_events(make_client, settings: Settings, run: dict):
    """The orchestrator's events for one run. A failure becomes one `error` event in plain words."""
    headers = {"Authorization": f"Bearer {settings.orchestrator_key}"}
    try:
        async with make_client() as client:
            async with client.stream(
                "POST", f"{settings.orchestrator_url}/run", json=run, headers=headers
            ) as response:
                if response.status_code != httpx.codes.OK:
                    yield "error", {"text": UNAVAILABLE}
                    return
                async for item in events_from(response):
                    yield item
    except (httpx.HTTPError, ValueError):
        yield "error", {"text": UNAVAILABLE}


async def whole_answer(events) -> str:
    """The finished answer of a run, the plain error, or a note that it stopped without one."""
    answer = None
    async for name, data in events:
        if name == "final":
            answer = data["answer"]
        elif name == "error":
            return data["text"]
    return answer if answer is not None else INTERRUPTED


STEPS = {  # what a step is called to a person, not to a programmer
    "ingest_assessments": "Reading the learner's records…",
    "compute_trend": "Working out a trend…",
    "make_chart": "Preparing a chart…",
    "get_criterion": "Looking up the rulebook…",
    "draft_summary": "Writing the draft…",
    "check_citations": "Checking the sources…",
    "resolve_issue": "Recording your answer…",
    "release_report": "Releasing the report…",
    "class_baseline": "Counting the class…",
    "stage_gap_counts": "Counting the class…",
    "execute_sql": "Looking up a learner number…",
}
USED = re.compile(r"^Used (\w+)\.$")


def progress_words(text: str) -> str:
    """A status line from the agent in plain words. A tool this list does not know is not named."""
    used = USED.match(text)
    if not used:
        return text
    return STEPS.get(used.group(1), "Working…")


def show_progress() -> bool:
    off = ("0", "false", "no", "off")
    return os.environ.get("SHOW_PROGRESS", "true").strip().lower() not in off


async def stream_events(request_id: str, events):
    """Open WebUI shows `reasoning_content` as a live "Thinking" block, so what the agent is doing
    is visible while it works, and it closes by itself when the answer starts."""
    yield chunk(request_id, {"role": "assistant", "content": ""})
    sent, finished, last = False, False, ""
    async for name, data in events:
        if name == "token":
            sent = True
            yield chunk(request_id, {"content": data["text"]})
        elif name == "final":
            if not sent:  # a refusal or a short answer that came without tokens
                yield chunk(request_id, {"content": data["answer"]})
            finished = True
        elif name == "error":
            yield chunk(request_id, {"content": data["text"]})
            finished = True
        elif name == "status":
            words = progress_words(data.get("text", ""))
            if show_progress() and not sent and words and words != last:
                last = words
                yield chunk(request_id, {"reasoning_content": f"{words}\n"})
            else:
                yield ": working\n\n"  # keeps the connection open; Open WebUI ignores comments
    if not finished and not sent:
        yield chunk(request_id, {"content": INTERRUPTED})
    yield chunk(request_id, {}, "stop")
    yield "data: [DONE]\n\n"


app = create_app()
