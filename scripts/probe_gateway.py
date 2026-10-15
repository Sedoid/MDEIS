#!/usr/bin/env python3
"""Prove the gateway's front door from inside its own container (`make probe-gateway`).

Four checks, each printed as PASS or FAIL, exit 1 on the first FAIL:
  1. no key                      -> 401
  2. the key but nobody signed in -> 401 (there is no anonymous approver)
  3. the key and a signed-in user -> 200 and an OpenAI-shaped answer (or a plain "not available")
  4. the model list names `mdeis-assistant`

The key is read from the container's environment and never printed. The signed-in user is made up
for the probe ("probe-user"). Standard library only.
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
TIMEOUT_SECONDS = 120


def call(path, headers, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def check(ok, text):
    print(("PASS  " if ok else "FAIL  ") + text)
    if not ok:
        sys.exit(1)


def main() -> int:
    key = os.environ.get("GATEWAY_API_KEY", "")
    chat = {"messages": [{"role": "user", "content": "What does rule TR-2 say?"}]}
    check(call("/v1/chat/completions", {}, chat)[0] == 401, "no key is refused (401)")
    keyed = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    check(call("/v1/chat/completions", keyed, chat)[0] == 401, "nobody signed in is refused (401)")
    user = {"X-OpenWebUI-User-Id": "probe-user", "X-OpenWebUI-User-Name": "Probe User"}
    signed_in = {**keyed, **user}
    status, reply = call("/v1/chat/completions", signed_in, chat)
    text = reply.get("choices", [{}])[0].get("message", {}).get("content", "")
    check(status == 200 and bool(text), "a signed-in user gets an answer in the chat")
    print("  answer begins: " + text[:120].replace("\n", " "))
    status, models = call("/v1/models", keyed)
    listed = models.get("data", [{}])[0].get("id")
    check(status == 200 and listed == "mdeis-assistant", "the model list names mdeis-assistant")
    print("\nGATEWAY OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
