#!/usr/bin/env python3
"""Scan tracked and untracked-but-not-ignored files for secret-shaped strings.

Uses the same patterns as .claude/hooks/block_secrets.py (kept in step by hand, because the hooks
folder is not shipped to the server). Prints the file and the kind of match, never the match
itself. Exit 1 on a hit. Standard library only.
"""
import re
import subprocess
import sys
from pathlib import Path

PATTERNS = [
    ("an OpenRouter key", re.compile(r"sk-or-v1-[A-Za-z0-9]{20,}")),
    ("an API key (sk- style)", re.compile(r"sk-[A-Za-z0-9]{32,}")),
    ("a Langfuse key", re.compile(r"\b(?:sk|pk)-lf-[A-Za-z0-9-]{12,}")),
    ("an AWS access key id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("a private key block", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
]
SECRET_NAMES = (".pem", ".key", ".p12", ".pfx")
ROOT = Path(__file__).resolve().parent.parent


def files() -> list[str]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard"],
                         capture_output=True, text=True, check=True).stdout
    return sorted(f for f in out.splitlines() if f and (ROOT / f).is_file())


def main() -> int:
    hits = []
    for f in files():
        name = f.rsplit("/", 1)[-1]
        if name == ".env" or (name.startswith(".env.") and name != ".env.example") or name.lower().endswith(SECRET_NAMES):
            hits.append(f"{f}: file name looks like a secrets file")
            continue
        try:
            text = (ROOT / f).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for label, pat in PATTERNS:
            if pat.search(text):
                hits.append(f"{f}: looks like {label}")
    if hits:
        print("FAIL: secret scan")
        for h in hits:
            print(f"  - {h}")
        return 1
    print("OK: no secret-shaped strings in tracked or untracked files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
