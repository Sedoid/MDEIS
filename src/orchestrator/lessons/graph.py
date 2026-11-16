"""The lesson graph. One lesson in, one checked plan (or a plain reason) out.

    gather    code    read the class for the chosen stage and term (`class_baseline`); keep the
                      finding of the chosen area
    guard     code    the notes and the objective go to `guard_lesson_notes` (a learner's name or
                      number refuses them); then a cheap look at the evidence
    explain   code    no usable evidence: the plain statement comes from the MCP server, no model
    propose   model   ONE call through the model client, JSON only, an EMPTY tool list; the notes
                      and the evidence sit in marked data blocks
    store     code    the answer goes, exactly as returned, to `save_lesson_plan`, which runs every
                      check (LC-1 to LC-10), applies the edits and stores the plan. A failing edit
                      list goes back to `propose` with the plain reasons and LC ids only, at most
                      `MAX_REPAIR_ROUNDS` times
    present   code    the stored plan, with the model's words labelled as a suggestion
    finish    code    the answer, with a step trace

What the graph cannot do, by construction:
* it has no tool list: every model call passes `[]`, and the model's own tool calls are ignored;
* it calls three tools by name (`GRAPH_TOOLS`) and `call_tool` refuses any other name;
* it holds no token, no approver and no school: the servers it is given carry the signed token, so
  the author and the school are the verified session's, never an argument;
* it holds no roster and never touches the plan table: the checks and the store run in the MCP
  server;
* it shows nothing but what `save_lesson_plan` returned as `stored`: an unchecked edit has no way
  in.

Every tool call is an action-log row written by the MCP server. The model call has no tool, so its
record is the `lesson_save_plan` row that receives its output (model name, prompt hash and the hash
of the output). Like the chat graph, it runs on LangGraph when that is installed and on a plain
loop otherwise, with the same nodes and routes.
"""

from __future__ import annotations

import hashlib
import operator
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from .. import graph as chat_graph
from ..model import ModelError
from ..tools import MDEIS
from . import (
    CLASS_TOOL,
    EMPTY_EDITS,
    GRAPH_TOOLS,
    GUARD_TOOL,
    MAX_REPAIR_ROUNDS,
    NO_MODEL,
    SAVE_TOOL,
    SUGGESTION_LABEL,
    evidence,
    prompt,
)

END = chat_graph.END
START_NODE = "gather"
NODES_SPARE = 8  # nodes beyond propose and store for every round: the start and the end

# Words of the interface. None is a rule, a number about a learner or a label for one.
NOT_ADAPTED = "The lesson was not adapted: "
STEP_FAILED = "A step failed, so the lesson was not adapted. Nothing was saved."
STORED = (
    "These edits passed the checks. Nothing is approved: each edit is for the tutor to accept or "
    "reject."
)
NO_VALID = "No edit passed the checks, so nothing is shown for approval."
GAVE_UP = (
    "The edit list still did not pass the checks after the allowed number of tries, so nothing is "
    "shown for approval."
)
NO_EVIDENCE = "There is not enough class evidence to adapt this lesson."


class NotAllowed(Exception):
    """A node asked for a tool that is not one of the graph's three."""


async def call_tool(server, name: str, arguments: dict) -> dict:
    """The only way a node reaches a tool. A failure is a result, never an exception, and never
    carries the error text (it could hold a record)."""
    if name not in GRAPH_TOOLS:
        raise NotAllowed(name)
    try:
        result = await server.call(name, arguments)
    except Exception as error:  # (the class only)
        return {"status": "error", "detail": f"the tool failed ({type(error).__name__})"}
    if not isinstance(result, dict):
        return {"status": "error", "detail": "the tool answered in a form I cannot read"}
    return result


class State(TypedDict, total=False):
    steps: Annotated[list, operator.add]  # the trace: names only, each node adds its own
    finding: dict | None  # the chosen area's evidence, as the model sees it
    output: str  # the model's last answer, exactly as returned (kept to hand to the save tool)
    problems: list  # plain reasons to send back to the model
    reasons: list  # the reasons of the last failed attempt, to show
    dropped: list
    repairs: int
    model_calls: int
    ignored_calls: int  # tool calls the model asked for and the graph did not run
    outcome: str  # stored, no_evidence, no_valid_edits, refused or error
    statement: str
    saved: dict  # the `stored` result of save_lesson_plan
    final: dict


@dataclass
class Deps:
    settings: Any
    model: Any
    server: Any  # the mdeis server client, already carrying the signed token and the run id
    run_id: str
    stage: str
    area: str
    term: int
    objective: str
    notes: str


def initial_state() -> State:
    return State(
        steps=[], finding=None, output="", problems=[], reasons=[], dropped=[], repairs=0,
        model_calls=0, ignored_calls=0, outcome="", statement="", saved={}, final={},
    )  # fmt: skip


def stopped(step: dict, result: dict) -> dict:
    """A tool refused or failed: say why in plain words and end."""
    if result.get("status") == "refused":
        reason = result.get("reason")
        return {
            "steps": [step],
            "outcome": "refused",
            "statement": NOT_ADAPTED + (reason if isinstance(reason, str) else "it was refused."),
        }
    return {"steps": [step], "outcome": "error", "statement": STEP_FAILED}


def step_of(node: str, tool: str, result: dict) -> dict:
    return {"node": node, "tool": tool, "status": result.get("status")}


def build(deps: Deps) -> chat_graph.Machine:
    selection = {"stage": deps.stage, "area": deps.area, "term": deps.term}

    def save_arguments(output: str, model: str) -> dict:
        return {
            **selection,
            "objective": deps.objective,
            "notes": deps.notes,
            "model_output": output,
            "model": model,
            "prompt_sha256": prompt.system_hash(),
        }

    async def gather(state: State) -> dict:
        result = await call_tool(deps.server, CLASS_TOOL, {"stage": deps.stage, "term": deps.term})
        step = step_of("gather", CLASS_TOOL, result)
        if result.get("status") in ("refused", "error"):
            return stopped(step, result)
        return {"steps": [step], "finding": evidence.finding_view(result, deps.area)}

    async def guard(state: State) -> dict:
        arguments = {**selection, "objective": deps.objective, "notes": deps.notes}
        result = await call_tool(deps.server, GUARD_TOOL, arguments)
        step = step_of("guard", GUARD_TOOL, result)
        if result.get("status") != "ok":
            return stopped(step, result)
        if not evidence.has_visible_gap(state.get("finding")):
            return {"steps": [step], "outcome": "no_evidence"}
        return {"steps": [step]}

    async def explain(state: State) -> dict:
        # No model was called. The save tool says in the rulebook's own words why there is nothing.
        result = await call_tool(
            deps.server, SAVE_TOOL, save_arguments(EMPTY_EDITS, NO_MODEL)
        )
        step = step_of("explain", SAVE_TOOL, result)
        if result.get("status") in ("refused", "error"):
            return stopped(step, result)
        statement = result.get("statement")
        return {
            "steps": [step],
            "outcome": "no_evidence",
            "statement": statement if isinstance(statement, str) and statement else NO_EVIDENCE,
        }

    async def propose(state: State) -> dict:
        messages = [
            {"role": "system", "content": prompt.system_prompt()},
            {
                "role": "user",
                "content": prompt.user_message(
                    deps.run_id, deps.objective, deps.notes, state["finding"]
                ),
            },
        ]
        if state["problems"]:
            messages.append({"role": "user", "content": prompt.repair_message(state["problems"])})
        step = {"node": "propose", "round": state["repairs"]}
        try:
            reply = await deps.model.complete(messages, [])  # the tool list is empty. Always.
        except ModelError as error:
            return {"steps": [step], "outcome": "error", "statement": str(error)}
        return {
            "steps": [step],
            "output": reply.text,
            "model_calls": state["model_calls"] + 1,
            "ignored_calls": state["ignored_calls"] + len(reply.calls),
        }

    async def store(state: State) -> dict:
        arguments = save_arguments(state["output"], deps.settings.model)
        result = await call_tool(deps.server, SAVE_TOOL, arguments)
        step = step_of("store", SAVE_TOOL, result)
        status = result.get("status")
        if status == "stored":
            return {"steps": [step], "outcome": "stored", "saved": result}
        if status in ("insufficient_evidence", "no_gap"):
            statement = result.get("statement")
            return {
                "steps": [step],
                "outcome": "no_evidence",
                "statement": statement if isinstance(statement, str) and statement else NO_EVIDENCE,
                "dropped": result.get("dropped") or [],
            }
        if status == "invalid_output":
            problems = [{"id": "all", "lc": ["LC-10"], "reason": str(result.get("reason"))}]
            repairable, dropped = bool(result.get("repairable")), []
        elif status == "no_valid_edits":
            dropped = result.get("dropped") or []
            problems = [
                {"id": d.get("id"), "lc": list(d.get("lc") or []), "reason": d.get("reason")}
                for d in dropped
            ]
            repairable = any(d.get("repairable") for d in dropped)
        else:
            return stopped(step, result)
        if repairable and state["repairs"] < MAX_REPAIR_ROUNDS:
            return {"steps": [step], "problems": problems, "repairs": state["repairs"] + 1}
        return {
            "steps": [step],
            "outcome": "no_valid_edits",
            "statement": GAVE_UP if repairable else NO_VALID,
            "dropped": dropped,
            "reasons": problems,
        }

    async def present(state: State) -> dict:
        saved = state["saved"]
        edits = [
            {**edit, "suggestion": {"label": SUGGESTION_LABEL, "text": edit.get("suggestion")}}
            for edit in saved.get("edits") or []
        ]
        plan = {
            "plan_id": saved.get("plan_id"),
            "plan_status": saved.get("plan_status"),
            "stage": saved.get("stage"),
            "area": saved.get("area"),
            "term": saved.get("term"),
            "template_version": saved.get("template_version"),
            "objective": saved.get("objective"),
            "original_text": deps.notes,
            "original_sha256": saved.get("original_sha256"),
            "revised_text": saved.get("revised_text"),
            "revised_sha256": saved.get("revised_sha256"),
            "diff": saved.get("diff"),
            "edits": edits,
            "dropped": saved.get("dropped") or [],
            "notices": saved.get("notices") or [],
        }
        return {"steps": [{"node": "present"}], "final": {"plan": plan}}

    async def finish(state: State) -> dict:
        outcome = state.get("outcome") or "error"
        called = state["model_calls"]
        shown = state.get("final", {}).get("plan")
        answer = {
            "outcome": outcome,
            "statement": STORED if outcome == "stored" else state.get("statement") or STEP_FAILED,
            "run_id": deps.run_id,
            "plan": shown,
            "dropped": [] if outcome == "stored" else state.get("dropped", []),
            "reasons": [] if outcome == "stored" else state.get("reasons", []),
            "model": deps.settings.model if called else None,
            "prompt_sha256": prompt.system_hash() if called else None,
            "model_calls": called,
            "repair_rounds": state["repairs"],
            "ignored_model_requests": state["ignored_calls"],
            "model_output_sha256": (
                hashlib.sha256(state["output"].encode("utf-8")).hexdigest()
                if outcome == "stored"
                else None
            ),
            "steps": [*state["steps"], {"node": "finish"}],
        }
        return {"final": answer}

    def after_gather(state: State) -> str:
        return "finish" if state.get("outcome") else "guard"

    def after_guard(state: State) -> str:
        if state.get("outcome") == "no_evidence":
            return "explain"
        return "finish" if state.get("outcome") else "propose"

    def after_propose(state: State) -> str:
        return "finish" if state.get("outcome") else "store"

    def after_store(state: State) -> str:
        if state.get("outcome") == "stored":
            return "present"
        return "finish" if state.get("outcome") else "propose"

    nodes = {
        "gather": gather, "guard": guard, "explain": explain, "propose": propose,
        "store": store, "present": present, "finish": finish,
    }  # fmt: skip
    routes = {
        "gather": after_gather, "guard": after_guard, "explain": lambda s: "finish",
        "propose": after_propose, "store": after_store, "present": lambda s: "finish",
        "finish": lambda s: END,
    }  # fmt: skip
    targets = {
        "gather": ["finish", "guard"], "guard": ["explain", "finish", "propose"],
        "explain": ["finish"], "propose": ["finish", "store"],
        "store": ["present", "finish", "propose"], "present": ["finish"], "finish": [END],
    }  # fmt: skip
    return chat_graph.Machine(nodes, routes, targets)


async def run_plain(machine: chat_graph.Machine, state: State) -> State:
    """The same machine without LangGraph."""
    current = START_NODE
    while current != END:
        update = dict(await machine.nodes[current](state))
        state["steps"] = [*state["steps"], *update.pop("steps", [])]
        state.update(update)
        current = machine.routes[current](state)
    return state


async def run_langgraph(machine: chat_graph.Machine, state: State, limit: int) -> State:
    from langgraph.graph import END as LG_END
    from langgraph.graph import START, StateGraph

    graph = StateGraph(State)
    for name, node in machine.nodes.items():
        graph.add_node(name, node)
    graph.add_edge(START, START_NODE)
    for name, route in machine.routes.items():
        mapping = {t: (LG_END if t == END else t) for t in machine.targets[name]}
        graph.add_conditional_edges(name, route, mapping)
    return await graph.compile().ainvoke(state, {"recursion_limit": limit})


async def run(
    *,
    stage: str,
    area: str,
    term: int,
    objective: str,
    notes: str,
    run_id: str,
    settings,
    model,
    servers: dict,
    engine: str | None = None,
) -> dict:
    """One lesson through the graph. `servers` is the run's tool-server clients (the signed token is
    in their headers). Returns the answer: the outcome, a plain statement, the plan when one was
    stored, and the step trace."""
    deps = Deps(
        settings=settings, model=model, server=servers[MDEIS], run_id=run_id, stage=stage,
        area=area, term=term, objective=objective, notes=notes,
    )  # fmt: skip
    machine, state = build(deps), initial_state()
    engine = engine or ("langgraph" if chat_graph.langgraph_available() else "plain")
    if engine == "langgraph":
        limit = 2 * (MAX_REPAIR_ROUNDS + 1) + NODES_SPARE
        state = await run_langgraph(machine, state, limit)
    else:
        state = await run_plain(machine, state)
    return state["final"]
