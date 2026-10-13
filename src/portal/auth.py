"""Who is signed in. The portal does not keep accounts: it asks Open WebUI.

The browser already holds Open WebUI's login cookie. Cookies are not tied to a port, so it also
reaches the portal when the portal is on the same host name (127.0.0.1 and localhost are different
hosts: use the same one for both). The portal sends that token to Open WebUI's own session endpoint
and reads the user back: id, name and role. Nobody signed in, or a role that is not `admin` or
`user`, means no upload. A positive answer is remembered for a short time, keyed by a hash of the
token and never by the token itself.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

SESSION_PATH = "/api/v1/auths/"
CACHE_SECONDS = 30
TIMEOUT_SECONDS = 5
ROLES = ("admin", "user")


@dataclass(frozen=True)
class User:
    id: str
    name: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class AuthBackend(Protocol):
    async def user_for(self, token: str | None) -> User | None: ...


class OpenWebUIAuth:
    """Asks Open WebUI who owns a login token."""

    def __init__(
        self, base_url: str, client: httpx.AsyncClient | None = None, clock=time.monotonic
    ):
        self.base_url, self._client, self._clock = base_url.rstrip("/"), client, clock
        self._seen: dict[str, tuple[float, User]] = {}

    async def user_for(self, token: str | None) -> User | None:
        if not token:
            return None
        key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        cached = self._seen.get(key)
        if cached and self._clock() - cached[0] < CACHE_SECONDS:
            return cached[1]
        client = self._client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        try:
            reply = await client.get(
                self.base_url + SESSION_PATH, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError:
            return None
        finally:
            if self._client is None:
                await client.aclose()
        user = read_user(reply)
        if user:
            self._seen[key] = (self._clock(), user)
        return user


def read_user(reply: httpx.Response) -> User | None:
    if reply.status_code != httpx.codes.OK:
        return None
    try:
        data = reply.json()
        user = User(id=str(data["id"]), name=str(data.get("name") or data["id"]), role=data["role"])
    except (ValueError, KeyError, TypeError):
        return None
    return user if user.role in ROLES else None
