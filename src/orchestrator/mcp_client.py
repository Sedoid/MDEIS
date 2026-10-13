"""The tool servers, reached over MCP, with a client made for each run.

The approver's token travels in the `X-Approver-Token` header of every call to `mdeis-mcp`, and only
there: the borrowed Postgres server never gets it, the model never sees it, and it is not an
argument of any tool. Both servers get the same `X-Run-Id` for the whole run, so the diary groups
the run's calls and `check_citations` knows which results belong to it.
"""

from __future__ import annotations

import json
from contextlib import AsyncExitStack

from .tools import MDEIS, PG

TOKEN_HEADER = "X-Approver-Token"
RUN_HEADER = "X-Run-Id"


def headers_for(server: str, token: str | None, run_id: str) -> dict:
    """The headers a server gets: the run for both, the approver's token for mdeis-mcp only."""
    headers = {RUN_HEADER: run_id}
    if server == MDEIS and token:
        headers[TOKEN_HEADER] = token
    return headers


class McpServer:
    """One open MCP session. Use as `async with McpServer(...) as server`."""

    def __init__(self, name: str, url: str, headers: dict, sse: bool = False):
        self.name, self.url, self.headers, self.sse = name, url, headers, sse
        self._stack = None
        self._session = None

    async def __aenter__(self):
        from mcp import ClientSession

        self._stack = AsyncExitStack()
        try:
            if self.sse:
                from mcp.client.sse import sse_client

                read, write = await self._stack.enter_async_context(
                    sse_client(self.url, headers=self.headers)
                )
            else:
                from mcp.client.streamable_http import streamablehttp_client

                read, write, _ = await self._stack.enter_async_context(
                    streamablehttp_client(self.url, headers=self.headers)
                )
            self._session = await self._stack.enter_async_context(ClientSession(read, write))
            await self._session.initialize()
        except BaseException:
            await self._stack.aclose()
            raise
        return self

    async def __aexit__(self, *exc):
        await self._stack.aclose()

    async def list_tools(self) -> list:
        listed = await self._session.list_tools()
        return [
            {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
            for t in listed.tools
        ]

    async def call(self, name: str, arguments: dict) -> dict:
        result = await self._session.call_tool(name, arguments)
        return result_of(result)


def result_of(result) -> dict:
    """A tool result as a plain dict: its structured content, else its text read as JSON."""
    data = getattr(result, "structuredContent", None)
    if not data:
        text = "".join(c.text for c in result.content if getattr(c, "type", "") == "text")
        try:
            data = json.loads(text)
        except ValueError:
            data = {"text": text}
    if not isinstance(data, dict):
        data = {"result": data}
    if getattr(result, "isError", False):
        return {"status": "error", "detail": data}
    return data
