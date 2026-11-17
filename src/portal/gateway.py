"""The portal's one outbound call besides Open WebUI: to the gateway, for the Lessons page.

The portal checks who is signed in (with Open WebUI) and passes that person to the gateway in the
same two headers Open WebUI uses. The gateway signs the pass for them and adds every key the tool
server needs; the portal holds only the key for the gateway's portal routes. The portal has no model
client, no model key and no signing secret. A body is passed on exactly as the page made it, after
the portal has checked it holds only what the tutor chose.

A failure of any kind is one plain answer with no text from the failure in it.
"""

from __future__ import annotations

import httpx

USER_ID_HEADER = "X-OpenWebUI-User-Id"
USER_NAME_HEADER = "X-OpenWebUI-User-Name"
READ_TIMEOUT_SECONDS = 620  # a new lesson waits for a model; the gateway waits up to 600
CONNECT_TIMEOUT_SECONDS = 5
UNAVAILABLE = (
    "The lesson service is not available right now. Nothing was changed. "
    "Please try again in a moment."
)
UNAVAILABLE_BODY = {"status": "refused", "reason": "service_unavailable", "detail": UNAVAILABLE}


class GatewayClient:
    """Asks the gateway on behalf of one signed-in person."""

    def __init__(self, base_url: str, key: str, client: httpx.AsyncClient | None = None):
        self.base_url, self.key, self._client = (base_url or "").rstrip("/"), key or "", client

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.key)

    async def request(self, method: str, path: str, user, body: dict | None = None) -> tuple:
        """(status, answer). `user` has `id` and `name`: the verified sign-in, nothing else."""
        headers = {
            "Authorization": f"Bearer {self.key}",
            USER_ID_HEADER: user.id,
            USER_NAME_HEADER: user.name,
        }
        client = self._client or httpx.AsyncClient(
            timeout=httpx.Timeout(READ_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)
        )
        try:
            reply = await client.request(method, self.base_url + path, headers=headers, json=body)
            answer = reply.json()
        except (httpx.HTTPError, ValueError):
            return 502, dict(UNAVAILABLE_BODY)
        finally:
            if self._client is None:
                await client.aclose()
        if not isinstance(answer, dict):
            return 502, dict(UNAVAILABLE_BODY)
        return reply.status_code, answer

    async def download(self, method: str, path: str, user) -> tuple:
        """(status, answer) for a call whose good answer is a file. The answer is the file's bytes
        for a PDF, or the gateway's JSON refusal as a dict. Anything else is one plain failure."""
        headers = {
            "Authorization": f"Bearer {self.key}",
            USER_ID_HEADER: user.id,
            USER_NAME_HEADER: user.name,
        }
        client = self._client or httpx.AsyncClient(
            timeout=httpx.Timeout(READ_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)
        )
        try:
            reply = await client.request(method, self.base_url + path, headers=headers)
            if reply.status_code == 200 and reply.headers.get(
                "content-type", ""
            ).startswith("application/pdf"):
                return 200, reply.content
            answer = reply.json()
        except (httpx.HTTPError, ValueError):
            return 502, dict(UNAVAILABLE_BODY)
        finally:
            if self._client is None:
                await client.aclose()
        if not isinstance(answer, dict):
            return 502, dict(UNAVAILABLE_BODY)
        return reply.status_code, answer
