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
        print("FAIL: there is no .env file yet.")
        print("      Run `make env-init` (it fills the random secrets for you), or run")
        print("      `cp .env.example .env` and fill in the values the README table lists.")
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
        print("FAIL: these names are not in .env at all. Run `make env-sync` to add them,")
        print("      or copy the lines from .env.example:")
        for n in missing:
            print(f"  - {n}")
    if unset:
        problems = True
        print("FAIL: still empty or 'change-me'. Set each one in .env. For a secret, a long random")
        print("      string works: `openssl rand -hex 32`. (Or delete .env and run `make env-init`.)")
        for n in unset:
            print(f"  - {n}")
    if yours:
        if strict:
            print("FAIL: you must set these yourself (empty or 'change-me'). They are your own keys and")
            print("      settings, and the agent cannot call the model without them:")
        else:
            print("NOTE: you supply these, needed before the agent can call the model:")
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
