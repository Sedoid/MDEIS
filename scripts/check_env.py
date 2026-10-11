#!/usr/bin/env python3
"""Check .env against .env.example. Prints variable names only, never values.

    python3 scripts/check_env.py            stack can start: every [generate] and [default] variable set
    python3 scripts/check_env.py --strict   also every [you] variable set (needed before the agent runs)

Exit 0 when fine, 1 when something is missing. Standard library only.
"""
import sys

from envfile import PLACEHOLDER, ROOT, parse_env, parse_example


def main() -> int:
    strict = "--strict" in sys.argv[1:]
    if not (ROOT / ".env").exists():
        print("FAIL: .env is missing. Run `make env-init`.")
        return 1
    example = parse_example()
    env = parse_env()

    missing, unset, yours = [], [], []
    for name, tag, _ in example:
        if tag == "optional":
            continue
        if name not in env:
            missing.append(name)
            continue
        value = env[name].strip()
        if value == "" or value == PLACEHOLDER:
            (yours if tag == "you" else unset).append(name)

    problems = False
    if missing:
        problems = True
        print("FAIL: not in .env at all (compare with .env.example):")
        for n in missing:
            print(f"  - {n}")
    if unset:
        problems = True
        print("FAIL: still empty or 'change-me' (run `make env-init` for a new .env, or set them):")
        for n in unset:
            print(f"  - {n}")
    if yours:
        label = "FAIL" if strict else "NOTE"
        print(f"{label}: you supply these, needed before the agent can call the model:")
        for n in yours:
            print(f"  - {n}")
        if strict:
            problems = True
    if problems:
        return 1
    print("OK: .env is complete" + (" (strict)" if strict else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
