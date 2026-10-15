"""The chat model: OpenRouter, an open-weights model, called over plain HTTP.

This is the only model client in our Python code (n8n's HTTP node is the one other model call).
Which model, which provider, and the two privacy switches (`allow_fallbacks` false,
`data_collection` deny) come from the environment through `Settings`, not from code. The reply is
read whole; the answer is streamed to the gateway in chunks afterwards.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

from .config import Settings

REQUEST_TIMEOUT_SECONDS = 120


class ModelError(Exception):
    """The model service did not give a usable answer. The message is safe to show."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # as the model wrote it: a JSON object in text


@dataclass(frozen=True)
class Reply:
    text: str
    calls: tuple = ()


def payload(settings: Settings, messages: list, tools: list) -> dict:
    """The request body. The provider block is what keeps the run on the chosen provider."""
    body = {"model": settings.model, "messages": messages}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    provider = {
        "allow_fallbacks": settings.allow_fallbacks,
        "data_collection": settings.data_collection,
    }
    if settings.provider_order:
        provider["order"] = list(settings.provider_order)
    body["provider"] = provider
    return body


def reply_of(data: dict) -> Reply:
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise ModelError("the model service sent an answer in a form I cannot read") from e
    calls = tuple(
        ToolCall(
            c.get("id") or f"call_{k}",
            c["function"]["name"],
            c["function"].get("arguments") or "{}",
        )
        for k, c in enumerate(message.get("tool_calls") or [])
    )
    return Reply(text=message.get("content") or "", calls=calls)


class OpenRouterModel:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._client = client

    async def complete(self, messages: list, tools: list) -> Reply:
        settings = self.settings
        headers = {
            "Authorization": f"Bearer {settings.model_key}",
            "Content-Type": "application/json",
        }
        client = self._client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
        try:
            response = await client.post(
                f"{settings.model_url}/chat/completions",
                headers=headers,
                content=json.dumps(payload(settings, messages, tools)),
            )
        except httpx.HTTPError as e:
            raise ModelError(f"the model service could not be reached ({type(e).__name__})") from e
        finally:
            if self._client is None:
                await client.aclose()
        if response.status_code != 200:
            raise ModelError(f"the model service answered with an error ({response.status_code})")
        try:
            return reply_of(response.json())
        except ValueError as e:
            raise ModelError("the model service sent an answer that is not JSON") from e
