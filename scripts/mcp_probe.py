#!/usr/bin/env python3
"""List the tools an MCP server offers. Standard library only.

Run from inside the network, through a container that has python (`make probe-mcp` does this):
    docker compose exec -T mdeis-mcp python - http://mdeis-mcp:8100/mcp < scripts/mcp_probe.py
    docker compose exec -T mdeis-mcp python - http://pg-mcp-ro:8200/sse sse < scripts/mcp_probe.py

Mode "http" (default) is Streamable HTTP. Mode "sse" is the older SSE transport, which the
borrowed server uses. Prints the tool names. Exit 1 on any failure.
"""
import http.client
import json
import sys
import urllib.parse
import urllib.request

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                   "clientInfo": {"name": "mdeis-probe", "version": "0"}}}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


def parse_body(raw: str, content_type: str, want_id: int):
    """A reply is plain JSON or an event stream. Return the message with the wanted id."""
    if "event-stream" in content_type:
        for line in raw.splitlines():
            if line.startswith("data:"):
                msg = json.loads(line[5:].strip())
                if msg.get("id") == want_id:
                    return msg
        raise RuntimeError("no reply with the expected id in the event stream")
    return json.loads(raw)


def probe_http(url: str) -> list[str]:
    session = None

    def post(payload, want_id=None):
        nonlocal session
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if session:
            headers["Mcp-Session-Id"] = session
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            session = resp.headers.get("Mcp-Session-Id") or session
            raw = resp.read().decode()
            if want_id is None:
                return None
            return parse_body(raw, resp.headers.get("Content-Type", ""), want_id)

    post(INIT, 1)
    post(INITIALIZED)
    reply = post(LIST, 2)
    return [t["name"] for t in reply["result"]["tools"]]


def probe_sse(url: str) -> list[str]:
    u = urllib.parse.urlparse(url)
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=15)
    conn.request("GET", u.path, headers={"Accept": "text/event-stream"})
    stream = conn.getresponse()
    if stream.status != 200:
        raise RuntimeError(f"SSE connect returned {stream.status}")

    def next_event():
        event, data = "message", []
        while True:
            line = stream.readline().decode().rstrip("\r\n")
            if line == "":
                if data:
                    return event, "\n".join(data)
                continue
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())

    event, endpoint = next_event()
    if event != "endpoint":
        raise RuntimeError(f"expected an endpoint event, got {event}")
    post_url = urllib.parse.urljoin(url, endpoint)

    def send(payload):
        req = urllib.request.Request(post_url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15).read()

    def reply_for(want_id):
        while True:
            _, data = next_event()
            msg = json.loads(data)
            if msg.get("id") == want_id:
                return msg

    send(INIT)
    reply_for(1)
    send(INITIALIZED)
    send(LIST)
    return [t["name"] for t in reply_for(2)["result"]["tools"]]


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: mcp_probe.py <url> [http|sse]")
        return 2
    url = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "http"
    try:
        names = probe_sse(url) if mode == "sse" else probe_http(url)
    except Exception as e:  # a probe reports every failure the same way
        print(f"FAIL: {url}: {type(e).__name__}: {e}")
        return 1
    print(f"OK: {url} offers {len(names)} tools")
    for n in names:
        print(f"  - {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
