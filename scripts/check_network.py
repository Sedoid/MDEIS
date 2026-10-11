#!/usr/bin/env python3
"""Check that the running containers are wired as docs/CONTAINERS.md says. Run by `make dev-health`.

Prints names and PASS / FAIL / SKIP only, never a secret value. Exit 1 if anything fails.
Standard library plus the `docker` command.
"""
import json
import subprocess
import sys

from envfile import ROOT, parse_env

# service -> expected to keep running (True) or to run once and exit 0 (False)
SERVICES = {"postgres": True, "embedder": True, "migrate": False, "mdeis-mcp": True,
            "pg-mcp-ro": True, "n8n": True, "gateway": True, "webui": True}
HOLDS_OPENROUTER = {"n8n"}
HOLDS_TOKEN_SECRET = {"gateway", "mdeis-mcp"}
NON_ROOT = ["mdeis-mcp", "gateway"]

results: list[tuple[str, str, str]] = []


def record(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))
    print(f"{status:5} {name}" + (f"  ({detail})" if detail else ""))


def run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)
    return p.returncode, (p.stdout + p.stderr).strip()


def main() -> int:
    if not (ROOT / ".env").exists():
        print("FAIL: .env is missing")
        return 1
    env = parse_env()
    suffix = env.get("STACK_SUFFIX", "mdeis")
    project = env.get("COMPOSE_PROJECT_NAME", "mdeis")
    cname = {s: f"{s}-{suffix}" for s in SERVICES}

    code, _ = run(["docker", "version", "--format", "{{.Server.Version}}"])
    if code != 0:
        print("FAIL: Docker is not available or not running")
        return 1

    # 1. every service is up and healthy (migrate must have exited 0)
    for svc, long_running in SERVICES.items():
        code, out = run(["docker", "inspect", "-f",
                         "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.State.ExitCode}}",
                         cname[svc]])
        if code != 0:
            record("FAIL", f"{svc}: container exists", "not found")
            continue
        status, health, exit_code = out.split("|")
        if long_running:
            ok = status == "running" and health in ("healthy", "")
            record("PASS" if ok else "FAIL", f"{svc}: running and healthy", f"{status}/{health or 'no healthcheck'}")
        else:
            ok = status == "exited" and exit_code == "0"
            record("PASS" if ok else "FAIL", f"{svc}: ran once and exited 0", f"{status}, exit {exit_code}")

    # 2. published ports: only webui, and the n8n editor in dev, both on 127.0.0.1
    allowed = {("webui", env.get("WEBUI_PORT", "3000")), ("n8n", env.get("N8N_EDITOR_PORT", "5678"))}
    bad = []
    for svc in SERVICES:
        code, out = run(["docker", "inspect", "-f", "{{json .HostConfig.PortBindings}}", cname[svc]])
        if code != 0:
            continue
        for _, binds in (json.loads(out) or {}).items():
            for b in binds or []:
                if (svc, b["HostPort"]) not in allowed:
                    bad.append(f"{svc}:{b['HostPort']}")
                if b["HostIp"] != "127.0.0.1":
                    bad.append(f"{svc}:{b['HostPort']} bound to {b['HostIp'] or 'all addresses'}")
    record("PASS" if not bad else "FAIL", "published ports are only webui and the n8n editor, on 127.0.0.1",
           ", ".join(bad))

    # 3. the data network is internal
    code, out = run(["docker", "network", "inspect", "-f", "{{.Internal}}", f"{project}_data"])
    record("PASS" if out == "true" else "FAIL", "data network is internal", out if code else "")

    # 4. secrets sit only where CONTAINERS.md section 8 says (names only, no values)
    leaks = []
    for svc in SERVICES:
        code, out = run(["docker", "inspect", "-f", "{{json .Config.Env}}", cname[svc]])
        if code != 0:
            continue
        names = {e.split("=", 1)[0] for e in json.loads(out)}
        if any(n.startswith("OPENROUTER") for n in names) and svc not in HOLDS_OPENROUTER:
            leaks.append(f"{svc} has an OPENROUTER variable")
        if "APPROVER_TOKEN_SECRET" in names and svc not in HOLDS_TOKEN_SECRET:
            leaks.append(f"{svc} has APPROVER_TOKEN_SECRET")
        if "APPROVER_TOKEN_SECRET" not in names and svc in HOLDS_TOKEN_SECRET:
            leaks.append(f"{svc} is missing APPROVER_TOKEN_SECRET")
    record("PASS" if not leaks else "FAIL", "secrets are held only by the right containers", "; ".join(leaks))

    # 5. the containers we build do not run as root
    for svc in NON_ROOT:
        code, out = run(["docker", "exec", cname[svc], "id", "-u"])
        record("PASS" if code == 0 and out != "0" else "FAIL", f"{svc}: runs as non-root", f"uid {out}" if code == 0 else out)

    # 6. database roles: n8n's role cannot open mdeis, the read-only role cannot write
    def psql(user_key: str, pw_key: str, db: str, sql: str) -> int:
        code, _ = run(["docker", "exec", "-e", f"PGPASSWORD={env.get(pw_key, '')}", cname["postgres"],
                       "psql", "-h", "postgres", "-U", env.get(user_key, ""), "-d", db, "-v", "ON_ERROR_STOP=1",
                       "-c", sql])
        return code
    record("PASS" if psql("N8N_DB_USER", "N8N_DB_PASSWORD", env.get("MDEIS_DB_NAME", "mdeis"), "select 1") != 0 else "FAIL",
           "n8n role cannot connect to the mdeis database")
    record("PASS" if psql("MDEIS_RO_USER", "MDEIS_RO_PASSWORD", env.get("MDEIS_DB_NAME", "mdeis"),
                          "create table probe_should_fail (i int)") != 0 else "FAIL",
           "read-only role cannot create a table")
    record("PASS" if psql("MDEIS_RO_USER", "MDEIS_RO_PASSWORD", env.get("MDEIS_DB_NAME", "mdeis"), "select 1") == 0 else "FAIL",
           "read-only role can read")

    # 7. each hop in the chain can reach the next
    py = "import sys,urllib.request as u; u.urlopen(sys.argv[1], timeout=5)"
    sock = "import socket,sys; socket.create_connection((sys.argv[1], int(sys.argv[2])), 5).close()"
    hops = [
        ("webui -> gateway", "webui", ["curl", "-fsS", "-o", "/dev/null", "http://gateway:8000/healthz"]),
        ("gateway -> n8n", "gateway", ["python", "-c", py, "http://n8n:5678/healthz"]),
        ("gateway -> mdeis-mcp", "gateway", ["python", "-c", py, "http://mdeis-mcp:8100/healthz"]),
        ("n8n -> mdeis-mcp", "n8n", ["wget", "-q", "-O", "/dev/null", "-T", "5", "http://mdeis-mcp:8100/healthz"]),
        ("n8n -> pg-mcp-ro", "n8n", ["nc", "-z", "-w", "5", "pg-mcp-ro", "8200"]),
        ("mdeis-mcp -> postgres", "mdeis-mcp", ["python", "-c", sock, "postgres", "5432"]),
        ("mdeis-mcp -> embedder", "mdeis-mcp", ["python", "-c", py, "http://embedder:8081/health"]),
    ]
    for label, svc, cmd in hops:
        code, out = run(["docker", "exec", cname[svc]] + cmd)
        if code == 0:
            record("PASS", label)
        elif "not found" in out or "executable file" in out:
            record("SKIP", label, "the probe tool is not in that container")
        else:
            record("FAIL", label)

    failed = [r for r in results if r[0] == "FAIL"]
    print()
    print(f"{len(results) - len(failed)} not failed, {len(failed)} failed, "
          f"{sum(1 for r in results if r[0] == 'SKIP')} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
