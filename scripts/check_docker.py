#!/usr/bin/env python3
"""Check that Docker is ready and the ports we publish are free. Run by `make dev-up`, `make demo`
and `make clean`. Plain words on failure, and the .env variable that changes a port.

    python3 scripts/check_docker.py             Docker, Compose, the chat port and the upload page port
    python3 scripts/check_docker.py --editor    also the n8n editor port (dev profile)
    python3 scripts/check_docker.py --no-ports  Docker and Compose only (used by `make clean`)

A port that one of our own containers already publishes is fine: that is the stack from an earlier
`make demo`. Prints names and port numbers, never a secret. Exit 1 on any problem.
Standard library only.
"""
import re
import shutil
import socket
import subprocess
import sys

from envfile import ROOT, parse_env

# (label, .env variable that sets the port, default used by the compose file)
CHAT = ("the chat (Open WebUI)", "WEBUI_PORT", "3000")
EDITOR = ("the n8n editor", "N8N_EDITOR_PORT", "5678")
PORTAL = ("the upload page (portal)", "PORTAL_PORT", "8080")


def run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)
    return p.returncode, (p.stdout + p.stderr).strip()


def fail(*lines: str) -> int:
    print("FAIL: " + lines[0])
    for line in lines[1:]:
        print("      " + line)
    return 1


def check_docker() -> int:
    if shutil.which("docker") is None:
        return fail("Docker is not installed (the `docker` command was not found).",
                    "Install Docker Desktop (Mac, Windows) or Docker Engine (Linux), start it,",
                    "then run this command again.")
    code, out = run(["docker", "info", "--format", "{{.ServerVersion}}"])
    if code != 0:
        low = out.lower()
        if "permission denied" in low:
            return fail("Docker is installed, but your user is not allowed to use it.",
                        "On Linux, add yourself to the docker group (`sudo usermod -aG docker $USER`),",
                        "log out and in again, then run this command again.")
        return fail("Docker is installed, but it is not running.",
                    "Start Docker Desktop (Mac, Windows) or run `sudo systemctl start docker` (Linux),",
                    "wait until it says it is running, then run this command again.")
    code, _ = run(["docker", "compose", "version"])
    if code != 0:
        return fail("Docker is running, but Docker Compose v2 is missing (`docker compose` did not work).",
                    "Update Docker Desktop, or install the Compose plugin for Docker Engine,",
                    "then run this command again.")
    return 0


def ports_published_by_us(suffix: str) -> set[str]:
    """Host ports already published by containers of this stack (names end in -<suffix>)."""
    code, out = run(["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"])
    mine: set[str] = set()
    if code != 0:
        return mine
    for line in out.splitlines():
        name, _, ports = line.partition("\t")
        if name.endswith(f"-{suffix}"):
            mine.update(re.findall(r":(\d+)->", ports))
    return mine


def port_in_use(host: str, port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(1)
        if s.connect_ex((host, port)) == 0:  # something is listening
            return True
    finally:
        s.close()
    b = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        b.bind((host, port))
    except OSError:
        return True
    finally:
        b.close()
    return False


def check_ports(wanted: list[tuple[str, str, str]]) -> int:
    env = parse_env() if (ROOT / ".env").exists() else {}
    host = env.get("HOST_BIND", "127.0.0.1").strip() or "127.0.0.1"
    suffix = env.get("STACK_SUFFIX", "mdeis").strip() or "mdeis"
    ours = ports_published_by_us(suffix)
    problems = 0
    for label, var, default in wanted:
        raw = (env.get(var) or default).strip()
        if not raw.isdigit() or not (1 <= int(raw) <= 65535):
            problems += fail(f"{var} in .env is not a port number.",
                             f"Set {var} to a number between 1 and 65535 (it is the port for {label}).")
            continue
        if raw in ours:
            continue  # our own containers hold it
        if port_in_use(host, int(raw)):
            problems += fail(f"Port {raw} on {host} is already in use by another program.",
                             f"That port is for {label}. Stop the other program, or set {var} in .env",
                             "to a free port (for example 3010) and run this command again.")
    return 1 if problems else 0


def main() -> int:
    args = sys.argv[1:]
    code = check_docker()
    if code or "--no-ports" in args:
        return code
    wanted = [CHAT, PORTAL] + ([EDITOR] if "--editor" in args else [])
    return check_ports(wanted)


if __name__ == "__main__":
    sys.exit(main())
