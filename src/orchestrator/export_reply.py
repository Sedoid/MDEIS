"""Putting a PDF download link into a reply. The model never writes a link.

The model may call `export_report`, a tool that only checks that a report is released. After the
model has written its answer, this module adds ONE line under it, built by plain code from the
portal address setting (`PORTAL_URL`) and a report number the tool returned in this run, and only
when the tool's answer was `ok`. Nothing the model writes can become a link: any markdown link,
HTML anchor, bare web address, script or data address and portal path in its text is replaced by a
plain notice (the same idea as `chart_reply.strip_images`, which does it for pictures).

The line points at the portal's Files page with the report number in the address. The portal then
asks for the file as the signed-in person; the tool server makes it from the stored report. So the
model never sees the link, a path or a file name of its own making, and nothing here reads or makes
a file.

The conversation comes back to us every turn with the link in it. `strip_links` takes it out of the
assistant's earlier messages before the model sees the history.
"""

from __future__ import annotations

import re

TOOL = "export_report"
DEFAULT_PORTAL_URL = "/portal/"
FRAGMENT = "#/files?report="
# A report number, as the database gives it. Anything else is never put into a link.
REPORT_ID = re.compile(r"R-[0-9]{6,}")
# A safe address base: a path from the site root, or an http(s) address with a path. No query, no
# fragment, no user name, no space, no quote, no bracket, and never a "//" start (another site).
SAFE_BASE = re.compile(r"(?:https?://[A-Za-z0-9.-]+(?::[0-9]{1,5})?)?(?!//)/[A-Za-z0-9._~/-]*")
LINK_REMOVED = "[a link was removed]"

_VIEWS = "upload|files|lessons|methods|chat"
# Order matters: the longer forms go first, so a bare-address rule never splits one in half. An
# image (`![alt](address)`) is not touched here: `chart_reply.strip_images` takes it out first.
_LINK_FORMS = re.compile(
    r"(?<!!)\[[^\]]*\]\((?:[^()]|\([^()]*\))*\)"  # [label](address), nested brackets allowed
    r"|<a\b[^>]*>.*?</a>"  # an HTML anchor
    r"|<(?:https?|ftp|mailto):[^>\s]*>"  # <https://...>
    r"|\b(?:https?|ftp)://[^\s<>\"')\]]+"  # a bare web address
    r"|\bwww\.[^\s<>\"')\]]+"
    r"|\b(?:javascript|vbscript|data|file|blob|mailto):(?=\S)\S*"  # script, data and mail
    r"|(?<![\w/])/portal/\S*"  # the portal's own paths
    rf"|(?<![\w/])#/(?:{_VIEWS})\S*",  # the portal's page addresses
    re.IGNORECASE | re.DOTALL,
)


def portal_base(url) -> str:
    """The portal address the link starts from: the setting, made to end in a slash, or the default
    when the setting is empty or not a plain path or http(s) address."""
    text = url.strip() if isinstance(url, str) else ""
    if not SAFE_BASE.fullmatch(text):
        return DEFAULT_PORTAL_URL
    return text if text.endswith("/") else text + "/"


def portal_of(settings) -> str:
    """The portal address from the run's settings (a test double without one gets the default)."""
    return portal_base(getattr(settings, "portal_url", DEFAULT_PORTAL_URL))


def is_export(tool: str, result) -> bool:
    """Is this the `ok` answer of `export_report` for a well-formed report number?"""
    return (
        tool == TOOL
        and isinstance(result, dict)
        and result.get("status") == "ok"
        and isinstance(result.get("report_id"), str)
        and REPORT_ID.fullmatch(result["report_id"]) is not None
    )


def add_export(found: list, tool: str, result) -> list:
    """`found` plus the report number in an `ok` answer of the tool, each number once, in order."""
    if is_export(tool, result) and result["report_id"] not in found:
        return [*found, result["report_id"]]
    return found


def link_line(found: list, portal_url: str = DEFAULT_PORTAL_URL) -> str:
    """One line for each report number: "PDF of report R-000001: [Download](address)". A number
    that is not a well-formed report number is skipped, whatever it was handed in as."""
    base = portal_base(portal_url)
    lines = [
        f"PDF of report {number}: [Download]({base}{FRAGMENT}{number})"
        for number in found
        if isinstance(number, str) and REPORT_ID.fullmatch(number)
    ]
    return "\n\n".join(lines)


def strip_links(text):
    """The text with every link-like thing replaced by a plain notice. Not a string: unchanged."""
    return _LINK_FORMS.sub(LINK_REMOVED, text) if isinstance(text, str) else text
