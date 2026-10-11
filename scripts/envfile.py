"""Shared helpers for the scripts that read .env and .env.example. Standard library only.

.env.example format: a comment line starting with a tag, then the variable.
    # [generate] What this is and who supplies it.
    NAME=change-me
Tags: [you] [generate] [default] [optional].
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER = "change-me"
TAG_RE = re.compile(r"^#\s*\[(you|generate|default|optional)\]")
VAR_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def parse_example(path: Path | None = None) -> list[tuple[str, str, str]]:
    """Return (name, tag, placeholder_value) for each variable in .env.example, in order."""
    path = path or ROOT / ".env.example"
    out = []
    tag = "default"
    for line in path.read_text(encoding="utf-8").splitlines():
        m = TAG_RE.match(line)
        if m:
            tag = m.group(1)
            continue
        m = VAR_RE.match(line)
        if m:
            out.append((m.group(1), tag, m.group(2).strip()))
            tag = "default"
    return out


def parse_env(path: Path | None = None) -> dict[str, str]:
    """Read KEY=VALUE pairs from .env. Quotes around a value are removed."""
    path = path or ROOT / ".env"
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = VAR_RE.match(line)
        if m:
            v = m.group(2).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            values[m.group(1)] = v
    return values
