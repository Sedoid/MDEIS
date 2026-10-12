"""STUB for the portal (browser-facing upload page).

/healthz is honest: healthy only when the database role connects, the inbox folder is writable and
Open WebUI answers. The page says the upload boxes are not built. The real page (upload boxes,
status list, sign-in check, chat pane) comes in T-036 to T-039.
"""
import os
import urllib.request

import psycopg
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse

app = FastAPI(title="MDEIS portal (stub)")


def _problems() -> list[str]:
    found: list[str] = []
    try:
        psycopg.connect(os.environ["PORTAL_DATABASE_URL"], connect_timeout=3).close()
    except Exception as e:  # (any failure means not healthy; the class name is enough)
        found.append(f"database: {type(e).__name__}")
    if not os.access(os.environ.get("INBOX_DIR", "/inbox"), os.W_OK):
        found.append("inbox folder is not writable")
    try:
        webui = os.environ.get("WEBUI_URL", "http://webui:8080")
        urllib.request.urlopen(f"{webui}/health", timeout=3)
    except Exception as e:
        found.append(f"webui: {type(e).__name__}")
    return found


@app.get("/healthz")
def healthz(response: Response) -> dict:
    problems = _problems()
    if problems:
        response.status_code = 503
    return {"status": "unavailable" if problems else "ok", "problems": problems}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (
        "<!doctype html><title>MDEIS upload</title>"
        "<h1>MDEIS upload page</h1>"
        "<p>Stub. The upload boxes and the status list are not built yet.</p>"
    )
