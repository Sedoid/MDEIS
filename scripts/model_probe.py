#!/usr/bin/env python3
"""Check that the chosen OpenRouter model can call a tool and then use the tool's answer.

Run it inside the orchestrator container, which already has the OPENROUTER_* variables:

    docker exec -i orchestrator-mdeis python - < scripts/model_probe.py
    docker exec -i orchestrator-mdeis python - vendor/model-slug < scripts/model_probe.py   (try another model)

It sends the same small question three times and passes only if the model asks for the tool every
time, with valid arguments, and then states the number the tool returned. It prints the model, the
provider that served it and the token counts, never the key. Standard library only. Exit 1 on any
failure. A pass shows tool use works. It does not show the model writes good reports.
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("OPENROUTER_MODEL", "")
PROVIDER = os.environ.get("OPENROUTER_PROVIDER", "")
PLACEHOLDER = "change-me"
RUNS = 3
SCORE = 13.5

TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_term_score",
        "description": "Return one learner's score out of 20 for one subject in one term.",
        "parameters": {
            "type": "object",
            "properties": {
                "learner_id": {"type": "string"},
                "subject": {"type": "string"},
                "term": {"type": "integer"},
            },
            "required": ["learner_id", "subject", "term"],
        },
    },
}]
MESSAGES = [
    {"role": "system", "content": "You are a test. Never guess a number: call the tool, then state what it returned."},
    {"role": "user", "content": "What was learner L-0007's Mathematics score in term 2? Use the tool."},
]


def post(messages: list) -> dict:
    routing = {"allow_fallbacks": False, "data_collection": "deny"}
    if PROVIDER and PROVIDER != PLACEHOLDER:
        routing["order"] = [PROVIDER]
    body = {"model": MODEL, "messages": messages, "tools": TOOLS, "tool_choice": "auto",
            "temperature": 0, "max_tokens": 300, "provider": routing}
    req = urllib.request.Request(
        f"{BASE}/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"FAIL: the API answered {e.code}: {e.read().decode()[:300]}") from None
    except OSError as e:
        raise SystemExit(f"FAIL: could not reach {BASE}: {e}") from None


def main() -> int:
    if not KEY or KEY == PLACEHOLDER:
        print("FAIL: OPENROUTER_API_KEY is not set in this container.")
        return 1
    if not MODEL or MODEL == PLACEHOLDER:
        print("FAIL: no model. Set OPENROUTER_MODEL in .env, or pass a slug: python - vendor/model-slug")
        return 1
    print(f"model {MODEL}; provider pin: {PROVIDER if PROVIDER and PROVIDER != PLACEHOLDER else 'none (not pinned)'}")
    ok = True
    served = set()
    for i in range(1, RUNS + 1):
        first = post(MESSAGES)
        served.add(str(first.get("provider")))
        msg = (first.get("choices") or [{}])[0].get("message") or {}
        calls = msg.get("tool_calls") or []
        if not calls or calls[0].get("function", {}).get("name") != "get_term_score":
            print(f"FAIL run {i}: the model did not call the tool (it said: {str(msg.get('content'))[:120]!r})")
            ok = False
            continue
        try:
            args = json.loads(calls[0]["function"]["arguments"])
            assert {"learner_id", "subject", "term"} <= set(args)
        except (ValueError, AssertionError, TypeError):
            print(f"FAIL run {i}: the tool arguments are not valid: {calls[0]['function'].get('arguments')!r}")
            ok = False
            continue
        followup = MESSAGES + [
            {"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls},
            {"role": "tool", "tool_call_id": calls[0].get("id", "call_1"), "content": json.dumps({"score": SCORE})},
        ]
        second = post(followup)
        text = str(((second.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        usage = second.get("usage") or {}
        if str(SCORE) in text:
            print(f"PASS run {i}: tool called with {args}, answer states {SCORE} "
                  f"(tokens in/out {usage.get('prompt_tokens')}/{usage.get('completion_tokens')})")
        else:
            print(f"FAIL run {i}: the answer does not state {SCORE}: {text[:120]!r}")
            ok = False
    print(f"served by: {', '.join(sorted(served))}")
    print("RESULT: tool use works" if ok else "RESULT: tool use did NOT work reliably, do not pin this model")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
