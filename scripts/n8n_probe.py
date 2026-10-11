#!/usr/bin/env python3
"""POST a small message to the n8n echo webhook from inside the network. Standard library only.

    docker compose exec -T mdeis-mcp python - < scripts/n8n_probe.py   (`make probe-n8n` does this)
Works only while the echo workflow (deploy/n8n/echo/) is the one imported, which is the case
until the real workflow lands in workflows/ (T-011). Exit 1 on any failure.
"""
import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("N8N_PROBE_URL", "http://n8n:5678/webhook/echo")


def main() -> int:
    req = urllib.request.Request(URL, data=json.dumps({"hello": "mdeis"}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as e:
        print(f"FAIL: {URL} answered {e.code}. If 404, the workflow is imported but not active (spike T-024).")
        return 1
    except OSError as e:
        print(f"FAIL: {URL}: {e}")
        return 1
    print(f"OK: {URL} answered: {body}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
