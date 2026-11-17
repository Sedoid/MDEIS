"""Run the build graph for one methodology, from the command line, in the orchestrator container.
(This file is outside the `build/` package on purpose: the package names no model client, and this
is the one place that makes one.)

    python -m orchestrator.build_cli D-000123 [--build-anyway 6.4 ...] [--engine plain|langgraph]

It opens the MCP server with the run's id and NO approver token (a build is by the system), runs
the graph with the pinned model, and prints the answer as JSON: what happened to each section, the
sections that were skipped and why, and the step trace. Exit 0 when the build ran to its end (even
if every section was held or rejected), 1 when it was refused, stopped on a conflict or failed.

The server side must have the build tools switched on (MEASURE_BUILD_ENABLED). Nothing here approves
anything: a version that passes every check waits at the demo for a registered teacher.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from contextlib import asynccontextmanager

from .build import graph
from .config import load_settings
from .mcp_client import McpServer, headers_for
from .model import OpenRouterModel
from .tools import MDEIS

OK_OUTCOMES = ("finished", "nothing_to_build")
WAITING = "awaiting_demo"
HELD = "held"
OPEN_AT = "the Methods tab of the portal (#/methods)"


def waiting_lines(answer: dict) -> list[str]:
    """One plain line for each measure the build left waiting for a teacher, naming it and saying
    where to open it, and one for each that the automated review is holding. Made by code from the
    build's own answer: no model writes it, and nothing is sent to anyone."""
    lines = []
    for section in (answer or {}).get("sections") or []:
        outcome = section.get("outcome")
        if outcome not in (WAITING, HELD):
            continue
        what = (f"{section.get('measure_id')} version {section.get('version')} "
                f"(section {section.get('section_id')})")
        if outcome == WAITING:
            lines.append(f"Waiting for a teacher: {what}. Open {OPEN_AT} to review it.")
        else:
            lines.append(f"Held for a person before any teacher sees it: {what}. "
                         "A developer must clear the hold first.")
    return lines


@asynccontextmanager
async def open_server(settings, run_id: str):
    """The mdeis MCP server for this run. No token: the build names no person."""
    async with McpServer(MDEIS, settings.mdeis_url, headers_for(MDEIS, None, run_id)) as server:
        yield server


async def build(args, settings, model, opener=open_server) -> dict:
    run_id = f"BUILD-{uuid.uuid4()}"
    async with opener(settings, run_id) as server:
        return await graph.run(
            document_id=args.document_id,
            build_anyway=args.build_anyway,
            run_id=run_id,
            settings=settings,
            model=model,
            servers={MDEIS: server},
            engine=args.engine,
        )


def main(argv=None, *, env=None, model=None, opener=open_server) -> int:
    parser = argparse.ArgumentParser(prog="python -m orchestrator.build_cli")
    parser.add_argument("document_id", help="the methodology in force, for example D-000012")
    parser.add_argument("--build-anyway", action="append", default=[], metavar="SECTION",
                        help="build a section a template clause already covers; repeat for more")
    parser.add_argument("--engine", choices=("plain", "langgraph"), default=None)
    args = parser.parse_args(argv)
    settings = load_settings(env)
    if model is None:
        if not settings.model_ready:
            print("Refused: OPENROUTER_API_KEY and OPENROUTER_MODEL are not set.", file=sys.stderr)
            return 1
        model = OpenRouterModel(settings)
    answer = asyncio.run(build(args, settings, model, opener))
    print(json.dumps(answer, indent=1, sort_keys=True, ensure_ascii=False))
    for line in waiting_lines(answer):  # on stderr, so what is printed above stays one document
        print(line, file=sys.stderr)
    return 0 if answer["outcome"] in OK_OUTCOMES else 1


if __name__ == "__main__":
    sys.exit(main())
