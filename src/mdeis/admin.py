"""The admin steps a person runs from the command line. The agent never calls these (SPEC.md 5.9).

    python -m mdeis.admin load-criteria templates/v2-strict.yaml templates/v1.yaml --by "Ada Teacher"

`load-criteria` is the name SPEC.md uses. It does what `python -m mdeis.rulebook load` does, and
takes the same options. The rulebook module also has embed, activate, status and search. More
admin steps (ingest, ack-methodology) are added here by the document tasks.
"""

from __future__ import annotations

import sys

from mdeis import rulebook

# the admin name -> the rulebook command it runs
COMMANDS = {"load-criteria": "load"}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in COMMANDS:
        print(
            "usage: python -m mdeis.admin load-criteria FILE [FILE ...] [--by NAME] [--no-activate]",
            file=sys.stderr,
        )
        return 2
    return rulebook.main([COMMANDS[argv[0]], *argv[1:]])


if __name__ == "__main__":
    sys.exit(main())
