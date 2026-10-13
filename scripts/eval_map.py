#!/usr/bin/env python3
"""Turn the database's school-reference -> learner-number map into a fixture-handle map.

Run on the host by `make evals`: it reads data/synthetic/truth/fixtures.json and the JSON that psql
printed ({"OCC/2024/0211": "L-0007", ...}) and prints {"FX-01": "L-0007", ...}. The evals need only
the learners in NEEDED. Exit 1 and say which of them is missing (run `make demo-data` first). The
fixtures that have no printed card (records only) are not loaded from cards and are skipped.
Standard library only.
"""
import json
import sys
from pathlib import Path

NEEDED = ("FX-01", "FX-04b", "FX-05", "FX-08")  # the learners the agent evals ask about
FIXTURES = Path(__file__).resolve().parents[1] / "data" / "synthetic" / "truth" / "fixtures.json"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: eval_map.py '<json from the database>'", file=sys.stderr)
        return 2
    loaded = json.loads(argv[1] or "{}")
    found, missing = {}, []
    for fixture in json.loads(FIXTURES.read_text(encoding="utf-8")):
        learner = loaded.get(fixture["school_ref"])
        if learner:
            found[fixture["handle"]] = learner
        else:
            missing.append(fixture["handle"])
    lacking = [h for h in NEEDED if h not in found]
    if lacking:
        print("Not loaded: " + ", ".join(lacking) + ". Run `make demo-data` first.", file=sys.stderr)
        return 1
    print(json.dumps(found))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
