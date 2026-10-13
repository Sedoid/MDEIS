"""The portal: an upload page for signed-in people. It keeps files and records them, nothing more.

    GET  /                       the page            GET  /api/config   where the chat is
    GET  /api/me                 who is signed in    GET  /api/csrf     a token for this user
    POST /api/documents?kind=    upload a file       GET  /api/documents[/D-000001]  status
    GET  /healthz

Who is signed in comes from Open WebUI (auth.py). A file is checked, written to the inbox under a
name we make, and then a `documents` row is added as `pending`; the ingest worker does the reading.
Nothing here reads a card, calls a model or holds a model key or the approver-token secret.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import security
from .auth import OpenWebUIAuth, User
from .settings import MB, Settings, load_settings
from .store import PgStore

log = logging.getLogger("portal")
STATIC = Path(__file__).parent / "static"
KINDS = {"results": (".pdf",), "methodology": (".pdf", ".md")}
PDF_START = b"%PDF-"
DOCUMENT_ID = re.compile(r"^D-[0-9]{6,}$")


def _say(status: int, message: str, **more) -> JSONResponse:
    return JSONResponse({"error": message, **more}, status_code=status)


def create_app(settings: Settings | None = None, auth=None, store=None) -> FastAPI:
    config = settings or load_settings()
    auth = auth or OpenWebUIAuth(config.webui_url)
    store = store or PgStore(config.database_url)
    app = FastAPI(title="MDEIS portal", docs_url=None, redoc_url=None, openapi_url=None)

    policy = security.csp(config.webui_public_url if config.frame_chat else None)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.update(security.HEADERS)
        response.headers["Content-Security-Policy"] = policy
        return response

    async def who(request: Request) -> User | None:
        token = request.cookies.get(config.cookie_name)
        bearer = request.headers.get("authorization", "")
        if not token and bearer.lower().startswith("bearer "):
            token = bearer[7:].strip()
        return await auth.user_for(token)

    def own_origin(request: Request) -> bool:
        origin = request.headers.get("origin")
        host = request.headers.get("host", "")
        if security.same_origin(origin, request.url.scheme, host):
            return True
        return bool(config.portal_public_url) and origin == config.portal_public_url

    @app.get("/healthz")
    async def healthz(response: Response) -> dict:
        found = []
        if (problem := await run_in_threadpool(store.problem)) is not None:
            found.append(problem)
        if not os.access(config.inbox, os.W_OK):
            found.append("inbox folder is not writable")
        try:
            await run_in_threadpool(
                urllib.request.urlopen, f"{config.webui_url}/health", None, 3
            )
        except Exception as error:  # any failure means not healthy; the class name is enough
            found.append(f"webui: {type(error).__name__}")
        if found:
            response.status_code = 503
        return {"status": "unavailable" if found else "ok", "problems": found}

    @app.get("/")
    async def page() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/api/config")
    async def get_config() -> dict:
        return {
            "chat_url": config.webui_public_url,
            "frame_chat": config.frame_chat,
            "max_mb": config.max_bytes // MB,
            "max_markdown_mb": config.max_markdown_bytes // MB,
        }

    @app.get("/api/me")
    async def me(request: Request):
        user = await who(request)
        if user is None:
            return _say(401, "Sign in to Open WebUI first.")
        return {"id": user.id, "name": user.name, "admin": user.is_admin}

    @app.get("/api/csrf")
    async def csrf(request: Request):
        user = await who(request)
        if user is None:
            return _say(401, "Sign in to Open WebUI first.")
        return {"csrf": security.csrf_token(config.session_secret, user.id)}

    @app.get("/api/documents")
    async def documents(request: Request):
        user = await who(request)
        if user is None:
            return _say(401, "Sign in to Open WebUI first.")
        rows = await run_in_threadpool(store.list, None if user.is_admin else user.id)
        return {"documents": rows}

    @app.get("/api/documents/{document_id}")
    async def document(document_id: str, request: Request):
        user = await who(request)
        if user is None:
            return _say(401, "Sign in to Open WebUI first.")
        found = DOCUMENT_ID.match(document_id) and await run_in_threadpool(store.get, document_id)
        if not found or not (user.is_admin or found.get("uploader_id") == user.id):
            return _say(404, "There is no such document.")
        return {k: v for k, v in found.items() if k != "uploader_id"}

    @app.post("/api/documents")
    async def upload(request: Request, kind: str = ""):
        user = await who(request)
        if user is None:
            return _say(401, "Sign in to Open WebUI first.")
        if kind not in KINDS:
            return _say(400, "The kind must be results or methodology.")
        if kind == "methodology" and not user.is_admin:
            return _say(403, "Only an administrator may upload the methodology.")
        if not own_origin(request) or not security.csrf_ok(
            config.session_secret, user.id, request.headers.get("x-csrf-token")
        ):
            return _say(403, "This request did not come from the page. Reload the page.")

        shown = security.clean_name(unquote(request.headers.get("x-file-name", "")))
        extension = Path(shown).suffix.lower()
        if extension not in KINDS[kind]:
            return _say(415, "That file type is not accepted here.", accepted=list(KINDS[kind]))
        cap = config.max_markdown_bytes if extension == ".md" else config.max_bytes
        if int(request.headers.get("content-length") or 0) > cap:
            return _say(413, "That file is too large.", limit_mb=cap / MB)

        stored = f"{uuid.uuid4().hex}{extension}"
        final, temp = Path(config.inbox) / stored, Path(config.inbox) / f".{stored}.part"
        digest, size, start = hashlib.sha256(), 0, b""
        try:
            with open(temp, "wb") as out:
                async for block in request.stream():
                    size += len(block)
                    if size > cap:
                        return _say(413, "That file is too large.", limit_mb=cap / MB)
                    if len(start) < len(PDF_START):
                        start = (start + block)[: len(PDF_START)]
                    digest.update(block)
                    out.write(block)
            if size == 0:
                return _say(400, "The file is empty.")
            if not _looks_right(extension, start, temp):
                return _say(415, "The contents are not what the file type says.")
            os.replace(temp, final)
        finally:
            temp.unlink(missing_ok=True)
        try:
            document_id = await run_in_threadpool(
                lambda: store.add(
                    kind=kind, original_name=shown, stored_name=stored, sha256=digest.hexdigest(),
                    size_bytes=size, uploader_id=user.id, uploader_name=user.name,
                )
            )
        except Exception as error:
            final.unlink(missing_ok=True)
            log.error("document not recorded: %s", type(error).__name__)
            return _say(503, "The file could not be recorded. Nothing was kept. Try again.")
        log.info("document %s (%s) from user %s", document_id, kind, user.id)
        return JSONResponse({"document_id": document_id, "status": "pending"}, status_code=201)

    return app


def _looks_right(extension: str, start: bytes, path: Path) -> bool:
    if extension == ".pdf":
        return start == PDF_START
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return False
    return "\x00" not in text


app = create_app()
