#!/usr/bin/env python3
"""Ask the orchestrator one question directly (no gateway) and print what it streams back.

Runs inside the orchestrator container, where the shared key is already in the environment:

    make ask Q="How is learner L-0001 doing in English?" [TOKEN=<from make token>]

Without a token the question is answered as a request that needs no gate: every acting tool is
refused at the tool server, which is itself a good thing to see once.

It is a conversation: each call adds your question and the answer to a file in the container and
sends the whole history, as the gateway does. `make ask ... NEW=1` starts a new conversation.
Standard library only. Exit 0 after a final answer, 1 after an error event or a failed request.
"""
import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("ORCHESTRATOR_URL", "http://127.0.0.1:8300/run")
HISTORY = "/tmp/ask_conversation.json"
TIMEOUT_SECONDS = 600


def main(argv: list[str]) -> int:
    if len(argv) < 2 or not argv[1].strip():
        print('usage: ask_orchestrator.py "question" [token]', file=sys.stderr)
        return 2
    new = len(argv) > 3 and argv[3] in ("1", "yes", "new")
    history = []
    if not new and os.path.exists(HISTORY):
        with open(HISTORY, encoding="utf-8") as f:
            history = json.load(f)
    history.append({"role": "user", "content": argv[1]})
    body = {
        "conversation_id": "probe",
        "messages": history,
        "approver_token": argv[2] if len(argv) > 2 and argv[2] else None,
    }
    request = urllib.request.Request(
        URL,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ.get('ORCHESTRATOR_API_KEY', '')}",
        },
    )
    try:
        response = urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS)
    except urllib.error.HTTPError as e:
        print(f"The orchestrator refused the request (status {e.code}).", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError) as e:
        print(f"Could not reach the orchestrator ({type(e).__name__}).", file=sys.stderr)
        return 1
    name = None
    with response:
        for raw in response:
            line = raw.decode().rstrip("\n")
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
                if name == "status":
                    print(f"  . {data['text']}")
                elif name == "error":
                    print(f"ERROR: {data['text']}", file=sys.stderr)
                    return 1
                elif name == "final":
                    print("\nANSWER\n" + data["answer"])
                    history.append({"role": "assistant", "content": data["answer"]})
                    with open(HISTORY, "w", encoding="utf-8") as f:
                        json.dump(history, f)
                    print(f"\ntools called: {', '.join(data['tools']) or 'none'}")
                    print(f"run: {data['run_id']}")
                    return 0
    print("The stream ended without an answer.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
