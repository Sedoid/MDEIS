#!/usr/bin/env python3
"""Ask the embedder for a vector twice and check size and repeatability. Standard library only.

Run from inside the network, through any container that has python (`make probe-embed` does this):
    docker compose exec -T mdeis-mcp python - < scripts/embed_probe.py
Prints the vector length and the first three values. Never prints a secret.
"""
import json
import os
import sys
import urllib.request

BASE = os.environ.get("EMBEDDER_URL", "http://embedder:8081")
EXPECTED_DIM = int(os.environ.get("EMBED_DIM", "768"))
# nomic-embed-text expects a task prefix on every input.
SENTENCE = "search_document: A trend is stated only with at least three assessments across two terms."


def embed(text: str) -> list[float]:
    body = json.dumps({"input": text}).encode()
    req = urllib.request.Request(f"{BASE}/v1/embeddings", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)["data"][0]["embedding"]


def main() -> int:
    first, second = embed(SENTENCE), embed(SENTENCE)
    print(f"call 1: length {len(first)}, first values {[round(v, 6) for v in first[:3]]}")
    print(f"call 2: length {len(second)}, first values {[round(v, 6) for v in second[:3]]}")
    ok = True
    if len(first) != EXPECTED_DIM:
        print(f"FAIL: expected {EXPECTED_DIM} dimensions (EMBED_DIM), got {len(first)}")
        ok = False
    if first != second:
        print("FAIL: the same sentence gave two different vectors")
        ok = False
    if ok:
        print(f"OK: {EXPECTED_DIM} dimensions, identical on repeat")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
