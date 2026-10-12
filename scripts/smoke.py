#!/usr/bin/env python3
"""Smoke test: call a tool end to end for one fixture learner. Standard library only.

Runs inside the network, through a container that has python (`make smoke` does this):
    docker compose exec -T mdeis-mcp python - < scripts/smoke.py
    python3 scripts/smoke.py http://127.0.0.1:8100/mcp http://127.0.0.1:8000/healthz   (by hand)

Steps, each printed as PASS or FAIL:
  1. the gateway answers its health check
  2. the MCP server answers its health check
  3. the MCP server lists its tools
  4. the learner tool exists and is not the stub
  5. the learner tool returns a result for the fixture learner, with no error
  6. the same call a second time returns the same result (numbers come from code, so they repeat)

Exit 1 on the first failure, with a plain sentence. It prints no secret and does not need one.

UNCONFIRMED: LEARNER_TOOL and LEARNER_ARGS below are my best reading of the planned tool and the
README example learner. They have not been checked against the real tool, because the real tools
are not built yet. Confirm both against the tool contract when the first learner tool lands.
"""
import json
import sys
import urllib.error
import urllib.request

LEARNER_TOOL = "compute_trend"
LEARNER_ARGS = {"learner_id": "L-0007"}
STUB_TOOLS = {"ping"}  # placeholder tools that do not count

MCP_URL = sys.argv[1] if len(sys.argv) > 1 else "http://mdeis-mcp:8100/mcp"
GATEWAY_HEALTH = sys.argv[2] if len(sys.argv) > 2 else "http://gateway:8000/healthz"
MCP_HEALTH = MCP_URL.rsplit("/", 1)[0] + "/healthz"


class Fail(Exception):
    pass


def step(ok: bool, text: str, why: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + text)
    if not ok:
        raise Fail(why or text)


def get(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError):
        return 0


def parse_body(raw: str, content_type: str, want_id: int) -> dict:
    """A reply is plain JSON or an event stream. Return the message with the wanted id."""
    if "event-stream" in content_type:
        for line in raw.splitlines():
            if line.startswith("data:"):
                msg = json.loads(line[5:].strip())
                if msg.get("id") == want_id:
                    return msg
        raise Fail("the MCP server sent no reply to the request")
    return json.loads(raw)


class Mcp:
    """Just enough of Streamable HTTP MCP: initialize, list tools, call a tool."""

    def __init__(self, url: str):
        self.url = url
        self.session = None
        self.next_id = 0

    def post(self, payload: dict, want_id: int | None = None):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                self.session = resp.headers.get("Mcp-Session-Id") or self.session
                raw = resp.read().decode()
                ctype = resp.headers.get("Content-Type", "")
        except (urllib.error.URLError, OSError) as e:
            raise Fail(f"could not reach the MCP server at {self.url} ({type(e).__name__})")
        return None if want_id is None else parse_body(raw, ctype, want_id)

    def call(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        msg = {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}}
        return self.post(msg, self.next_id)

    def start(self) -> None:
        self.call("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                 "clientInfo": {"name": "mdeis-smoke", "version": "0"}})
        self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})


def main() -> int:
    try:
        step(get(GATEWAY_HEALTH) == 200, "gateway health check", f"the gateway did not answer at {GATEWAY_HEALTH}")
        step(get(MCP_HEALTH) == 200, "mdeis-mcp health check", f"mdeis-mcp did not answer at {MCP_HEALTH}")

        mcp = Mcp(MCP_URL)
        mcp.start()
        reply = mcp.call("tools/list")
        names = [t["name"] for t in reply.get("result", {}).get("tools", [])]
        step(bool(names), f"mdeis-mcp lists its tools ({len(names)})", "mdeis-mcp offers no tools")

        real = [n for n in names if n not in STUB_TOOLS]
        step(LEARNER_TOOL in names,
             f"learner tool '{LEARNER_TOOL}' is offered",
             f"the tool '{LEARNER_TOOL}' is not offered. The server offers: {', '.join(names)}. "
             + ("Only a placeholder tool is built so far, so the smoke test cannot check a learner yet."
                if not real else "Check LEARNER_TOOL in scripts/smoke.py."))

        results = []
        for _ in range(2):
            out = mcp.call("tools/call", {"name": LEARNER_TOOL, "arguments": LEARNER_ARGS})
            if "error" in out:
                raise Fail(f"the call to '{LEARNER_TOOL}' returned an error: {out['error'].get('message', '')}")
            result = out.get("result", {})
            if result.get("isError"):
                raise Fail(f"the call to '{LEARNER_TOOL}' for {LEARNER_ARGS} reported an error")
            results.append(json.dumps(result, sort_keys=True))
            if len(results) == 1:
                step(bool(result.get("content") or result.get("structuredContent")),
                     f"'{LEARNER_TOOL}' returned a result for the fixture learner",
                     "the tool returned an empty result")
        step(results[0] == results[1], "a second identical call returns the same result",
             "two identical calls returned different results")
    except Fail as e:
        print(f"\nSMOKE FAILED: {e}")
        return 1
    except (KeyError, ValueError) as e:
        print(f"\nSMOKE FAILED: an unexpected reply from the server ({type(e).__name__})")
        return 1
    print("\nSMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
