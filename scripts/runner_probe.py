"""Probe of the measure-runner container: what a function could NOT do if it got past every guard.

Run it inside the container (nothing is installed, standard library only):

    make probe-runner

It checks the container's own limits, the real boundary. Each line is PASS or FAIL, and the exit code
is 1 if any check fails. It prints no secret: it lists variable names that look like one, never values.
"""

from __future__ import annotations

import os
import socket
import sys

SECRET_WORDS = ("KEY", "SECRET", "PASSWORD", "TOKEN", "DATABASE", "URL")
RESULTS: list[tuple[bool, str]] = []


def record(ok: bool, what: str, detail: str = "") -> None:
    RESULTS.append((ok, what))
    print(f"{'PASS' if ok else 'FAIL'}  {what}{': ' + detail if detail else ''}")


def refused(action, what: str) -> None:
    """PASS when the action raises OSError (the container says no)."""
    try:
        action()
    except OSError as error:
        record(True, what, type(error).__name__)
        return
    record(False, what, "it worked, and it should not have")


def write_a_file() -> None:
    with open("/probe-write", "w", encoding="utf-8") as handle:
        handle.write("x")


def reach_the_internet() -> None:
    socket.create_connection(("1.1.1.1", 53), 3).close()


def main() -> int:
    refused(write_a_file, "a file cannot be written in the root file system")
    refused(reach_the_internet, "no connection to the internet")
    record(os.getuid() != 0, "the process is not root", f"uid {os.getuid()}")
    try:
        interfaces = sorted(os.listdir("/sys/class/net"))
    except OSError:
        interfaces = []
    record(interfaces == ["lo"], "the only network interface is the loopback", ", ".join(interfaces))
    named = sorted(n for n in os.environ if any(w in n for w in SECRET_WORDS))
    allowed = [n for n in named if n.startswith("MEASURE_RUNNER_")]
    leaks = [n for n in named if n not in allowed]
    record(not leaks, "no key, password, token or address in the environment", ", ".join(leaks))
    path = os.environ.get("MEASURE_RUNNER_SOCKET", "")
    mode = os.stat(path).st_mode & 0o777 if path and os.path.exists(path) else None
    record(mode == 0o660, "the socket is owner and group read-write only",
           "missing" if mode is None else oct(mode))
    failed = sum(1 for ok, _ in RESULTS if not ok)
    print(f"{len(RESULTS)} checks, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
