"""The short-lived pass that names the signed-in person (docs/SPEC.md section 3).

The gateway signs it with a secret that only the gateway and the toolbox hold. The toolbox checks
the signature and the expiry, and reads the approver from it. It is never a tool argument, and the
AI writer never sees it. Standard library only.

    token = sign(secret, "u-12", "Ada Teacher")        # the gateway
    who = verify(secret, token)                         # the toolbox: Identity, or TokenError

A token is `<payload>.<signature>`: the payload is JSON (user id, display name, expiry, a one-time
number) in URL-safe base64, and the signature is HMAC-SHA256 of that text.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import NamedTuple

TOKEN_LIFETIME_SECONDS = 300
B64_BLOCK = 4  # base64 text is padded to a multiple of this


class TokenError(ValueError):
    """The token is missing, malformed, tampered with, signed with another secret, or expired."""


class Identity(NamedTuple):
    id: str
    name: str


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % B64_BLOCK))


def _signature(secret: str, payload: str) -> str:
    return _b64(hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest())


def sign(secret, user_id, name, *, now=None, lifetime=TOKEN_LIFETIME_SECONDS, nonce=None) -> str:
    if not secret:
        raise TokenError("no signing secret")
    if not user_id or not name:
        raise TokenError("a token needs a user id and a name")
    issued = time.time() if now is None else now
    body = {
        "uid": user_id,
        "name": name,
        "exp": int(issued) + int(lifetime),
        "nonce": nonce or secrets.token_urlsafe(),
    }
    payload = _b64(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return payload + "." + _signature(secret, payload)


def verify(secret, token, *, now=None) -> Identity:
    if not secret:
        raise TokenError("no signing secret")
    if not token or token.count(".") != 1:
        raise TokenError("malformed token")
    payload, given = token.split(".")
    if not hmac.compare_digest(given, _signature(secret, payload)):
        raise TokenError("bad signature")
    try:
        body = json.loads(_unb64(payload))
        uid, name, expires = body["uid"], body["name"], body["exp"]
    except (ValueError, KeyError, TypeError) as e:
        raise TokenError("the token is missing a field") from e
    if not isinstance(uid, str) or not isinstance(name, str) or not uid or not name:
        raise TokenError("the token is missing a field")
    if (time.time() if now is None else now) >= expires:
        raise TokenError("expired")
    return Identity(uid, name)
