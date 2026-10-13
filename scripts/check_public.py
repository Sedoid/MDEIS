#!/usr/bin/env python3
"""Check the public address from outside the server (`make prod-check HOST=demo.example.org`).

Run it from a machine that is not the server, so it sees what a stranger sees:

    python3 scripts/check_public.py demo.example.org

PASS or FAIL for each check, exit 1 if any fails:
  1. https://HOST/mdeis-about/ answers 200 over a valid certificate, with a strict-transport header,
     no server name, and the made-up-data notice on the page
  2. https://HOST/ answers (Open WebUI's sign-in page)
  3. http://HOST/ is sent on to https
  4. the ports of everything that must not be public are closed: the gateway, orchestrator,
     toolbox, lookup server, n8n, the database, the upload page and the embedder
Standard library only. It sends nothing but ordinary GET requests and connection attempts.
"""
import http.client
import socket
import ssl
import sys
import urllib.error
import urllib.request

# Ports of containers that must never be reachable from outside the server.
PRIVATE_PORTS = {
    3000: "Open WebUI directly",
    5432: "the database",
    5678: "n8n",
    8000: "the gateway",
    8080: "the upload page",
    8081: "the embedder",
    8100: "the toolbox (mdeis-mcp)",
    8200: "the lookup server",
    8300: "the orchestrator",
}
TIMEOUT_SECONDS = 5


def check(ok, text, why=""):
    print(("PASS  " if ok else "FAIL  ") + text + ("" if ok or not why else f"  ({why})"))
    return ok


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "mdeis-public-check"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), ""
    except (urllib.error.URLError, ssl.SSLError, OSError) as e:
        return 0, {}, type(e).__name__


def open_port(host, port):
    try:
        socket.create_connection((host, port), TIMEOUT_SECONDS).close()
        return True
    except OSError:
        return False


def main(argv):
    if len(argv) != 2:
        print("usage: check_public.py HOST", file=sys.stderr)
        return 2
    host = argv[1].removeprefix("https://").removeprefix("http://").strip("/")
    ok = True
    status, headers, body = fetch(f"https://{host}/mdeis-about/")
    lower = {k.lower(): v for k, v in headers.items()}
    ok &= check(status == 200, "the about page answers over https", f"status {status} {body[:40]}")
    ok &= check("strict-transport-security" in lower, "the strict-transport header is set")
    ok &= check("server" not in lower, "the server does not name itself")
    ok &= check("made-up data" in body, "the page says the data is made up")
    status, _, _ = fetch(f"https://{host}/")
    ok &= check(status in (200, 302, 307), "the application answers", f"status {status}")
    try:
        conn = http.client.HTTPConnection(host, timeout=TIMEOUT_SECONDS)
        conn.request("GET", "/")
        reply = conn.getresponse()
        where = reply.getheader("Location", "")
        ok &= check(
            reply.status in (301, 302, 307, 308) and where.startswith("https://"),
            "plain http is sent on to https",
            f"status {reply.status}",
        )
    except OSError as e:
        ok &= check(False, "plain http is sent on to https", type(e).__name__)
    for port, what in sorted(PRIVATE_PORTS.items()):
        ok &= check(not open_port(host, port), f"port {port} ({what}) is closed to the outside")
    print("\nPUBLIC CHECK OK" if ok else "\nPUBLIC CHECK FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
