"""The build graph. One acknowledged methodology in, proposed functions waiting at the demo out.

    plan      code    `build_plan`: which sections are built, which are skipped (and why), which
                      were removed. The answer also carries the repair bound and the lists the
                      prompts need, so this package reads no file
    retire    code    `build_retire_removed`: measures built from a removed section are retired
    next      code    the next section to build, or the end
    extract   model   ONE call, JSON only, an EMPTY tool list: the values the section states
    settle    code    `build_settle_rules`: a rule is kept only if its quote is literally in the
                      text; one home per setting (read the template, or propose a clause); a value
                      that conflicts with the template stops the whole build
    propose   model   ONE call, JSON only, an EMPTY tool list: the function and its tests
    check     code    `build_check_function`: the static check, the tests in the sandbox, the
                      sensitivity check, the exact-numbers rule. A failure goes back to `propose`
                      with the plain reasons, at most the bound in the build rules
    store     code    `build_store_version`: re-runs every check, then writes the version, its tests
                      and its patch to the registry as `awaiting_review`
    review    model   ONE call, JSON only, an EMPTY tool list: the automated reviewer. It can only
                      HOLD. Its answer, or a failure to answer, is recorded as given; anything that
                      is not a clear `pass` is a hold
    record    code    `build_record_review`: a pass lets the version go on to the demo; a hold keeps
                      it where it is until a person clears it
    finish    code    the answer, with the step trace

What the graph cannot do, by construction:
* it has no tool list: every model call passes `[]`, and the model's own tool calls are ignored;
* it calls six tools by name (`GRAPH_TOOLS`) and `call_tool` refuses any other name;
* it holds no token, no approver and no school: the build is by the system. Nothing it sends names a
  person, and the model is never shown one;
* it never approves, confirms, rejects or releases: it has no such tool;
* a version only reaches the registry after the server has run every check again itself, so the
  graph cannot store a function the checks refused, and the reviewer is never asked about one.

Like the other graphs, it runs on LangGraph when that is installed and on a plain loop otherwise,
with the same nodes and routes. Every tool call is an action-log row written by the MCP server.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from .. import graph as chat_graph
from ..model import ModelError
from ..tools import MDEIS
from . import (
    CHECK_TOOL,
    EXTRACT,
    FUNCTION,
    GRAPH_TOOLS,
    PLAN_TOOL,
    RETIRE_TOOL,
    REVIEW,
    REVIEW_TOOL,
    SETTLE_TOOL,
    STORE_TOOL,
    TASKS,
    answers,
    prompt,
)

END = chat_graph.END
START_NODE = "plan"
# A bound on the loop, not a school rule: the most nodes one run may pass through.
MAX_NODES = 1000

# Words of the interface. None is a rule, a number about a learner or a label for one.
NOT_BUILT = "Nothing was built: "
STEP_FAILED = "A step failed, so the build stopped. Nothing further was stored."
NO_SANDBOX = "The sandbox could not be reached, so no function could be tested. Nothing was stored."
FINISHED = (
    "Each section is listed with what happened to it. Nothing here is approved: a version that "
    "passed every check waits at the demo for a teacher, and a held version waits for a person."
)
NOTHING = "Nothing in this methodology needs a new measure."
CONFLICT = (
    "The build stopped: setting_conflict. A value in the document differs from the school's "
    "template, so nothing was built from it. The conflicts are listed; a person decides which is "
    "right."
)


class NotAllowed(Exception):
    """A node asked for a tool that is not one of the graph's six."""


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
    goto: str  # the node a node chose to go to next
    plan: dict
    build: dict  # what the plan said the prompts and bounds need
    queue: list  # sections still to build
    section: dict | None
    output: str  # the model's last answer, exactly as returned
    problems: list  # plain reasons to send back to the model
    extract_repairs: int  # repeats used on this section
    function_repairs: int
    repairs: dict  # repeats used over the whole run, by job
    settled: dict
    proposal: dict
    checked: dict
    stored: dict
    review_missing: bool  # the reviewer could not be reached
    results: list  # one entry for each section that was tried
    conflicts: list
    retired: list
    model_calls: int
    ignored_calls: int  # tool calls the model asked for and the graph did not run
    outcome: str  # finished, nothing_to_build, setting_conflict, refused or error
    statement: str
    final: dict


@dataclass
class Deps:
    settings: Any
    model: Any
    server: Any  # the mdeis server client, carrying the run id and no approver (a system build)
    run_id: str
    document_id: str
    build_anyway: list


def initial_state() -> State:
    return State(
        steps=[], goto="", plan={}, build={}, queue=[], section=None, output="", problems=[],
        extract_repairs=0, function_repairs=0, repairs={EXTRACT: 0, FUNCTION: 0}, settled={},
        proposal={}, checked={}, stored={}, review_missing=False, results=[], conflicts=[],
        retired=[], model_calls=0, ignored_calls=0, outcome="", statement="", final={},
    )  # fmt: skip


def step_of(node: str, tool: str, result: dict) -> dict:
    return {"node": node, "tool": tool, "status": result.get("status")}


def problem_lines(problems: list) -> list[str]:
    """What a failed check says, for the model, in plain words. Codes and details only."""
    lines = []
    for p in problems:
        where = f" [{p['test']}]" if p.get("test") else ""
        lines.append(f"{p.get('check')}: {p.get('code')}{where}: {p.get('detail')}")
    return lines


def build(deps: Deps) -> chat_graph.Machine:
    async def ask_model(messages: list):
        """The one place a model is called. The tool list is empty. Always."""
        return await deps.model.complete(messages, [])

    def asked(state: State, task: str, section_id: str, reply) -> dict:
        return {
            "steps": [{"node": task, "section": section_id}],
            "output": reply.text,
            "model_calls": state["model_calls"] + 1,
            "ignored_calls": state["ignored_calls"] + len(reply.calls),
        }

    def section_done(state: State, outcome: str, **more) -> dict:
        section = state["section"]
        entry = {"section_id": section["section_id"], "outcome": outcome, **more}
        return {"results": [*state["results"], entry], "goto": "next"}

    def dropped_of(state: State) -> list:
        return state["settled"].get("dropped", []) if state.get("settled") else []

    async def plan(state: State) -> dict:
        arguments = {"document_id": deps.document_id, "build_anyway": deps.build_anyway}
        result = await call_tool(deps.server, PLAN_TOOL, arguments)
        step = step_of("plan", PLAN_TOOL, result)
        if result.get("status") == "refused":
            reason = result.get("reason")
            text = NOT_BUILT + (reason if isinstance(reason, str) else "it was refused.")
            return {"steps": [step], "outcome": "refused", "statement": text, "goto": "finish"}
        if result.get("status") != "ok" or not isinstance(result.get("build"), dict):
            return {"steps": [step], "outcome": "error", "statement": STEP_FAILED, "goto": "finish"}
        if result.get("removed"):
            goto = "retire"
        else:
            goto = "next" if result.get("sections") else "finish"
        return {
            "steps": [step], "plan": result, "build": result["build"],
            "queue": list(result.get("sections") or []), "goto": goto,
        }  # fmt: skip

    async def retire(state: State) -> dict:
        arguments = {"document_id": deps.document_id, "section_ids": state["plan"]["removed"]}
        result = await call_tool(deps.server, RETIRE_TOOL, arguments)
        step = step_of("retire", RETIRE_TOOL, result)
        retired = result.get("retired") if result.get("status") == "ok" else []
        goto = "next" if state["queue"] else "finish"
        return {"steps": [step], "retired": list(retired or []), "goto": goto}

    async def next_section(state: State) -> dict:
        queue = list(state["queue"])
        if not queue:
            return {"steps": [{"node": "next"}], "section": None, "goto": "finish"}
        return {
            "steps": [{"node": "next", "section": queue[0]["section_id"]}],
            "queue": queue[1:], "section": queue[0], "problems": [], "extract_repairs": 0,
            "function_repairs": 0, "settled": {}, "proposal": {}, "checked": {}, "stored": {},
            "review_missing": False, "output": "", "goto": "extract",
        }  # fmt: skip

    async def extract(state: State) -> dict:
        section = state["section"]
        messages = [
            {"role": "system", "content": prompt.system_prompt(EXTRACT)},
            {"role": "user",
             "content": prompt.extract_message(deps.run_id, section, state["build"])},
        ]  # fmt: skip
        if state["problems"]:
            messages.append({"role": "user", "content": prompt.repair_message(state["problems"])})
        try:
            reply = await ask_model(messages)
        except ModelError as error:
            return {"steps": [{"node": EXTRACT}], "outcome": "error", "statement": str(error),
                    "goto": "finish"}  # fmt: skip
        return {**asked(state, EXTRACT, section["section_id"], reply), "goto": "settle"}

    def unreadable(state: State, job: str, problems: list) -> dict:
        """The answer could not be read: send it back, or give the section up after the bound."""
        counter = f"{job}_repairs"
        if state[counter] < state["build"]["repair_rounds"]:
            repairs = {**state["repairs"], job: state["repairs"][job] + 1}
            return {counter: state[counter] + 1, "problems": problems, "repairs": repairs,
                    "goto": job}  # fmt: skip
        reason = "extraction_failed" if job == EXTRACT else "function_failed"
        return section_done(state, reason, problems=problems, dropped=dropped_of(state))

    async def settle(state: State) -> dict:
        section = state["section"]
        rules, problems = answers.read_rules(state["output"])
        if rules is None:
            return unreadable(state, EXTRACT, problems)
        if not rules:
            return section_done(state, "no_rules", dropped=[])
        arguments = {"document_id": deps.document_id, "section_id": section["section_id"],
                     "rules": rules}  # fmt: skip
        result = await call_tool(deps.server, SETTLE_TOOL, arguments)
        step = step_of("settle", SETTLE_TOOL, result)
        status = result.get("status")
        if status == "setting_conflict":
            return {"steps": [step], "outcome": "setting_conflict", "statement": CONFLICT,
                    "conflicts": result.get("conflicts") or [], "goto": "finish"}  # fmt: skip
        if status != "ok":
            update = section_done(state, "failed", reason=str(result.get("reason") or status))
            return {**update, "steps": [step]}
        if not result.get("reads"):
            update = section_done(state, "no_rules", dropped=result.get("dropped") or [])
            return {**update, "steps": [step]}
        return {"steps": [step], "settled": result, "problems": [], "goto": FUNCTION}

    async def propose(state: State) -> dict:
        section = state["section"]
        messages = [
            {"role": "system", "content": prompt.system_prompt(FUNCTION)},
            {"role": "user",
             "content": prompt.function_message(deps.run_id, section, state["settled"],
                                                state["build"])},
        ]  # fmt: skip
        if state["problems"]:
            messages.append({"role": "user", "content": prompt.repair_message(state["problems"])})
        try:
            reply = await ask_model(messages)
        except ModelError as error:
            return {"steps": [{"node": FUNCTION}], "outcome": "error", "statement": str(error),
                    "goto": "finish"}  # fmt: skip
        return {**asked(state, FUNCTION, section["section_id"], reply), "goto": "check"}

    async def check(state: State) -> dict:
        section = state["section"]
        proposal, problems = answers.read_proposal(state["output"])
        if proposal is None:
            return unreadable(state, FUNCTION, problems)
        arguments = {"document_id": deps.document_id, "section_id": section["section_id"],
                     "settled": state["settled"], "proposal": proposal}  # fmt: skip
        result = await call_tool(deps.server, CHECK_TOOL, arguments)
        step = step_of("check", CHECK_TOOL, result)
        status = result.get("status")
        if status == "error" and result.get("kind") == "runner_unavailable":
            return {"steps": [step], "outcome": "error", "statement": NO_SANDBOX, "goto": "finish"}
        if status != "ok":
            update = section_done(state, "failed", reason=str(result.get("reason") or status),
                                  dropped=dropped_of(state))  # fmt: skip
            return {**update, "steps": [step]}
        if result.get("passed") is True:
            return {"steps": [step], "proposal": proposal, "checked": result, "goto": "store"}
        found = result.get("problems") or []
        counter = "function_repairs"
        if state[counter] < state["build"]["repair_rounds"]:
            repairs = {**state["repairs"], FUNCTION: state["repairs"][FUNCTION] + 1}
            return {"steps": [step], counter: state[counter] + 1, "problems": problem_lines(found),
                    "repairs": repairs, "goto": FUNCTION}  # fmt: skip
        update = section_done(state, "failed", reason="checks_failed", problems=found,
                              dropped=dropped_of(state))  # fmt: skip
        return {**update, "steps": [step]}

    async def store(state: State) -> dict:
        section = state["section"]
        arguments = {"document_id": deps.document_id, "section_id": section["section_id"],
                     "settled": state["settled"], "proposal": state["proposal"]}  # fmt: skip
        result = await call_tool(deps.server, STORE_TOOL, arguments)
        step = step_of("store", STORE_TOOL, result)
        if result.get("status") == "stored":
            return {"steps": [step], "stored": result, "goto": REVIEW}
        update = section_done(state, "failed", reason="not_stored",
                              problems=result.get("problems") or [],
                              detail=str(result.get("reason") or result.get("status")),
                              dropped=dropped_of(state))  # fmt: skip
        return {**update, "steps": [step]}

    async def review(state: State) -> dict:
        section = state["section"]
        messages = [
            {"role": "system", "content": prompt.system_prompt(REVIEW)},
            {"role": "user",
             "content": prompt.review_message(deps.run_id, section, state["proposal"],
                                              state["settled"], state["checked"])},
        ]  # fmt: skip
        try:
            reply = await ask_model(messages)
        except ModelError:
            # The reviewer could not be reached. That is a hold, never a pass.
            return {"steps": [{"node": REVIEW, "section": section["section_id"]}],
                    "output": "", "review_missing": True, "goto": "record"}  # fmt: skip
        return {**asked(state, REVIEW, section["section_id"], reply), "goto": "record"}

    async def record(state: State) -> dict:
        if state["review_missing"]:
            result, reasons = "hold", [answers.REVIEW_UNREACHED]
        else:
            result, reasons = answers.read_review(state["output"])
        stored = state["stored"]
        arguments = {"measure_id": stored["measure_id"], "version": stored["version"],
                     "result": result, "reasons": reasons}  # fmt: skip
        reply = await call_tool(deps.server, REVIEW_TOOL, arguments)
        step = step_of("record", REVIEW_TOOL, reply)
        if reply.get("status") != "ok":
            update = section_done(state, "review_not_recorded", measure_id=stored["measure_id"],
                                  version=stored["version"], dropped=dropped_of(state))  # fmt: skip
            return {**update, "steps": [step]}
        outcome = "awaiting_demo" if reply.get("version_status") == "awaiting_demo" else "held"
        update = section_done(
            state, outcome, measure_id=stored["measure_id"], version=stored["version"],
            version_status=reply.get("version_status"), review_result=result,
            review_reasons=reasons, patch_id=stored.get("patch_id"),
            clause_ids=stored.get("clause_ids"), dropped=dropped_of(state),
        )  # fmt: skip
        return {**update, "steps": [step]}

    async def finish(state: State) -> dict:
        planned = state.get("plan") or {}
        outcome = state.get("outcome")
        if not outcome:
            outcome = "finished" if planned.get("sections") else "nothing_to_build"
        statement = state.get("statement") or (FINISHED if outcome == "finished" else NOTHING)
        called = state["model_calls"]
        answer = {
            "outcome": outcome,
            "statement": statement,
            "run_id": deps.run_id,
            "document_id": deps.document_id,
            "document_version": planned.get("document_version"),
            "template_version": planned.get("template_version"),
            "sections": state["results"],
            "skipped": planned.get("skipped") or [],
            "build_anyway": planned.get("build_anyway") or [],
            "not_measures": planned.get("not_measures") or [],
            "removed": planned.get("removed") or [],
            "retired": state["retired"],
            "conflicts": state["conflicts"],
            "model": deps.settings.model if called else None,
            "prompt_sha256": {task: prompt.system_hash(task) for task in TASKS},
            "model_calls": called,
            "repair_rounds": state["repairs"],
            "ignored_model_requests": state["ignored_calls"],
            "steps": [*state["steps"], {"node": "finish"}],
        }
        return {"final": answer, "goto": END}

    def goto(state: State) -> str:
        return state["goto"]

    nodes = {
        "plan": plan, "retire": retire, "next": next_section, EXTRACT: extract, "settle": settle,
        FUNCTION: propose, "check": check, "store": store, REVIEW: review, "record": record,
        "finish": finish,
    }  # fmt: skip
    routes = {name: goto for name in nodes}
    targets = {
        "plan": ["retire", "next", "finish"], "retire": ["next", "finish"],
        "next": [EXTRACT, "finish"], EXTRACT: ["settle", "finish"],
        "settle": [EXTRACT, FUNCTION, "next", "finish"], FUNCTION: ["check", "finish"],
        "check": [FUNCTION, "store", "next", "finish"], "store": [REVIEW, "next"],
        REVIEW: ["record"], "record": ["next"], "finish": [END],
    }  # fmt: skip
    return chat_graph.Machine(nodes, routes, targets)


async def run_plain(machine: chat_graph.Machine, state: State) -> State:
    """The same machine without LangGraph."""
    current, passed = START_NODE, 0
    while current != END:
        passed += 1
        if passed > MAX_NODES:
            state.update(outcome="error", statement=STEP_FAILED)
            current = "finish"
        update = dict(await machine.nodes[current](state))
        state["steps"] = [*state["steps"], *update.pop("steps", [])]
        state.update(update)
        current = machine.routes[current](state)
    return state


async def run_langgraph(machine: chat_graph.Machine, state: State) -> State:
    from langgraph.graph import END as LG_END
    from langgraph.graph import START, StateGraph

    graph = StateGraph(State)
    for name, node in machine.nodes.items():
        graph.add_node(name, node)
    graph.add_edge(START, START_NODE)
    for name, route in machine.routes.items():
        mapping = {t: (LG_END if t == END else t) for t in machine.targets[name]}
        graph.add_conditional_edges(name, route, mapping)
    return await graph.compile().ainvoke(state, {"recursion_limit": MAX_NODES})


async def run(
    *,
    document_id: str,
    build_anyway: list,
    run_id: str,
    settings,
    model,
    servers: dict,
    engine: str | None = None,
) -> dict:
    """One methodology through the graph. `servers` is the run's tool-server clients (no token: the
    build is by the system). Returns the answer: the outcome, a plain statement, what happened to
    each section, the skipped sections and the step trace."""
    deps = Deps(
        settings=settings, model=model, server=servers[MDEIS], run_id=run_id,
        document_id=document_id, build_anyway=list(build_anyway or []),
    )  # fmt: skip
    machine, state = build(deps), initial_state()
    engine = engine or ("langgraph" if chat_graph.langgraph_available() else "plain")
    if engine == "langgraph":
        state = await run_langgraph(machine, state)
    else:
        state = await run_plain(machine, state)
    return state["final"]


__all__ = ["Deps", "NotAllowed", "build", "call_tool", "run"]
