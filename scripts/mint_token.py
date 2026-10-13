#!/usr/bin/env python3
"""Make a short-lived approver token for the person you name, for testing the gate by hand.

Runs inside the mdeis-mcp container, which holds the signing secret (the orchestrator never does):

    make token ID=u-ada NAME="Ada Teacher"

The token lasts five minutes. It is what the gateway will sign for a signed-in user (Step 11); this
stands in for it in a test. Print it, pass it to `make ask TOKEN=...`, and do not keep it.
"""
import os
import sys

from mdeis.core import approver_token


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print('usage: mint_token.py ID "Display Name"', file=sys.stderr)
        return 2
    secret = os.environ.get("APPROVER_TOKEN_SECRET", "")
    try:
        print(approver_token.sign(secret, argv[1], argv[2]))
    except approver_token.TokenError as e:
        print(f"Could not make a token: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
