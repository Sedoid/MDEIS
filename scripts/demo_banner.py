#!/usr/bin/env python3
"""Print the addresses and what to try first, at the end of `make demo`. Standard library only.

Reads port names from .env and prints addresses. It never prints a password or key: it tells you
which variable holds the sign-in. The upload page is printed only if the portal container exists
(PORTAL_PORT is in .env); until it is built, the banner says so instead of inventing an address.
"""
import sys

from envfile import ROOT, parse_env


def main() -> int:
    if not (ROOT / ".env").exists():
        print("FAIL: .env is missing")
        return 1
    env = parse_env()
    host = (env.get("HOST_BIND") or "127.0.0.1").strip()
    chat = f"http://{host}:{(env.get('WEBUI_PORT') or '3000').strip()}"
    portal_port = (env.get("PORTAL_PORT") or "").strip()

    print()
    print("Lucent AI is up and healthy.")
    print()
    print(f"  Chat:         {chat}")
    if portal_port:
        print(f"  Upload page:  http://{host}:{portal_port}")
    else:
        print("  Upload page:  not available in this build (the portal container is not built yet)")
    print()
    print("  Sign in with WEBUI_ADMIN_EMAIL and WEBUI_ADMIN_PASSWORD from your .env.")
    print("  Try first:    Show me the longitudinal picture for L-0007.")
    print("                (the synthetic learner from the README walkthrough)")
    print()
    print("  Stop it:      make dev-down      (keeps the data)")
    print("  Start over:   make clean         (deletes the data volumes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
