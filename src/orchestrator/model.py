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
# The usage fields, as OpenRouter names them. `cost` is in USD.
# `reasoning_tokens` is read from `completion_tokens_details` when the service reports it there.
USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens", "cost", "reasoning_tokens")
MAX_PROVIDER_CHARS = 80


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
    usage: dict | None = None  # tokens and the USD cost as the service reported them (tracing)
    provider: str | None = None  # which provider served the call (tracing only)
    reasoning: str | None = None  # the reasoning text, if the service sent it (tracing only)


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
    body["usage"] = {"include": True}  # ask for the token counts and the cost in USD
    return body


def usage_of(data: dict) -> dict | None:
    """The token counts and cost the service reported. A value that is not a plain non-negative
    number is left out; nothing is worked out here, and there is no price table."""
    block = data.get("usage")
    if not isinstance(block, dict):
        return None
    details = block.get("completion_tokens_details")
    if isinstance(details, dict):
        block = {"reasoning_tokens": details.get("reasoning_tokens"), **block}
    found = {
        key: block[key]
        for key in USAGE_KEYS
        if isinstance(block.get(key), int | float)
        and not isinstance(block[key], bool)
        and block[key] >= 0
    }
    return found or None


def reasoning_of(message: dict) -> str | None:
    """The reasoning text, from where OpenRouter puts it: `reasoning`, `reasoning_content`, or the
    text and summary parts of `reasoning_details` (an encrypted part is skipped). Blank is None."""
    for key in ("reasoning", "reasoning_content"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value
    parts = message.get("reasoning_details")
    if isinstance(parts, list):
        texts = [
            part.get("text") or part.get("summary")
            for part in parts
            if isinstance(part, dict)
        ]
        joined = "\n".join(t for t in texts if isinstance(t, str) and t.strip())
        return joined or None
    return None


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
    provider = data.get("provider")
    if not isinstance(provider, str) or len(provider) > MAX_PROVIDER_CHARS:
        provider = None
    return Reply(
        text=message.get("content") or "", calls=calls, usage=usage_of(data), provider=provider,
        reasoning=reasoning_of(message),
    )


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
