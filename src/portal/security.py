"""The small safety pieces of the upload page: CSRF tokens, the same-origin check, file names."""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from collections import defaultdict, deque

CSRF_SECONDS = 3600
TRIES = 5  # wrong passwords allowed for one address in WINDOW seconds
GLOBAL_TRIES = 50  # and for everyone together
WINDOW = 60
NAME_LIMIT = 200
CONTROL = re.compile(r"[\x00-\x1f\x7f]")

HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def csp(frame_source: str | None) -> str:
    """A strict content policy: only this page's own files, and the chat in a frame if allowed."""
    frame = f"frame-src {frame_source}" if frame_source else "frame-src 'none'"
    return (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        f"connect-src 'self'; {frame}; base-uri 'none'; form-action 'none'; object-src 'none'"
    )


def csrf_token(secret: str, user_id: str, now: float | None = None) -> str:
    """A token bound to the user and to the hour. Only the portal can make one."""
    hour = int((time.time() if now is None else now) // CSRF_SECONDS)
    mac = hmac.new(secret.encode(), f"{user_id}|{hour}".encode(), hashlib.sha256).hexdigest()
    return f"{hour}.{mac}"


def csrf_ok(secret: str, user_id: str, token: str | None, now: float | None = None) -> bool:
    if not secret or not token or "." not in token:
        return False
    hour_text = token.split(".", 1)[0]
    if not hour_text.isdigit():
        return False
    current = int((time.time() if now is None else now) // CSRF_SECONDS)
    if current - int(hour_text) not in (0, 1):  # this hour or the last
        return False
    expected = csrf_token(secret, user_id, int(hour_text) * CSRF_SECONDS)
    return hmac.compare_digest(expected, token)


def same_origin(origin: str | None, scheme: str, host: str) -> bool:
    """A browser upload comes from the page itself: its Origin is this site's own address."""
    return bool(origin) and origin == f"{scheme}://{host}"


def clean_name(raw: str | None) -> str:
    """A file name for display only: no folders, no control characters, not too long."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = CONTROL.sub("", name).strip()
    return name[:NAME_LIMIT] or "unnamed"


class Throttle:
    """Counts wrong passwords, so a guessing script is slowed down. Kept in memory only."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._by_name: dict[str, deque] = defaultdict(deque)
        self._all: deque = deque()

    def _recent(self, line: deque) -> int:
        while line and self._clock() - line[0] > WINDOW:
            line.popleft()
        return len(line)

    def blocked(self, name: str) -> bool:
        key = name.strip().lower()
        return self._recent(self._by_name[key]) >= TRIES or self._recent(self._all) >= GLOBAL_TRIES

    def failed(self, name: str) -> None:
        now = self._clock()
        self._by_name[name.strip().lower()].append(now)
        self._all.append(now)
