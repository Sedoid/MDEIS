"""The plain tool-calling loop (ORCHESTRATOR_MODE=linear, docs/SPEC.md section 7).

One run: call the model with the tools it may see, run the tool calls it asks for, give it the
results, and stop when it answers without a tool call or the step limit is reached. The conversation
arrives whole each time (the service keeps no state). The loop yields events for the gateway:
`status`, `token`, `final`, `error`.

What the loop decides in code, not by asking the model:
* a request to rank or compare learners, or to suggest a pathway, is refused before the model is
  called (guards.refusal_for);
* only allow-listed tools are offered and only those can run (tools.bind);
* `release_report` is held back unless the approver asked for it in their latest message and the
  draft was not written in this same run (guards.release_blocked);
* the approver's token is removed from anything bound for the model (guards.scrub).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import chart_reply, guards, tools
from .model import ModelError

PROMPT_FILE = Path(__file__).parent / "prompts" / "system.md"
GRAPH_NOTE = Path(__file__).parent / "prompts" / "graph_note.md"
CHUNK_WORDS = 8  # words to a `token` event
KEPT_ROLES = ("user", "assistant")  # the service writes its own system prompt


def system_prompt(graph: bool = False) -> str:
    """Our system prompt. In graph mode a short note says which steps the system does itself."""
    text = PROMPT_FILE.read_text(encoding="utf-8")
    return text + GRAPH_NOTE.read_text(encoding="utf-8") if graph else text


def event(name: str, **data) -> dict:
    return {"event": name, "data": data}


def conversation_from(messages: list, token: str | None) -> list:
    """The model's view of the conversation: user and assistant text, the token taken out."""
    kept = [m for m in messages if m["role"] in KEPT_ROLES]
    return [
        {"role": m["role"], "content": chart_reply.strip_images(guards.scrub(m["content"], token))}
        for m in kept
    ]


def latest_user_text(messages: list) -> str:
    users = [m["content"] for m in messages if m["role"] == "user"]
    return users[-1] if users else ""


def chunks(text: str) -> list:
    pieces = re.findall(r"\S+\s*", text)
    return ["".join(pieces[k : k + CHUNK_WORDS]) for k in range(0, len(pieces), CHUNK_WORDS)]


def final_events(text: str, called: list, run_id: str, **extra):
    for piece in chunks(text):
        yield event("token", text=piece)
    yield event("final", answer=text, tools=called, run_id=run_id, **extra)


async def execute(
    call, owner: dict, servers: dict, last_user: str, drafted: set, releasable: set | None = None
) -> dict:
    """Run one tool call the model asked for, or say why it did not run. Never raises.

    `releasable`, when given, is the set of report numbers that may be released now.
    """
    try:
        arguments = json.loads(call.arguments or "{}")
        if not isinstance(arguments, dict):
            raise ValueError
    except ValueError:
        return {"status": "error", "detail": "the tool arguments were not a JSON object"}
    if call.name not in owner:
        return {"status": "refused", "reason": "that tool is not available"}
    if call.name == "release_report":
        why = guards.release_blocked(arguments, last_user, drafted)
        if not why and releasable is not None and arguments.get("report_id") not in releasable:
            why = "no draft with that number has passed the check and been shown to the approver"
        if why:
            return {"status": "refused", "reason": why}
    try:
        result = await servers[owner[call.name]].call(call.name, arguments)
    except Exception as error:  # (the class only: never the message, which could hold a record)
        return {"status": "error", "detail": f"the tool failed ({type(error).__name__})"}
    if call.name == "draft_summary" and isinstance(result, dict) and result.get("report_id"):
        drafted.add(str(result["report_id"]))
    return result


async def run(*, messages: list, token: str | None, run_id: str, settings, model, servers: dict):
    """The events of one run, in order."""
    yield event("status", text="Reading your question.")
    last_user = latest_user_text(messages)
    reason = guards.refusal_for(last_user)
    if reason:
        for item in final_events(reason, [], run_id, refused=True):
            yield item
        return
    listed = {name: await server.list_tools() for name, server in servers.items()}
    definitions, owner, _dropped = tools.bind(listed, settings.pg_tools, settings.class_view)
    conversation = [{"role": "system", "content": system_prompt()}]
    conversation += conversation_from(messages, token)
    called, drafted, charts, sources, rules, assumed = [], set(), [], [], [], []
    for step in range(settings.max_steps):
        yield event("status", text="Thinking." if not step else "Working on it.")
        try:
            reply = await model.complete(conversation, definitions)
        except ModelError as error:
            yield event("error", text=str(error))
            return
        if not reply.calls:
            words = guards.scrub(reply.text, token)
            answer = chart_reply.compose(words, charts, sources, rules=rules, assumed=assumed)
            for item in final_events(answer, called, run_id):
                yield item
            return
        conversation.append(
            {
                "role": "assistant",
                "content": reply.text or None,
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": c.arguments},
                    }
                    for c in reply.calls
                ],
            }
        )
        for call in reply.calls:
            result = await execute(call, owner, servers, last_user, drafted)
            called.append(call.name)
            if chart_reply.is_chart(call.name, result):
                charts.append(result)
            sources = chart_reply.add_sources(sources, result)
            rules = chart_reply.add_rules(rules, result)
            assumed = chart_reply.add_assumed(assumed, result)
            content = guards.scrub(json.dumps(result, default=str), token)
            conversation.append({"role": "tool", "tool_call_id": call.id, "content": content})
            yield event("status", text=f"Used {call.name}.")
    note = (
        "I reached my step limit before I finished. "
        "Ask me to carry on, or ask a narrower question."
    )
    for item in final_events(
        chart_reply.compose(note, charts, sources, rules=rules, assumed=assumed),
        called,
        run_id,
        incomplete=True,
    ):
        yield item
