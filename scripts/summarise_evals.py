#!/usr/bin/env python3
"""Print one line for each scenario of an agent-eval result file. Standard library only.

    python3 scripts/summarise_evals.py evals/results/agent-2026-10-14-0930.json
    python3 scripts/summarise_evals.py evals/results/agent-2026-10-14-0930.json --markdown

`--markdown` prints a table to paste into EVALS.md, with the date, model and commit. It adds nothing
that is not in the file.

A scenario that did not pass every run is listed with its rule, so the failures are as visible as
the passes. Nothing is hidden or rounded.
"""
import json
import sys


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print("usage: summarise_evals.py FILE [--markdown]", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as f:
        result = json.load(f)
    if len(argv) == 3 and argv[2] == "--markdown":
        print(f"Run {result['date']}, model `{result['model']}`, mode `{result['mode']}`, commit `{result['commit']}`, "
              f"{result['runs_each']} runs each.\n")
        print("| Test | Passed | Rule | Tools called in the first run |")
        print("| --- | --- | --- | --- |")
        for sc in result["scenarios"]:
            first = ", ".join(sc["runs"][0]["tools"]) or "none"
            print(f"| {sc['id']} | {sc['passed']} of {sc['of']} | {sc['rule']} | {first} |")
        return 0
    print(f"model {result['model'] or '(not set)'}, mode {result['mode']}, commit {result['commit']}")
    for sc in result["scenarios"]:
        flag = "ok  " if sc["passed"] == sc["of"] else "MISS"
        print(f"{flag} {sc['id']:6} {sc['passed']}/{sc['of']}  {sc['rule']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
