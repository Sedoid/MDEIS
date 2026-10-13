"""The portal's settings, read from the environment when the service starts.

Everything here is configuration: where the inbox is, how big a file may be, where Open WebUI is.
There is no model key and no approver-token secret in this service, and none is read.
"""

import os
from dataclasses import dataclass

DEFAULT_MAX_MB = 10
DEFAULT_MAX_MARKDOWN_MB = 1
MB = 1024 * 1024
ON = ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    database_url: str
    inbox: str
    webui_url: str  # where the portal asks Open WebUI who is signed in (inside the network)
    webui_public_url: str  # where the browser opens Open WebUI
    session_secret: str  # signs the CSRF tokens
    cookie_name: str  # the Open WebUI login cookie
    max_bytes: int
    max_markdown_bytes: int
    frame_chat: bool
    portal_public_url: str = ""  # the address people type, if a proxy sits in front


def load_settings(env=None) -> Settings:
    env = os.environ if env is None else env
    return Settings(
        database_url=env.get("PORTAL_DATABASE_URL", ""),
        inbox=env.get("INBOX_DIR", "/inbox"),
        webui_url=(env.get("WEBUI_URL") or "http://webui:8080").rstrip("/"),
        webui_public_url=(env.get("WEBUI_PUBLIC_URL") or "http://127.0.0.1:3000").rstrip("/"),
        session_secret=env.get("PORTAL_SESSION_SECRET", ""),
        cookie_name=env.get("WEBUI_COOKIE_NAME") or "token",
        max_bytes=int(env.get("MAX_UPLOAD_MB") or DEFAULT_MAX_MB) * MB,
        max_markdown_bytes=int(env.get("MAX_MARKDOWN_MB") or DEFAULT_MAX_MARKDOWN_MB) * MB,
        frame_chat=env.get("PORTAL_FRAME_CHAT", "false").strip().lower() in ON,
        portal_public_url=(env.get("PORTAL_PUBLIC_URL") or "").rstrip("/"),
    )
