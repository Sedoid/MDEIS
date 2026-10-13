"""STUB for the gateway between Open WebUI and n8n.

Only /healthz and the model list, so the container starts and Open WebUI can connect.
It does not answer chats yet: mcp-engineer builds the real behaviour in T-029.
"""
from fastapi import FastAPI

app = FastAPI(title="MDEIS gateway (stub)")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/v1/models")
def models() -> dict:
    model = {"id": "mdeis-assistant", "object": "model", "owned_by": "mdeis"}
    return {"object": "list", "data": [model]}
