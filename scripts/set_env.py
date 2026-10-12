#!/usr/bin/env python3
"""Put values from the process environment into .env, by name. For CI. Standard library only.

    OPENROUTER_API_KEY=... python3 scripts/set_env.py OPENROUTER_API_KEY OPENROUTER_MODEL

Each NAME must already be a line in .env (so a typo is caught). The value is read from the
environment variable of the same name, so it never appears on a command line. Prints names only,
never a value. Exit 1 if a name is not in .env or its environment variable is empty.
"""
import os
import sys

from envfile import ROOT, VAR_RE


def main() -> int:
    names = sys.argv[1:]
    target = ROOT / ".env"
    if not names:
        print("usage: set_env.py NAME [NAME...]")
        return 2
    if not target.exists():
        print("FAIL: .env is missing. Run `make env-init` first.")
        return 1
    lines = target.read_text(encoding="utf-8").splitlines()
    done, problems = [], []
    for name in names:
        value = os.environ.get(name, "")
        if not value:
            problems.append(f"{name}: the environment variable is empty or not set")
            continue
        for i, line in enumerate(lines):
            m = VAR_RE.match(line)
            if m and m.group(1) == name:
                lines[i] = f"{name}={value}"
                done.append(name)
                break
        else:
            problems.append(f"{name}: not a line in .env")
    if problems:
        print("FAIL:")
        for p in problems:
            print(f"  - {p}")
        return 1
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Set in .env: " + ", ".join(done))
    return 0


if __name__ == "__main__":
    sys.exit(main())
