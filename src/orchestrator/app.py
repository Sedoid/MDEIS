"""STUB for the orchestrator (the agent service).

It answers /healthz honestly: healthy only when it can open its own database and reach mdeis-mcp.
POST /run checks the shared key and then says it is not built. The LangGraph agent and the plain
tool-calling loop come in T-042 and T-011. No model call happens here yet.
"""
import hmac
import os
from typing import Annotated

import httpx
import psycopg
from fastapi import FastAPI, Header, HTTPException, Response

app = FastAPI(title="MDEIS orchestrator (stub)")


def _problems() -> list[str]:
    found: list[str] = []
    try:
        psycopg.connect(os.environ["ORCHESTRATOR_DATABASE_URL"], connect_timeout=3).close()
    except Exception as e:  # (any failure means not healthy; the class name is enough)
        found.append(f"own database: {type(e).__name__}")
    try:
        base = os.environ.get("MDEIS_MCP_URL", "http://mdeis-mcp:8100/mcp").rsplit("/mcp", 1)[0]
        httpx.get(f"{base}/healthz", timeout=3).raise_for_status()
    except Exception as e:
        found.append(f"mdeis-mcp: {type(e).__name__}")
    return found


@app.get("/healthz")
def healthz(response: Response) -> dict:
    problems = _problems()
    if problems:
        response.status_code = 503
    return {"status": "unavailable" if problems else "ok", "problems": problems}


@app.post("/run")
def run(authorization: Annotated[str | None, Header()] = None) -> dict:
    expected = os.environ.get("ORCHESTRATOR_API_KEY", "")
    if not expected or not hmac.compare_digest(authorization or "", f"Bearer {expected}"):
        raise HTTPException(status_code=401, detail="missing or wrong key")
    raise HTTPException(status_code=501, detail="not built yet (T-042)")
