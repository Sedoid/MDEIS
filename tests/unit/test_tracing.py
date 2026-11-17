"""Optional Langfuse tracing in the orchestrator (T-067). Written before the code, then rewritten
for OpenTelemetry (OTLP over HTTP with a JSON body) when Langfuse Cloud shut its legacy ingestion
API on 2026-11-16.

There is no network and no OpenTelemetry or Langfuse package: spans are built by hand and sent to
`/api/public/otel/v1/traces` with httpx, and every test puts `httpx.MockTransport` where the network
would be. Expected values are typed by hand: the sample OpenRouter answer below is made up
(plausible fields, invented numbers).

What is held here: off unless all three variables are set (and then no client is even made); the
model request asks OpenRouter for usage and keeps the provider block; the OTLP payload has valid
ids, parent links, nanosecond timestamps and only the documented attribute names; one request goes
for a whole run; tokens and the USD cost come from the answer and are never computed; the default
carries no prompt, reply, tool argument or tool result; with content capture on, a lesson's notes
and objective and a learner's name still never leave; the user is an id, never a name; the trace id
comes from the run id; the keys are used for basic auth only and are in no body and no log line; a
transport that fails, times out, answers 401 or 400, or hangs changes nothing about the run; the
queue is bounded; the shutdown flush sends each queued run once; and the chat loop and the lesson
graph give the same answers with the tracer on and off.
"""

import asyncio
import base64
import hashlib
import json
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from lesson_graph_fakes import LessonTools
from lessonkit import A_ASK, FINDING_ID, LESSON, OBJECTIVE
from orchestrator_fakes import MDEIS_LISTING, PG_LISTING, FakeModel, FakeServer, ask, say

from mdeis.core import approver_token
from orchestrator import loop, tools, tracing
from orchestrator.config import load_settings
from orchestrator.lessons import graph as lesson_graph
from orchestrator.lessons import prompt as lesson_prompt
from orchestrator.model import ModelError, OpenRouterModel, Reply, payload, reply_of

ROOT = Path(__file__).resolve().parents[2]
PK, SK = "test-public-key-0001", "test-secret-key-0002"  # made up; not a real key
HOST = "https://lf.example.test"
BASE = {
    "ORCHESTRATOR_API_KEY": "k",
    "OPENROUTER_API_KEY": "or-key",
    "OPENROUTER_MODEL": "open/weights-model",
    "LANGFUSE_PUBLIC_KEY": PK,
    "LANGFUSE_SECRET_KEY": SK,
    "LANGFUSE_HOST": HOST,
}
TOKEN = approver_token.sign("test-secret-not-real", "u-12", "Ada Teacher", school="TST")
NEW_ASK = "Ask what the passage tells us and why."
GOOD = json.dumps({"edits": [{
    "id": "e1", "type": "reword", "anchor_quote": A_ASK, "replacement": NEW_ASK,
    "finding_ids": [FINDING_ID], "resource_ids": [],
    "suggestion": "Give learners a question that asks for a reason.",
}]})
# A made-up OpenRouter answer: the usage block with `include` set carries the cost in USD.
SAMPLE = {
    "id": "gen-test-1",
    "provider": "Alpha",
    "model": "open/weights-model",
    "choices": [{"message": {"role": "assistant", "content": "The records show a steady trend."}}],
    "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150, "cost": 0.000345},
}
# Every attribute name the code may send: the ones Langfuse documents for OpenTelemetry.
DOCUMENTED = {
    "langfuse.trace.name", "langfuse.trace.tags", "user.id", "session.id", "mdeis.run_id",
    "langfuse.observation.type", "langfuse.observation.level",
    "langfuse.observation.status_message", "langfuse.observation.model.name",
    "gen_ai.request.model", "langfuse.observation.usage_details",
    "langfuse.observation.cost_details", "langfuse.observation.input",
    "langfuse.observation.output",
}
DOCUMENTED_PREFIXES = ("langfuse.observation.metadata.", "langfuse.trace.metadata.")
GEN = "langfuse.observation.type"


def cfg(**over):
    return load_settings({**BASE, **over})


SETTINGS = cfg()


def tid(run_id):
    """The trace id of a run id that is not `RUN-<uuid>`: 32 hex characters of its sha256."""
    return hashlib.sha256(run_id.encode()).hexdigest()[:32]


def value_of(value):
    """An OTLP/JSON attribute value as a Python value. An int64 must be a JSON string."""
    if "stringValue" in value:
        return value["stringValue"]
    if "intValue" in value:
        assert isinstance(value["intValue"], str), "an int64 is a JSON string in OTLP/JSON"
        return int(value["intValue"])
    if "doubleValue" in value:
        return value["doubleValue"]
    if "boolValue" in value:
        return value["boolValue"]
    return [v["stringValue"] for v in value["arrayValue"]["values"]]


def attrs(span):
    return {a["key"]: value_of(a["value"]) for a in span["attributes"]}


class Wire:
    """A stand-in for Langfuse. `answer` and `status` are what it says; `error` is what the
    transport raises; `hang` never answers."""

    def __init__(self, status=200, answer=None, error=None, hang=False):
        self.status, self.answer, self.error, self.hang = status, answer, error, hang
        self.requests = []

    async def handler(self, request):
        self.requests.append(request)
        if self.hang:
            await asyncio.sleep(3600)
        if self.error:
            raise self.error
        return httpx.Response(self.status, json={} if self.answer is None else self.answer)

    def client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def spans(self):
        found = []
        for r in self.requests:
            for resource in json.loads(r.content)["resourceSpans"]:
                for scope in resource["scopeSpans"]:
                    found += scope["spans"]
        return found

    def roots(self):
        return [s for s in self.spans() if "parentSpanId" not in s]

    def generations(self):
        return [s for s in self.spans() if attrs(s).get(GEN) == "generation"]

    def tool_spans(self):
        found = [
            s for s in self.spans() if "parentSpanId" in s and attrs(s).get(GEN) == "span"
        ]
        return sorted(found, key=lambda s: int(s["startTimeUnixNano"]))

    def text(self):
        return "\n".join(r.content.decode() for r in self.requests)


def tracer_for(wire, **env):
    return tracing.Tracer(cfg(**env), client=wire.client())


def chat(tracer, model, *, results=None, pg=None, text="How is the reading going?",
         run_id="RUN-C1", token=TOKEN, conv="conv-1"):
    async def go():
        servers = {tools.MDEIS: FakeServer(MDEIS_LISTING, results or {})}
        if pg is not None:
            servers[tools.PG] = pg
        trace = tracer.start(run_id=run_id, kind="chat", token=token, conversation_id=conv)
        out = [
            e
            async for e in loop.run(
                messages=[{"role": "user", "content": text}], token=token, run_id=run_id,
                settings=SETTINGS, model=trace.wrap_model(model),
                servers=trace.wrap_servers(servers),
            )
        ]
        trace.finish("ok")
        await tracer.flush()
        return out

    return asyncio.run(go())


def lesson(tracer, model, server=None, run_id="RUN-L1"):
    async def go():
        trace = tracer.start(run_id=run_id, kind="lesson", token=TOKEN, mask=(LESSON, OBJECTIVE))
        answer = await lesson_graph.run(
            stage="Form 3", area="literacy", term=2, objective=OBJECTIVE, notes=LESSON,
            run_id=run_id, settings=SETTINGS, model=trace.wrap_model(model),
            servers=trace.wrap_servers({tools.MDEIS: server or LessonTools()}), engine="plain",
        )
        trace.finish(
            answer["outcome"], repair_rounds=answer["repair_rounds"],
            model_calls=answer["model_calls"],
        )
        await tracer.flush()
        return answer

    return asyncio.run(go())


def script():
    return FakeModel(ask("get_criterion", {"query": "TR-1"}), say("Reading is steady."))


# --------------------------------------------------------------------------- off by default
@pytest.mark.parametrize("missing", ["LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"])
@pytest.mark.parametrize("value", ["", "   "])
def test_it_is_off_unless_all_three_are_set_and_then_nothing_touches_the_network(
    monkeypatch, missing, value
):
    def no_network(*args, **kwargs):
        raise AssertionError("a network client was made while tracing is off")

    monkeypatch.setattr(httpx, "AsyncClient", no_network)
    monkeypatch.setattr(httpx, "Client", no_network)
    tracer = tracing.tracer_for(cfg(**{missing: value}))
    assert isinstance(tracer, tracing.NullTracer) and tracer.enabled is False
    model, servers = object(), {}
    trace = tracer.start(run_id="RUN-1", kind="chat", token=TOKEN, conversation_id="c")
    assert trace.wrap_model(model) is model and trace.wrap_servers(servers) is servers
    trace.finish("ok")
    asyncio.run(tracer.flush())


def test_nothing_set_is_off_and_all_three_set_is_on():
    assert not tracing.tracer_for(load_settings({})).enabled
    assert tracing.tracer_for(SETTINGS).enabled


def test_the_settings_read_the_variables_and_never_print_the_keys():
    s = cfg(LANGFUSE_HOST=HOST + "/", LANGFUSE_CAPTURE_CONTENT="true")
    assert (s.langfuse_public_key, s.langfuse_secret_key) == (PK, SK)
    assert s.langfuse_host == HOST and s.langfuse_capture_content is True
    assert cfg().langfuse_capture_content is False, "content capture is off unless asked for"
    assert PK not in repr(s) and SK not in repr(s)


# --------------------------------------------------------------------------- the model request
def test_the_request_asks_for_usage_and_the_provider_block_is_unchanged():
    body = payload(SETTINGS, [{"role": "user", "content": "x"}], [])
    assert body["usage"] == {"include": True}
    assert body["provider"] == {"allow_fallbacks": False, "data_collection": "deny"}
    ordered = payload(cfg(OPENROUTER_PROVIDER="Alpha, Beta"), [], [])
    assert ordered["provider"]["order"] == ["Alpha", "Beta"]
    assert ordered["provider"]["allow_fallbacks"] is False


def test_usage_and_cost_are_read_from_the_answer_and_nothing_is_worked_out():
    reply = reply_of(SAMPLE)
    assert reply.text == "The records show a steady trend." and reply.calls == ()
    assert reply.usage == {
        "prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150, "cost": 0.000345,
    }
    assert reply.provider == "Alpha"
    no_cost = {**SAMPLE, "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}}
    assert "cost" not in reply_of(no_cost).usage
    assert reply_of({"choices": SAMPLE["choices"]}).usage is None
    odd = {**SAMPLE, "usage": {"prompt_tokens": "lots", "cost": True, "total_tokens": -4}}
    assert reply_of(odd).usage is None, "a value that is not a plain non-negative number is dropped"


def test_the_real_client_sends_the_usage_switch_and_returns_the_usage():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=SAMPLE)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    reply = asyncio.run(OpenRouterModel(SETTINGS, client=client).complete([], []))
    assert seen[0]["usage"] == {"include": True}
    assert seen[0]["provider"]["data_collection"] == "deny"
    assert reply.usage["cost"] == 0.000345


# --------------------------------------------------------------------------- the OTLP payload
def test_the_request_is_a_basic_auth_post_to_the_otel_route_with_the_ingestion_header():
    wire = Wire()
    chat(tracer_for(wire), script())
    request = wire.requests[0]
    expected = "Basic " + base64.b64encode(f"{PK}:{SK}".encode()).decode()
    assert request.method == "POST"
    assert str(request.url) == HOST + "/api/public/otel/v1/traces"
    assert request.headers["authorization"] == expected
    assert request.headers["content-type"].startswith("application/json")
    assert request.headers["x-langfuse-ingestion-version"] == "4"


def test_a_whole_run_goes_in_one_request_with_a_root_and_one_child_for_each_call():
    wire = Wire()
    chat(tracer_for(wire), script())
    assert len(wire.requests) == 1, "the spans of a run are sent together, at the end"
    body = json.loads(wire.requests[0].content)
    [resource] = body["resourceSpans"]
    assert {"key": "service.name", "value": {"stringValue": "mdeis-orchestrator"}} in (
        resource["resource"]["attributes"]
    )
    [scope] = resource["scopeSpans"]
    assert scope["scope"]["name"] == "mdeis"
    spans = scope["spans"]
    assert [s["name"] for s in sorted(spans, key=lambda s: int(s["startTimeUnixNano"]))] == [
        "mdeis-chat", "chat-model-call", "get_criterion", "chat-model-call",
    ]
    [root] = wire.roots()
    assert root["name"] == "mdeis-chat"
    assert all(s["parentSpanId"] == root["spanId"] for s in spans if s["spanId"] != root["spanId"])


def test_ids_times_kind_and_status_follow_the_otlp_json_form():
    wire = Wire()
    chat(tracer_for(wire), script(), run_id="RUN-C1")
    spans = wire.spans()
    assert len({s["spanId"] for s in spans}) == len(spans)
    root = wire.roots()[0]
    for s in spans:
        assert re.fullmatch(r"[0-9a-f]{32}", s["traceId"]) and s["traceId"] == tid("RUN-C1")
        assert re.fullmatch(r"[0-9a-f]{16}", s["spanId"]) and set(s["spanId"]) != {"0"}
        if "parentSpanId" in s:
            assert re.fullmatch(r"[0-9a-f]{16}", s["parentSpanId"])
        assert re.fullmatch(r"[1-9][0-9]+", s["startTimeUnixNano"]), "integer nanoseconds, a string"
        assert re.fullmatch(r"[1-9][0-9]+", s["endTimeUnixNano"])
        assert int(s["startTimeUnixNano"]) <= int(s["endTimeUnixNano"])
        assert s["kind"] == 1 and s["status"] == {"code": 1}
        assert int(root["startTimeUnixNano"]) <= int(s["startTimeUnixNano"])
        assert int(root["endTimeUnixNano"]) >= int(s["endTimeUnixNano"])


def test_a_run_id_made_from_a_uuid_gives_that_uuids_hex_as_the_trace_id():
    wire = Wire()
    chat(tracer_for(wire), script(), run_id="RUN-12345678-1234-5678-1234-567812345678")
    assert {s["traceId"] for s in wire.spans()} == {"12345678123456781234567812345678"}
    for s in wire.spans():
        assert attrs(s)["mdeis.run_id"] == "RUN-12345678-1234-5678-1234-567812345678"
    assert tracing.trace_id_for("RUN-ABCDEF12-1234-5678-1234-567812345678") == (
        "abcdef12123456781234567812345678"
    ), "lower case"


def test_only_documented_attribute_names_are_sent_and_the_trace_ones_are_on_every_span():
    wire = Wire()
    chat(tracer_for(wire, LANGFUSE_CAPTURE_CONTENT="true"), script())
    for s in wire.spans():
        mine = attrs(s)
        for key in mine:
            assert key in DOCUMENTED or key.startswith(DOCUMENTED_PREFIXES), key
        assert mine["langfuse.trace.name"] == "mdeis-chat"
        assert mine["langfuse.trace.tags"] == ["chat"]
        assert mine["user.id"] == "u-12" and mine["session.id"] == "conv-1"
        assert mine["langfuse.trace.metadata.run_id"] == "RUN-C1"
        assert mine["langfuse.trace.metadata.outcome"] == "ok"
        assert mine[GEN] in ("span", "generation")


def test_the_root_span_carries_the_run_outcome_and_a_failed_run_is_an_error_span():
    wire = Wire()
    tracer = tracer_for(wire)
    trace = tracer.start(run_id="RUN-E1", kind="chat", token=TOKEN, conversation_id="c")
    trace.finish("error")
    asyncio.run(tracer.flush())
    [root] = wire.roots()
    mine = attrs(root)
    assert mine["langfuse.observation.level"] == "ERROR" and root["status"] == {"code": 2}
    assert mine["langfuse.observation.metadata.outcome"] == "error"


# --------------------------------------------------------------------------- what a span holds
def real_model(answer=SAMPLE):
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=answer))
    return OpenRouterModel(SETTINGS, client=httpx.AsyncClient(transport=transport))


def test_a_generation_carries_tokens_cost_model_provider_and_time():
    wire = Wire()
    chat(tracer_for(wire), real_model())
    [gen] = wire.generations()
    mine = attrs(gen)
    assert gen["parentSpanId"] == wire.roots()[0]["spanId"]
    assert json.loads(mine["langfuse.observation.usage_details"]) == {
        "input": 120, "output": 30, "total": 150,
    }
    assert json.loads(mine["langfuse.observation.cost_details"]) == {"total": 0.000345}
    assert mine["langfuse.observation.model.name"] == "open/weights-model"
    assert mine["gen_ai.request.model"] == "open/weights-model"
    assert mine["langfuse.observation.metadata.provider"] == "Alpha"
    assert mine["langfuse.observation.metadata.graph_kind"] == "chat"
    assert mine["langfuse.observation.metadata.latency_ms"] >= 0
    assert mine["langfuse.observation.level"] == "DEFAULT"
    spent = int(gen["endTimeUnixNano"]) - int(gen["startTimeUnixNano"])
    assert mine["langfuse.observation.metadata.latency_ms"] == spent // 1_000_000


def test_with_no_cost_in_the_answer_tokens_are_sent_and_no_cost():
    wire = Wire()
    answer = {**SAMPLE, "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}}
    chat(tracer_for(wire), real_model(answer))
    mine = attrs(wire.generations()[0])
    assert json.loads(mine["langfuse.observation.usage_details"]) == {
        "input": 5, "output": 2, "total": 7,
    }
    assert "langfuse.observation.cost_details" not in mine


def test_a_reply_with_unusable_usage_still_traces_without_numbers():
    wire = Wire()
    bad = Reply("fine", usage={"prompt_tokens": "x", "cost": True})
    chat(tracer_for(wire), FakeModel(bad))
    mine = attrs(wire.generations()[0])
    assert "langfuse.observation.usage_details" not in mine
    assert "langfuse.observation.cost_details" not in mine


def test_a_failed_model_call_is_an_error_generation_and_the_error_still_reaches_the_run():
    wire = Wire()
    failure = ModelError("the model service answered with an error (500)")
    out = chat(tracer_for(wire), FakeModel(failure))
    assert out[-1]["event"] == "error"
    [gen] = wire.generations()
    mine = attrs(gen)
    assert mine["langfuse.observation.level"] == "ERROR" and gen["status"] == {"code": 2}
    assert "500" in mine["langfuse.observation.status_message"]


def test_tool_calls_are_spans_with_the_tool_name_and_the_outcome_only():
    wire = Wire()
    results = {"get_criterion": {"status": "refused", "reason": "not allowed"}}
    chat(tracer_for(wire), script(), results=results)
    [span] = wire.tool_spans()
    mine = attrs(span)
    assert span["name"] == "get_criterion"
    assert mine["langfuse.observation.metadata.outcome"] == "refused"
    assert mine["langfuse.observation.metadata.server"] == "mdeis"
    assert mine["langfuse.observation.level"] == "WARNING" and span["status"] == {"code": 1}
    assert "TR-1" not in wire.text() and "not allowed" not in wire.text()


def test_a_tool_that_raises_is_an_error_span_and_the_error_still_reaches_the_caller():
    class Broken:
        async def call(self, name, arguments):
            raise RuntimeError("SECRET-DETAIL")

    wire = Wire()

    async def go():
        tracer = tracer_for(wire)
        trace = tracer.start(run_id="RUN-T1", kind="chat")
        server = trace.wrap_servers({"mdeis": Broken()})["mdeis"]
        with pytest.raises(RuntimeError):
            await server.call("compute_trend", {})
        trace.finish("error")
        await tracer.flush()

    asyncio.run(go())
    [span] = wire.tool_spans()
    assert span["status"] == {"code": 2} and "SECRET-DETAIL" not in wire.text()


def test_the_default_carries_no_prompt_reply_tool_argument_or_tool_result():
    wire = Wire()
    model = FakeModel(
        ask("get_criterion", {"query": "SECRET-ARG"}), say("SECRET-REPLY about reading.")
    )
    results = {"get_criterion": {"text": "SECRET-RESULT"}}
    chat(tracer_for(wire), model, results=results, text="SECRET-PROMPT please")
    text = wire.text()
    for secret in ("SECRET-ARG", "SECRET-REPLY", "SECRET-RESULT", "SECRET-PROMPT"):
        assert secret not in text
    for span in wire.spans():
        mine = attrs(span)
        assert "langfuse.observation.input" not in mine
        assert "langfuse.observation.output" not in mine
    assert [s["name"] for s in wire.tool_spans()] == ["get_criterion"]


def test_with_capture_on_the_text_is_sent_but_a_learner_name_from_a_lookup_never_is():
    wire = Wire()
    pg = FakeServer(
        PG_LISTING,
        {"execute_sql": {"rows": [{"display_name": "Amara Okafor", "school_ref": "L-0001"}]}},
    )
    model = FakeModel(
        ask("execute_sql", {"sql": "select display_name from learners"}),
        say("Amara Okafor is reading in the Form 1 range."),
    )
    chat(tracer_for(wire, LANGFUSE_CAPTURE_CONTENT="true"), model, pg=pg,
         text="Tell me about the reading")
    first, second = sorted(wire.generations(), key=lambda s: int(s["startTimeUnixNano"]))
    first, second = attrs(first), attrs(second)
    shown_in = json.loads(second["langfuse.observation.input"])
    shown_out = json.loads(second["langfuse.observation.output"])
    assert any("Tell me about the reading" in m["content"] for m in shown_in)
    assert "reading in the Form 1 range" in shown_out["text"], "capture is really on"
    assert json.loads(first["langfuse.observation.output"])["tool_calls"] == ["execute_sql"]
    tool_messages = [m for m in shown_in if m["role"] == "tool"]
    assert tool_messages and all(m["content"] == tracing.WITHHELD for m in tool_messages)
    text = wire.text()
    for part in ("Amara", "Okafor", "select display_name"):
        assert part not in text


def test_with_capture_on_a_lessons_notes_and_objective_are_never_sent():
    wire = Wire()
    answer = lesson(tracer_for(wire, LANGFUSE_CAPTURE_CONTENT="true"), FakeModel(say(GOOD)))
    assert answer["outcome"] == "stored"
    text = wire.text()
    for fragment in (A_ASK, NEW_ASK, OBJECTIVE, "underline the key sentence", "Give learners a"):
        assert fragment not in text
    [gen] = wire.generations()
    mine = attrs(gen)
    system, user = json.loads(mine["langfuse.observation.input"])
    assert system["content"] == lesson_prompt.system_prompt(), "capture is really on"
    assert user["content"] == tracing.WITHHELD
    assert json.loads(mine["langfuse.observation.output"])["text"] == tracing.WITHHELD


def test_a_lesson_trace_has_the_graph_tools_the_outcome_and_the_repair_count():
    wire = Wire()
    lesson(tracer_for(wire), FakeModel(say(GOOD)))
    assert [s["name"] for s in wire.tool_spans()] == [
        "class_baseline", "guard_lesson_notes", "save_lesson_plan",
    ]
    [root] = wire.roots()
    mine = attrs(root)
    assert root["name"] == "mdeis-lesson"
    assert mine["langfuse.trace.metadata.graph_kind"] == "lesson"
    assert mine["langfuse.trace.metadata.outcome"] == "stored"
    assert mine["langfuse.trace.metadata.repair_rounds"] == 0
    assert mine["langfuse.trace.metadata.model_calls"] == 1
    assert "session.id" not in mine, "a lesson has no conversation"
    for fragment in (A_ASK, NEW_ASK, OBJECTIVE):
        assert fragment not in wire.text()


# --------------------------------------------------------------------------- who and which run
def test_the_user_is_the_id_the_session_is_the_conversation_and_the_trace_is_the_run():
    wire = Wire()
    chat(tracer_for(wire), script(), run_id="RUN-ABC", conv="conv-9")
    for s in wire.spans():
        mine = attrs(s)
        assert s["traceId"] == tid("RUN-ABC") and mine["mdeis.run_id"] == "RUN-ABC"
        assert mine["user.id"] == "u-12" and mine["session.id"] == "conv-9"
    assert "Ada" not in wire.text() and "Teacher" not in wire.text()


@pytest.mark.parametrize("token", [None, "", "signed.token", "no-dot", "a.b.c"])
def test_a_token_that_cannot_be_read_gives_no_user_and_never_breaks_the_run(token):
    wire = Wire()
    chat(tracer_for(wire), script(), token=token)
    assert all("user.id" not in attrs(s) for s in wire.spans()) and wire.spans()


# --------------------------------------------------------------------------- the keys
def test_the_keys_are_in_no_body_and_no_log_line(caplog):
    caplog.set_level(logging.DEBUG)
    ok, bad = Wire(), Wire(status=401, answer={"message": "Invalid credentials"})
    chat(tracer_for(ok, LANGFUSE_CAPTURE_CONTENT="true"), script())
    chat(tracer_for(bad), script())
    for wire in (ok, bad):
        assert PK not in wire.text() and SK not in wire.text()
    assert PK not in caplog.text and SK not in caplog.text
    assert "Invalid credentials" not in caplog.text, "a server's message is not logged"


def test_a_key_that_leaks_into_a_captured_text_is_masked_before_it_is_sent():
    wire = Wire()
    model = FakeModel(say(f"the key is {SK}"))
    chat(tracer_for(wire, LANGFUSE_CAPTURE_CONTENT="true"), model, text=f"here is {PK}")
    assert PK not in wire.text() and SK not in wire.text()


# --------------------------------------------------------------------------- never break a run
def plain_run(model_script):
    return chat(tracing.NullTracer(), model_script())


@pytest.mark.parametrize(
    "wire_args",
    [
        {"error": httpx.ConnectError("refused")},
        {"error": httpx.ReadTimeout("too slow")},
        {"status": 401, "answer": {"message": "Invalid credentials"}},
        {"status": 400, "answer": {"message": "bad request"}},
        {"status": 500, "answer": {"message": "oops"}},
    ],
    ids=["connect-error", "timeout", "401", "400", "500"],
)
def test_a_failing_transport_changes_nothing_and_the_failures_are_counted(wire_args):
    wire = Wire(**wire_args)
    tracer = tracer_for(wire)
    assert chat(tracer, script()) == plain_run(script)
    assert tracer.stats["sent"] == 0
    assert tracer.stats["failed"] == tracer.stats["queued"] == 4  # the root and three children


def test_a_partial_success_is_counted_by_span_and_the_reason_is_not_quoted(caplog):
    caplog.set_level(logging.DEBUG)
    answer = {"partialSuccess": {"rejectedSpans": 2, "errorMessage": "SECRET-BAD-FIELD"}}
    wire = Wire(answer=answer)
    tracer = tracer_for(wire)
    tracer.submit([{"traceId": tid("x"), "spanId": f"{k:016x}"} for k in range(3)])
    asyncio.run(tracer.flush())
    assert len(wire.requests) == 1
    assert tracer.stats["rejected"] == 2 and tracer.stats["sent"] == 1
    assert "SECRET-BAD-FIELD" not in caplog.text


def test_a_success_answer_in_either_form_counts_every_span_as_sent():
    for answer in ({}, {"partialSuccess": {}}):
        wire = Wire(answer=answer)
        tracer = tracer_for(wire)
        chat(tracer, script())
        assert tracer.stats["sent"] == 4 and tracer.stats["failed"] == 0


def test_a_slow_network_adds_no_wait_to_the_run_and_the_flush_is_bounded(monkeypatch):
    monkeypatch.setattr(tracing, "FLUSH_TIMEOUT_SECONDS", 0.05)
    wire = Wire(hang=True)
    tracer = tracer_for(wire)

    async def go():
        trace = tracer.start(run_id="RUN-S1", kind="chat", token=TOKEN, conversation_id="c")
        began = time.monotonic()
        out = [
            e
            async for e in loop.run(
                messages=[{"role": "user", "content": "hello"}], token=TOKEN, run_id="RUN-S1",
                settings=SETTINGS, model=trace.wrap_model(script()),
                servers=trace.wrap_servers({tools.MDEIS: FakeServer(MDEIS_LISTING)}),
            )
        ]
        trace.finish("ok")
        run_seconds = time.monotonic() - began
        await tracer.flush()
        return out, run_seconds, time.monotonic() - began

    out, run_seconds, total = asyncio.run(asyncio.wait_for(go(), 5))
    assert out[-1]["event"] == "final"
    assert run_seconds < 1 and total < 2
    assert tracer.stats["errors"] >= 1


def test_a_tracer_that_raises_inside_does_not_touch_the_run(monkeypatch):
    wire = Wire()
    tracer = tracer_for(wire)

    def boom(*args, **kwargs):
        raise RuntimeError("tracer bug")

    monkeypatch.setattr(tracer, "submit", boom)
    assert chat(tracer, script()) == plain_run(script)
    assert tracer.stats["errors"] > 0 and wire.requests == []


def test_a_span_that_cannot_be_built_is_counted_and_the_run_is_unchanged(monkeypatch):
    wire = Wire()
    tracer = tracer_for(wire)

    def boom(*args, **kwargs):
        raise RuntimeError("builder bug")

    monkeypatch.setattr(tracing.Trace, "_span", boom)
    assert chat(tracer, script()) == plain_run(script)
    assert tracer.stats["errors"] > 0


def test_the_queue_is_bounded_and_the_overflow_is_counted_in_spans():
    tracer = tracing.Tracer(SETTINGS, client=Wire().client())
    for k in range(tracing.QUEUE_MAX + 5):
        tracer.submit([{"spanId": f"{k:016x}"}, {"spanId": f"{k:016x}"}])
    assert len(tracer._queue) == tracing.QUEUE_MAX
    assert tracer.stats["dropped"] == 10, "five runs of two spans"


def test_the_flush_on_shutdown_sends_each_queued_run_once():
    wire = Wire()
    tracer = tracer_for(wire)
    trace = tracer.start(run_id="RUN-F1", kind="chat", token=TOKEN, conversation_id="c")
    trace.finish("ok")  # the run is queued; no loop is running, so nothing was sent
    assert wire.requests == []
    asyncio.run(tracer.flush())
    assert len(wire.requests) == 1 and len(wire.spans()) == 1
    asyncio.run(tracer.flush())
    assert len(wire.requests) == 1, "a second flush has nothing left to send"
    assert tracer.stats["sent"] == 1


def test_a_flush_right_after_several_runs_sends_one_request_each_and_nothing_twice():
    wire = Wire()
    tracer = tracer_for(wire)

    async def go():
        for k in range(3):
            trace = tracer.start(run_id=f"RUN-M{k}", kind="chat", token=TOKEN)
            trace.finish("ok")
        await tracer.flush()

    asyncio.run(go())
    assert len(wire.requests) == 3
    spans = wire.spans()
    assert len({s["spanId"] for s in spans}) == 3 == len({s["traceId"] for s in spans})


def test_finishing_a_run_twice_sends_it_once():
    wire = Wire()
    tracer = tracer_for(wire)
    trace = tracer.start(run_id="RUN-D1", kind="chat", token=TOKEN)
    trace.finish("ok")
    trace.finish("error")
    asyncio.run(tracer.flush())
    assert len(wire.requests) == 1 and attrs(wire.roots()[0])[
        "langfuse.trace.metadata.outcome"
    ] == "ok"


# --------------------------------------------------------------------------- the graphs, on and off
def test_the_chat_loop_gives_the_same_events_with_the_tracer_on_and_off():
    results = {"get_criterion": {"status": "ok", "text": "a rule"}}
    off = chat(tracing.NullTracer(), script(), results=results)
    on = chat(tracer_for(Wire()), script(), results=results)
    assert on == off and off[-1]["event"] == "final"


def test_the_lesson_graph_gives_the_same_answer_with_the_tracer_on_and_off():
    off = lesson(tracing.NullTracer(), FakeModel(say(GOOD)))
    on = lesson(tracer_for(Wire()), FakeModel(say(GOOD)))
    assert on == off and off["outcome"] == "stored"
    broken = lesson(tracer_for(Wire(error=httpx.ConnectError("down"))), FakeModel(say(GOOD)))
    assert broken == off


def test_a_lesson_repair_round_is_counted_in_the_trace():
    wire = Wire()
    answer = lesson(tracer_for(wire), FakeModel(say("not json"), say(GOOD)))
    assert answer["repair_rounds"] == 1
    mine = attrs(wire.roots()[0])
    assert mine["langfuse.trace.metadata.repair_rounds"] == 1
    assert mine["langfuse.trace.metadata.model_calls"] == 2
    assert len(wire.generations()) == 2


# --------------------------------------------------------------------------- what the log says
LINE = re.compile(
    r"^langfuse: run (sent, spans=\d+, http=\d+, rejected=\d+|not sent, spans=\d+, reason=\w+)$"
)
SIX = {"on", "runs_sent", "runs_failed", "runs_dropped", "spans_rejected", "last_status"}


def tracer_lines(caplog):
    return [r for r in caplog.records if r.name.startswith("orchestrator.tracing")]


def test_a_sent_run_logs_one_info_line_with_counts_and_status_only(caplog):
    caplog.set_level(logging.DEBUG)
    chat(tracer_for(Wire()), script())
    [line] = tracer_lines(caplog)
    assert line.levelno == logging.INFO
    assert line.getMessage() == "langfuse: run sent, spans=4, http=200, rejected=0"


@pytest.mark.parametrize("status", [400, 401, 500])
def test_a_refused_run_logs_the_status_number_and_nothing_the_server_said(caplog, status):
    caplog.set_level(logging.DEBUG)
    chat(tracer_for(Wire(status=status, answer={"message": "SENTINEL-BODY-TEXT"})), script())
    [line] = tracer_lines(caplog)
    assert line.levelno == logging.WARNING
    assert line.getMessage() == f"langfuse: run not sent, spans=4, reason={status}"


def test_a_timeout_logs_the_exception_class_name_only(caplog):
    caplog.set_level(logging.DEBUG)
    chat(tracer_for(Wire(error=httpx.ReadTimeout("SENTINEL-BODY-TEXT"))), script())
    [line] = tracer_lines(caplog)
    assert line.levelno == logging.WARNING
    assert line.getMessage() == "langfuse: run not sent, spans=4, reason=ReadTimeout"


def test_a_partial_rejection_is_one_warning_with_the_count_and_no_reason(caplog):
    caplog.set_level(logging.DEBUG)
    answer = {"partialSuccess": {"rejectedSpans": 2, "errorMessage": "SENTINEL-BODY-TEXT"}}
    chat(tracer_for(Wire(answer=answer)), script())
    [line] = tracer_lines(caplog)
    assert line.levelno == logging.WARNING
    assert line.getMessage() == "langfuse: run sent, spans=4, http=200, rejected=2"


def test_no_log_line_holds_a_key_a_body_or_any_text_of_the_run(caplog):
    caplog.set_level(logging.DEBUG)
    answers = [
        Wire(),
        Wire(status=401, answer={"message": "SENTINEL-BODY-TEXT"}),
        Wire(error=httpx.ConnectError("SENTINEL-BODY-TEXT")),
        Wire(answer={"partialSuccess": {"rejectedSpans": 1, "errorMessage": "SENTINEL-BODY-TEXT"}}),
    ]
    for wire in answers:
        tracer = tracer_for(wire, LANGFUSE_CAPTURE_CONTENT="true")
        chat(tracer, FakeModel(say(f"SENTINEL-REPLY {SK}")), text=f"SENTINEL-PROMPT {PK}")
    lines = tracer_lines(caplog)
    assert len(lines) == 4, "one line for each run"
    for line in lines:
        text = line.getMessage() + " " + str(line.args) + str(line.exc_info)
        assert LINE.match(line.getMessage()), line.getMessage()
        for secret in (PK, SK, HOST, "SENTINEL", "Authorization", "Basic"):
            assert secret not in text


def test_with_tracing_off_nothing_at_all_is_logged(caplog):
    caplog.set_level(logging.DEBUG)
    chat(tracing.NullTracer(), script())
    trace = tracing.tracer_for(load_settings({})).start(run_id="RUN-O1", kind="chat")
    trace.finish("ok")
    assert tracer_lines(caplog) == []


def test_a_real_tracer_makes_its_info_lines_visible_in_the_container_log():
    tracing.Tracer(SETTINGS, client=Wire().client())
    assert tracing.log.isEnabledFor(logging.INFO) and tracing.log.handlers


# --------------------------------------------------------------------------- the counters
def test_the_counters_after_a_sent_run_a_refused_run_a_timeout_and_a_partial_rejection():
    wire = Wire()
    tracer = tracer_for(wire)
    assert tracer.summary() == {
        "on": True, "runs_sent": 0, "runs_failed": 0, "runs_dropped": 0, "spans_rejected": 0,
        "last_status": None,
    }
    chat(tracer, script())
    assert tracer.summary() == {
        "on": True, "runs_sent": 1, "runs_failed": 0, "runs_dropped": 0, "spans_rejected": 0,
        "last_status": 200,
    }
    refused = tracer_for(Wire(status=401))
    chat(refused, script())
    assert refused.summary()["runs_failed"] == 1 and refused.summary()["last_status"] == 401
    assert refused.summary()["runs_sent"] == 0
    slow = tracer_for(Wire(error=httpx.ReadTimeout("slow")))
    chat(slow, script())
    assert slow.summary()["runs_failed"] == 1 and slow.summary()["last_status"] is None
    partial = tracer_for(Wire(answer={"partialSuccess": {"rejectedSpans": 2}}))
    chat(partial, script())
    assert partial.summary()["spans_rejected"] == 2 and partial.summary()["runs_sent"] == 1


def test_a_run_dropped_from_a_full_queue_is_counted_as_a_run():
    tracer = tracing.Tracer(SETTINGS, client=Wire().client())
    for k in range(tracing.QUEUE_MAX + 2):
        tracer.submit([{"spanId": f"{k:016x}"}])
    assert tracer.summary()["runs_dropped"] == 2


def test_the_summary_has_six_fields_and_no_host_and_no_key():
    tracer = tracer_for(Wire())
    chat(tracer, script())
    summary = tracer.summary()
    assert set(summary) == SIX
    text = json.dumps(summary)
    for secret in (PK, SK, HOST, "langfuse.example", "lf."):
        assert secret not in text


def test_the_off_tracer_reports_on_false_and_zeros():
    assert tracing.NullTracer().summary() == {
        "on": False, "runs_sent": 0, "runs_failed": 0, "runs_dropped": 0, "spans_rejected": 0,
        "last_status": None,
    }


# --------------------------------------------------------------------------- the service
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from orchestrator import app as service  # noqa: E402

AUTH = {"Authorization": "Bearer k"}


class Answers:
    def __init__(self, settings):
        self.settings = settings

    async def complete(self, messages, definitions):
        return Reply("The records show a steady trend [A-000001].")


class NoTools:
    async def list_tools(self):
        return []

    async def call(self, name, arguments):
        return {}


def run_events(response):
    out = []
    for block in response.text.strip().split("\n\n"):
        name, data = block.split("\n")
        out.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


def test_the_service_traces_a_chat_run_under_its_run_id_and_flushes_on_shutdown(monkeypatch):
    for key, value in BASE.items():
        monkeypatch.setenv(key, value)
    wire = Wire()
    tracer = tracer_for(wire)

    @asynccontextmanager
    async def servers(settings, token, run_id):
        yield {tools.MDEIS: NoTools()}

    app = service.create_app(
        model_factory=Answers, servers_factory=servers, tracer_factory=lambda settings: tracer
    )
    body = {
        "conversation_id": "c-1", "approver_token": TOKEN,
        "messages": [{"role": "user", "content": "How is the reading going?"}],
    }
    with TestClient(app) as client:
        response = client.post("/run", json=body, headers=AUTH)
    final = run_events(response)[-1][1]
    [root] = wire.roots()
    mine = attrs(root)
    assert root["traceId"] == uuid.UUID(final["run_id"].removeprefix("RUN-")).hex
    assert mine["mdeis.run_id"] == final["run_id"]
    assert mine["session.id"] == "c-1" and mine["user.id"] == "u-12"
    assert mine["langfuse.trace.metadata.outcome"] == "ok"
    assert len(wire.generations()) == 1
    assert "How is the reading going?" not in wire.text()


def test_the_service_traces_a_lesson_without_the_notes(monkeypatch):
    for key, value in BASE.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("CLASS_VIEW_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_CAPTURE_CONTENT", "true")
    wire = Wire()
    tracer = tracer_for(wire, LANGFUSE_CAPTURE_CONTENT="true")

    @asynccontextmanager
    async def servers(settings, token, run_id):
        yield {tools.MDEIS: LessonTools()}

    app = service.create_app(
        model_factory=lambda settings: FakeModel(say(GOOD)), lesson_servers_factory=servers,
        tracer_factory=lambda settings: tracer,
    )
    body = {
        "approver_token": TOKEN, "stage": "Form 3", "area": "literacy", "term": 2,
        "objective": OBJECTIVE, "notes": LESSON,
    }
    with TestClient(app) as client:
        response = client.post("/lessons", json=body, headers=AUTH)
    assert response.json()["outcome"] == "stored"
    assert attrs(wire.roots()[0])["langfuse.trace.metadata.outcome"] == "stored"
    for fragment in (A_ASK, NEW_ASK, OBJECTIVE):
        assert fragment not in wire.text()


def test_the_service_with_tracing_off_makes_no_tracer_traffic(monkeypatch):
    for key, value in BASE.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("LANGFUSE_HOST", "")

    def no_network(*args, **kwargs):
        raise AssertionError("tracing is off, so no client may be made for it")

    @asynccontextmanager
    async def servers(settings, token, run_id):
        yield {tools.MDEIS: NoTools()}

    app = service.create_app(model_factory=Answers, servers_factory=servers)
    monkeypatch.setattr(httpx, "AsyncClient", no_network)
    body = {
        "conversation_id": "c-1", "approver_token": TOKEN,
        "messages": [{"role": "user", "content": "How is the reading going?"}],
    }
    with TestClient(app) as client:
        response = client.post("/run", json=body, headers=AUTH)
    assert run_events(response)[-1][0] == "final"


def healthy(monkeypatch):
    """No database in a unit test: say the service is otherwise fine, so `status` is about tracing
    only."""
    monkeypatch.setattr(service, "_problems", lambda: [])


def health_of(client, until=None):
    """The health answer. With `until`, ask again until it holds."""
    deadline = time.monotonic() + 3
    while True:
        response = client.get("/healthz")
        if until is None or until(response.json()["tracing"]) or time.monotonic() > deadline:
            return response
        time.sleep(0.05)


def run_body():
    return {
        "conversation_id": "c-1", "approver_token": TOKEN,
        "messages": [{"role": "user", "content": "How is the reading going?"}],
    }


@asynccontextmanager
async def no_tool_servers(settings, token, run_id):
    yield {tools.MDEIS: NoTools()}


def test_healthz_carries_the_counters_and_keeps_its_other_fields(monkeypatch):
    healthy(monkeypatch)
    for key, value in BASE.items():
        monkeypatch.setenv(key, value)
    tracer = tracer_for(Wire())
    app = service.create_app(
        model_factory=Answers, servers_factory=no_tool_servers,
        tracer_factory=lambda settings: tracer,
    )
    with TestClient(app) as client:
        before = client.get("/healthz")
        assert before.status_code == 200 and before.json()["status"] == "ok"
        assert before.json()["tracing"]["on"] is True and before.json()["tracing"]["runs_sent"] == 0
        client.post("/run", json=run_body(), headers=AUTH)
        after = health_of(client, until=lambda t: t["runs_sent"] == 1).json()
    assert {"status", "problems", "mode", "engine", "model_configured"} <= set(after)
    assert set(after["tracing"]) == SIX
    assert after["tracing"] == {
        "on": True, "runs_sent": 1, "runs_failed": 0, "runs_dropped": 0, "spans_rejected": 0,
        "last_status": 200,
    }
    assert HOST not in json.dumps(after) and PK not in json.dumps(after)


@pytest.mark.parametrize(
    "wire_args",
    [{"error": httpx.ConnectError("refused")}, {"status": 401}, {"status": 400}],
    ids=["connect-error", "401", "400"],
)
def test_a_failing_sender_never_makes_the_service_unhealthy(monkeypatch, wire_args):
    healthy(monkeypatch)
    for key, value in BASE.items():
        monkeypatch.setenv(key, value)
    tracer = tracer_for(Wire(**wire_args))
    app = service.create_app(
        model_factory=Answers, servers_factory=no_tool_servers,
        tracer_factory=lambda settings: tracer,
    )
    with TestClient(app) as client:
        response = client.post("/run", json=run_body(), headers=AUTH)
        assert run_events(response)[-1][0] == "final"
        health = health_of(client, until=lambda t: t["runs_failed"] == 1)
    assert health.status_code == 200 and health.json()["status"] == "ok"
    assert health.json()["problems"] == []
    assert health.json()["tracing"]["runs_failed"] == 1


def test_a_broken_tracer_factory_does_not_break_healthz(monkeypatch):
    healthy(monkeypatch)

    def boom(settings):
        raise RuntimeError("factory bug")

    app = service.create_app(tracer_factory=boom)
    health = TestClient(app).get("/healthz")
    assert health.status_code == 200 and health.json()["tracing"]["on"] is False


def test_with_tracing_off_healthz_says_on_false_and_logs_nothing(monkeypatch, caplog):
    healthy(monkeypatch)
    for key, value in BASE.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")
    caplog.set_level(logging.DEBUG)
    app = service.create_app(model_factory=Answers, servers_factory=no_tool_servers)
    with TestClient(app) as client:
        client.post("/run", json=run_body(), headers=AUTH)
        health = client.get("/healthz")
    assert health.json()["tracing"] == {
        "on": False, "runs_sent": 0, "runs_failed": 0, "runs_dropped": 0, "spans_rejected": 0,
        "last_status": None,
    }
    assert tracer_lines(caplog) == []


# --------------------------------------------------------------------------- the code itself
def test_no_new_dependency_no_legacy_route_and_no_literal_key_in_the_tracing_module():
    source = (ROOT / "src" / "orchestrator" / "tracing.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import|from)\s+(langfuse|opentelemetry)", source, re.M)
    toml = (ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    assert "langfuse" not in toml and "opentelemetry" not in toml
    assert "/api/public/ingestion" not in source, "Langfuse Cloud closed that route on 2026-11-16"
    assert "/api/public/otel/v1/traces" in source
    assert not re.search(r"[ps]k-lf-[A-Za-z0-9]{8}", source)


def test_only_the_orchestrator_names_langfuse_in_source():
    src = ROOT / "src"
    mentions = {
        p.relative_to(src).parts[0]
        for p in src.rglob("*.py")
        if "langfuse" in p.read_text(encoding="utf-8").lower()
    }
    assert mentions == {"orchestrator"}
