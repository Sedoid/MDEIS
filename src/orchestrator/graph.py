"""The agent as a graph (ORCHESTRATOR_MODE=graph, docs/SPEC.md section 7, D-015).

The model still chooses which tools to call and writes the words. The graph decides, in code, the
things that must not depend on the model:

    screen   refuse a request to rank, compare or advise (no model call)
    agent    ask the model what to do next
    act      run the tools it asked for
    ask      a problem blocks a subject: ask the approver in plain words and end the turn
    check    after every draft, run the citation check; on a fail, back to the model, at most
             ORCHESTRATOR_MAX_REDRAFTS times, then stop and say what is still wrong
    verify   a pass-through here; the second-check model (D-016) plugs in at this node
    finish   send the answer

The turn ends at "draft ready". `release_report` is not offered to the model until an earlier turn
ended with a draft that passed the check, and then only for that draft.

State is the conversation itself: the question put to the approver and the line "The draft R-...
has passed the citation check" are in the messages Open WebUI keeps and the gateway sends back each
time, so nothing has to be stored here. The state below holds no approver and no token; the token is
only in the header of the tool-server clients.

The nodes and routes are plain functions. LangGraph runs them when it is installed; if it is not,
`run_plain` runs the same machine without it, so the behaviour does not depend on the library.
"""

from __future__ import annotations

import json
import operator
import re
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from . import guards, loop, tools
from .model import ModelError, ToolCall

END = "__end__"
START_NODE = "screen"
READY = re.compile(r"The draft (R-\d{6,}) has passed the citation check")
ISSUE_ID = re.compile(r"I-\d{6,}")
NODES_PER_STEP = 3  # nodes one model step can pass through (agent, act, check)
NODES_SPARE = 10  # and a few more for the start and the end
LIMIT_NOTE = (
    "I reached my step limit before I finished. Ask me to carry on, or ask a narrower question."
)


class State(TypedDict, total=False):
    conversation: list  # what the model sees
    events: Annotated[list, operator.add]  # for the gateway; each node adds its own
    pending: list  # tool calls the model asked for and have not run
    called: list  # tool names, in order
    drafted: list  # report numbers written in this run
    asked: list  # issue numbers put to the approver in this run
    questions: list  # blocking problems found by the last tools, to ask about
    report: str  # a draft that still has to be checked
    draft_text: str  # the words of the draft that was saved, exactly as saved
    ready: str  # a draft that passed the check in this run
    steps: int
    redrafts: int
    present: bool  # the draft passed: the model now only shows it
    outcome: str  # how the run ends: answer, refused, ask, gave_up, limit, error
    answer: str


@dataclass
class Machine:
    """The graph as data: where each node is, and where it goes next."""

    nodes: dict
    routes: dict
    targets: dict = field(default_factory=dict)  # for LangGraph: every place a node may go


def event(name: str, **data) -> dict:
    return loop.event(name, **data)


def assistant_call(*calls: ToolCall, text: str = "") -> dict:
    """The assistant message that carries tool calls, in the chat format."""
    return {
        "role": "assistant",
        "content": text or None,
        "tool_calls": [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": c.arguments},
            }
            for c in calls
        ],
    }


def ready_reports(messages: list) -> set:
    """Reports that an earlier turn ended with as checked and waiting for the approver."""
    found = set()
    for m in messages:
        if m["role"] == "assistant":
            found.update(READY.findall(m["content"]))
    return found


def questions_from(result: dict, asked_before: set) -> list:
    """Blocking problems in a tool result that nobody has been asked about yet."""
    found = []
    for item in result.get("issues", []):
        if item.get("severity") == "blocking" and not item.get("answered"):
            found.append(item)
    for item in result.get("reasons", []):
        found.append(item)
    return [q for q in found if q.get("issue_id") not in asked_before]


def ask_text(questions: list) -> str:
    lines = ["Before I can go on, I need your help with the records:"]
    for k, q in enumerate(questions, start=1):
        options = q.get("possible_answers")
        choice = f" You can answer: {' or '.join(options)}." if options else ""
        subject = f" ({q['subject']})" if q.get("subject") else ""
        lines.append(f"{k}. {q['question']}{subject} [{q['issue_id']}]{choice}")
    lines.append(
        "Tell me what to do for each one, in your own words, and I will record your answer. "
        "If you would rather leave these subjects out, say so."
    )
    return "\n".join(lines)


def give_up_text(report_id: str, problems: list) -> str:
    listed = "; ".join(
        f"{p.get('reason', 'a problem')}"
        + (f" ({p['detail']})" if p.get("detail") else "")
        for p in problems[:5]
    )
    return (
        f"I could not write a draft that passes the citation check. The draft {report_id} is saved "
        f"but it cannot be released. What is still wrong: {listed}. "
        "Please look at the records or ask me to try again."
    )


def saved_text(report_id: str, words: str) -> str:
    """The draft exactly as saved, so that the approver reads what would be released."""
    quoted = "\n".join(f"> {line}" if line else ">" for line in words.splitlines())
    return f"\n\nThe draft {report_id} as saved, with its sources, word for word:\n\n{quoted}"


def ready_footer(report_id: str) -> str:
    return (
        f"\n\nThe draft {report_id} has passed the citation check and is waiting for your "
        "release. Nothing has been released."
    )


@dataclass
class Deps:
    """What the nodes use. Held by the nodes, not in the state."""

    settings: Any
    model: Any
    servers: dict
    definitions: list
    owner: dict
    token: str | None
    run_id: str
    last_user: str
    earlier_ready: set
    earlier_asked: set


def build(deps: Deps) -> Machine:
    """The nodes and routes for one run."""
    settings = deps.settings

    def offered(state: State) -> list:
        """The tools the model may see now. `release_report` only for a checked draft."""
        if state.get("present"):
            return []
        keep = bool(deps.earlier_ready)
        return [d for d in deps.definitions if keep or d["function"]["name"] != "release_report"]

    async def screen(state: State) -> dict:
        reason = guards.refusal_for(deps.last_user)
        update: dict = {"events": [event("status", text="Reading your question.")]}
        if reason:
            update.update(outcome="refused", answer=reason)
        return update

    async def agent(state: State) -> dict:
        # Showing a draft that already passed the check uses no tools: the limit does not stop it.
        if state["steps"] >= settings.max_steps and not state.get("present"):
            return {"outcome": "limit", "answer": LIMIT_NOTE}
        words = "Thinking." if not state["steps"] else "Working on it."
        update: dict = {"events": [event("status", text=words)], "steps": state["steps"] + 1}
        try:
            reply = await deps.model.complete(state["conversation"], offered(state))
        except ModelError as error:
            return {**update, "outcome": "error", "events": [event("error", text=str(error))]}
        if not reply.calls:
            return {**update, "outcome": "answer", "answer": guards.scrub(reply.text, deps.token)}
        message = assistant_call(*reply.calls, text=reply.text)
        return {
            **update,
            "conversation": [*state["conversation"], message],
            "pending": list(reply.calls),
        }

    async def run_tool(call: ToolCall, state: State, drafted: set) -> dict:
        return await loop.execute(
            call, deps.owner, deps.servers, deps.last_user, drafted, deps.earlier_ready
        )

    async def act(state: State) -> dict:
        conversation, called = list(state["conversation"]), list(state["called"])
        drafted, events = set(state["drafted"]), []
        asked_now = set(state["asked"]) | deps.earlier_asked
        questions, seen, report, draft_text = [], set(), None, ""
        for call in state["pending"]:
            result = await run_tool(call, state, drafted)
            called.append(call.name)
            content = guards.scrub(json.dumps(result, default=str), deps.token)
            conversation.append({"role": "tool", "tool_call_id": call.id, "content": content})
            events.append(event("status", text=f"Used {call.name}."))
            if call.name in ("ingest_assessments", "compute_trend"):
                for q in questions_from(result, asked_now):
                    if q["issue_id"] not in seen:
                        seen.add(q["issue_id"])
                        questions.append(q)
            if call.name == "draft_summary" and result.get("report_id"):
                report = str(result["report_id"])
                draft_text = str(json.loads(call.arguments).get("text", ""))
        return {
            "conversation": conversation,
            "called": called,
            "drafted": sorted(drafted),
            "pending": [],
            "questions": questions,
            "report": report or "",
            "draft_text": draft_text or state["draft_text"],
            "events": events,
        }

    async def ask(state: State) -> dict:
        questions = state["questions"]
        return {
            "outcome": "ask",
            "answer": ask_text(questions),
            "asked": [*state["asked"], *(q["issue_id"] for q in questions)],
        }

    async def check(state: State) -> dict:
        report = state["report"]
        call = ToolCall(
            f"graph-check-{state['redrafts'] + 1}",
            "check_citations",
            json.dumps({"report_id": report}),
        )
        result = await loop.execute(
            call, deps.owner, deps.servers, deps.last_user, set(state["drafted"])
        )
        # The check goes into the conversation as a tool call, so the model sees what ran.
        conversation = [
            *state["conversation"],
            assistant_call(call),
            {"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, default=str)},
        ]
        update: dict = {
            "conversation": conversation,
            "called": [*state["called"], "check_citations"],
            "report": "",
            "events": [event("status", text="Checked the draft against its sources.")],
        }
        if result.get("status") == "ok":
            note = (
                f"The draft {report} passed the citation check. "
                "Show it to the approver in plain words."
            )
            update.update(
                ready=report,
                conversation=[*conversation, {"role": "system", "content": note}],
            )
            return update
        redrafts = state["redrafts"] + 1
        update["redrafts"] = redrafts
        if redrafts > settings.max_redrafts:
            problems = result.get("problems", [])
            update.update(outcome="gave_up", answer=give_up_text(report, problems))
            return update
        problems = json.dumps(result.get("problems", []), default=str)
        note = (
            f"The citation check failed: {problems}. "
            "Fix exactly these problems and call draft_summary again."
        )
        update["conversation"] = [*conversation, {"role": "system", "content": note}]
        return update

    async def verify(state: State) -> dict:
        # The second-check model (D-016) is called here, in a later step. For now it passes.
        return {"present": True}

    async def finish(state: State) -> dict:
        outcome = state.get("outcome", "answer")
        if outcome == "error":
            return {"events": []}
        text = state.get("answer", "")
        if state.get("ready"):
            text += saved_text(state["ready"], state.get("draft_text", ""))
            text += ready_footer(state["ready"])
        extra = {
            "refused": {"refused": True},
            "ask": {"needs_answer": True},
            "limit": {"incomplete": True},
            "gave_up": {"incomplete": True},
        }.get(outcome, {})
        events = list(loop.final_events(text, state.get("called", []), deps.run_id, **extra))
        return {"events": events}

    def after_screen(state: State) -> str:
        return "finish" if state.get("outcome") == "refused" else "agent"

    def after_agent(state: State) -> str:
        return "act" if state.get("pending") else "finish"

    def after_act(state: State) -> str:
        if state.get("questions"):
            return "ask"
        return "check" if state.get("report") else "agent"

    def after_check(state: State) -> str:
        if state.get("outcome") == "gave_up":
            return "finish"
        return "verify" if state.get("ready") else "agent"

    nodes = {
        "screen": screen, "agent": agent, "act": act, "ask": ask,
        "check": check, "verify": verify, "finish": finish,
    }  # fmt: skip
    routes = {
        "screen": after_screen, "agent": after_agent, "act": after_act,
        "ask": lambda s: "finish", "check": after_check, "verify": lambda s: "agent",
        "finish": lambda s: END,
    }  # fmt: skip
    targets = {
        "screen": ["finish", "agent"], "agent": ["act", "finish"],
        "act": ["ask", "check", "agent"], "ask": ["finish"], "check": ["finish", "verify", "agent"],
        "verify": ["agent"], "finish": [END],
    }  # fmt: skip
    return Machine(nodes, routes, targets)


def initial_state(conversation: list) -> State:
    return State(
        conversation=conversation,
        events=[],
        pending=[],
        called=[],
        drafted=[],
        asked=[],
        questions=[],
        report="",
        draft_text="",
        ready="",
        steps=0,
        redrafts=0,
        present=False,
        outcome="",
        answer="",
    )


async def run_plain(machine: Machine, state: State):
    """Run the machine without LangGraph: the same nodes, the same routes."""
    current = START_NODE
    while current != END:
        update = dict(await machine.nodes[current](state))
        for item in update.pop("events", []):
            yield item
        state.update(update)
        current = machine.routes[current](state)


def langgraph_available() -> bool:
    try:
        import langgraph.graph  # noqa: F401
    except ImportError:
        return False
    return True


async def run_langgraph(machine: Machine, state: State, limit: int):
    """Run the machine as a LangGraph graph, streaming each node's events as it finishes."""
    from langgraph.graph import END as LG_END
    from langgraph.graph import START, StateGraph

    graph = StateGraph(State)
    for name, node in machine.nodes.items():
        graph.add_node(name, node)
    graph.add_edge(START, START_NODE)
    for name, route in machine.routes.items():
        mapping = {t: (LG_END if t == END else t) for t in machine.targets[name]}
        graph.add_conditional_edges(name, route, mapping)
    config = {"recursion_limit": limit}
    async for chunk in graph.compile().astream(state, config, stream_mode="updates"):
        for update in chunk.values():
            if isinstance(update, dict):
                for item in update.get("events", []):
                    yield item


async def run(
    *,
    messages: list,
    token: str | None,
    run_id: str,
    settings,
    model,
    servers: dict,
    engine: str | None = None,
):
    """The events of one run. `engine` is `langgraph`, `plain`, or None for LangGraph if present."""
    listed = {name: await server.list_tools() for name, server in servers.items()}
    definitions, owner, _dropped = tools.bind(listed, settings.pg_tools, settings.class_view)
    conversation = [{"role": "system", "content": loop.system_prompt(graph=True)}]
    conversation += loop.conversation_from(messages, token)
    visible = [m for m in messages if m["role"] in loop.KEPT_ROLES]
    said = [m["content"] for m in visible if m["role"] == "assistant"]
    deps = Deps(
        settings=settings,
        model=model,
        servers=servers,
        definitions=definitions,
        owner=owner,
        token=token,
        run_id=run_id,
        last_user=loop.latest_user_text(messages),
        earlier_ready=ready_reports(visible),
        earlier_asked={i for text in said for i in ISSUE_ID.findall(text)},
    )
    machine, state = build(deps), initial_state(conversation)
    engine = engine or ("langgraph" if langgraph_available() else "plain")
    if engine == "langgraph":
        limit = NODES_PER_STEP * (settings.max_steps + settings.max_redrafts) + NODES_SPARE
        stream = run_langgraph(machine, state, limit)
    else:
        stream = run_plain(machine, state)
    async for item in stream:
        yield item
