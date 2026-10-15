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
SIGNIN_PATH = "/api/v1/auths/signin"
SIGNOUT_PATH = "/api/v1/auths/signout"
REFUSED = (400, 401, 403)  # Open WebUI saying "not these credentials"
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


class Unavailable(Exception):
    """Open WebUI could not be asked, or answered something unexpected."""


class AuthBackend(Protocol):
    async def user_for(self, token: str | None) -> User | None: ...
    async def sign_in(self, email: str, password: str) -> str | None: ...
    async def sign_out(self, token: str) -> None: ...


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

    async def sign_in(self, email: str, password: str) -> str | None:
        """Open WebUI checks the password. Returns its login token, or None if it said no."""
        client = self._client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        try:
            reply = await client.post(
                self.base_url + SIGNIN_PATH, json={"email": email, "password": password}
            )
        except httpx.HTTPError:
            raise Unavailable from None
        finally:
            if self._client is None:
                await client.aclose()
        if reply.status_code in REFUSED:
            return None
        try:
            token = reply.json()["token"]
        except (ValueError, KeyError, TypeError):
            raise Unavailable from None
        if reply.status_code != httpx.codes.OK or not isinstance(token, str) or not token:
            raise Unavailable
        return token

    async def sign_out(self, token: str) -> None:
        """Ends the session at Open WebUI as well. Best effort: the cookie is cleared anyway."""
        self._seen.pop(hashlib.sha256(token.encode("utf-8")).hexdigest(), None)
        client = self._client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        try:
            await client.get(
                self.base_url + SIGNOUT_PATH, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError:
            pass
        finally:
            if self._client is None:
                await client.aclose()


def read_user(reply: httpx.Response) -> User | None:
    if reply.status_code != httpx.codes.OK:
        return None
    try:
        data = reply.json()
        user = User(id=str(data["id"]), name=str(data.get("name") or data["id"]), role=data["role"])
    except (ValueError, KeyError, TypeError):
        return None
    return user if user.role in ROLES else None
