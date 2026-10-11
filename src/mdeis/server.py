"""STUB entry point for the MDEIS MCP server (Streamable HTTP).

This only exists so the container starts and the network checks can run. It serves /healthz and
/mcp with one placeholder tool. mcp-engineer replaces it in T-006 and the tasks after it.
Written against mcp 1.x (FastMCP). Not run yet: first run is on the server.
"""
import os

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

mcp = FastMCP(
    "mdeis",
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_PORT", "8100")),
)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.tool()
def ping() -> dict:
    """Placeholder tool. Confirms the server is reachable. Removed when the real tools land."""
    return {"status": "ok", "server": "mdeis-mcp", "stub": True}


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
