#!/usr/bin/env python3
"""Turn the database's school-reference -> learner-number map into a fixture-handle map.

Run on the host by `make evals`: it reads data/synthetic/truth/fixtures.json and the JSON that psql
printed ({"OCC/2024/0211": "L-0007", ...}) and prints {"FX-01": "L-0007", ...}. Exit 1 and say which
fixture is missing if a fixture learner has not been loaded (run `make demo-data` first).
Standard library only.
"""
import json
import sys
from pathlib import Path

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
    if missing:
        print("Not loaded: " + ", ".join(missing) + ". Run `make demo-data` first.", file=sys.stderr)
        return 1
    print(json.dumps(found))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
