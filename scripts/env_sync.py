#!/usr/bin/env python3
"""Add to .env every variable that .env.example has and .env lacks. Never changes or removes a line.

    make env-sync

Use it after pulling a change that adds variables. A [generate] variable gets a random value; the
others get the example's value. Prints variable names only, never values. Standard library only.
"""
import secrets
import sys

from envfile import PLACEHOLDER, ROOT, parse_env, parse_example


def main() -> int:
    target = ROOT / ".env"
    if not target.exists():
        print("FAIL: there is no .env yet. Run `make env-init`.")
        return 1
    have = parse_env()
    added, still_yours = [], []
    lines = []
    for name, tag, value in parse_example():
        if name in have:
            continue
        if tag == "generate" and value == PLACEHOLDER:
            value = secrets.token_hex(24)
        elif tag == "you" and value == PLACEHOLDER:
            still_yours.append(name)
        lines.append(f"{name}={value}")
        added.append(name)
    if not added:
        print("OK: .env already has every variable in .env.example.")
        return 0
    text = target.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    text += "\n# Added by make env-sync\n" + "\n".join(lines) + "\n"
    target.write_text(text, encoding="utf-8")
    target.chmod(0o600)
    print(f"Added {len(added)} variables to .env: {', '.join(added)}")
    if still_yours:
        print("You still need to set these by hand: " + ", ".join(still_yours))
    return 0


if __name__ == "__main__":
    sys.exit(main())
