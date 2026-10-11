#!/usr/bin/env python3
"""Check docs/STRUCTURE.md against the repo.

1. Every file that git tracks (or would track: not ignored) is covered by a row.
   A row covers a file if the path equals it, or ends with '/' and is a prefix of it.
2. Every row marked `exists` exists on disk. Rows marked `local` (kept off git) and `planned`
   are not checked.

Exit 0 if both hold, 1 otherwise. Standard library only. Run from anywhere inside the repo.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache", ".ruff_cache"}


def repo_root() -> Path:
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, check=True).stdout.strip()
        return Path(out)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return Path(__file__).resolve().parent.parent


def list_files(root: Path) -> list[str]:
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "--cached", "--others",
                              "--exclude-standard"], capture_output=True, text=True, check=True)
        files = [line for line in out.stdout.splitlines() if line]
        return sorted(f for f in files if (root / f).exists())
    except (subprocess.CalledProcessError, FileNotFoundError):
        found = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                found.append(Path(dirpath, name).relative_to(root).as_posix())
        return sorted(found)


def parse_rows(text: str) -> list[tuple[str, str]]:
    rows = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        m = re.fullmatch(r"`([^`]+)`", cells[0])
        if not m:
            continue  # header or separator
        rows.append((m.group(1), cells[-1].strip().lower()))
    return rows


def covered(path: str, prefixes: list[str]) -> bool:
    return any(path == p or (p.endswith("/") and path.startswith(p)) for p in prefixes)


def main() -> int:
    root = repo_root()
    table = root / "docs" / "STRUCTURE.md"
    if not table.exists():
        print("FAIL: docs/STRUCTURE.md not found")
        return 1
    rows = parse_rows(table.read_text(encoding="utf-8"))
    if not rows:
        print("FAIL: no rows found in docs/STRUCTURE.md")
        return 1

    prefixes = [p for p, _ in rows]
    problems = []

    for f in list_files(root):
        if not covered(f, prefixes):
            problems.append(f"not in STRUCTURE.md: {f}")

    for path, status in rows:
        if status == "exists" and not (root / path).exists():
            problems.append(f"marked exists but missing: {path}")

    if problems:
        print("FAIL: structure check")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"OK: {len(rows)} rows, all files covered, all 'exists' rows present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
