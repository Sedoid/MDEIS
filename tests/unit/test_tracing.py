"""Optional Langfuse tracing in the orchestrator (T-067). Written before the code.

There is no network and no Langfuse package: events go to Langfuse's HTTP ingestion route with
httpx, and every test puts `httpx.MockTransport` where the network would be. Expected values are
typed by hand: the sample OpenRouter answer below is made up (plausible fields, invented numbers).

What is held here: off unless all three variables are set (and then no client is even made); the
model request asks OpenRouter for usage and keeps the provider block; tokens and the USD cost come
from the answer and are never computed (no cost when the answer has none); the default carries no
prompt, reply, tool argument or tool result; with content capture on, a lesson's notes and
objective and a learner's name still never leave; the user is an id, never a name; the trace id is
the run id; the keys are used for basic auth only and are in no body and no log line; a transport
that fails, times out, answers 401 or hangs changes nothing about the run; the queue is bounded;
the shutdown flush sends each queued event once; and the chat loop and the lesson graph give the
same answers with the tracer on and off.
"""

import asyncio
import base64
import json
import logging
import re
import time
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


def cfg(**over):
    return load_settings({**BASE, **over})


SETTINGS = cfg()


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
        return httpx.Response(self.status, json=self.answer or {"successes": [], "errors": []})

    def client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def events(self, kind=None):
        found = [e for r in self.requests for e in json.loads(r.content)["batch"]]
        return [e for e in found if kind in (None, e["type"])]

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


# --------------------------------------------------------------------------- what an event holds
def real_model(answer=SAMPLE):
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=answer))
    return OpenRouterModel(SETTINGS, client=httpx.AsyncClient(transport=transport))


def test_a_generation_carries_tokens_cost_model_provider_and_time():
    wire = Wire()
    chat(tracer_for(wire), real_model())
    [gen] = wire.events("generation-create")
    body = gen["body"]
    assert body["traceId"] == "RUN-C1" and body["model"] == "open/weights-model"
    assert body["usageDetails"] == {"input": 120, "output": 30, "total": 150}
    assert body["costDetails"] == {"total": 0.000345}
    assert body["startTime"] <= body["endTime"]
    assert body["metadata"]["provider"] == "Alpha" and body["metadata"]["graph_kind"] == "chat"
    assert isinstance(body["metadata"]["latency_ms"], int) and body["metadata"]["latency_ms"] >= 0


def test_with_no_cost_in_the_answer_tokens_are_sent_and_no_cost():
    wire = Wire()
    answer = {**SAMPLE, "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}}
    chat(tracer_for(wire), real_model(answer))
    body = wire.events("generation-create")[0]["body"]
    assert body["usageDetails"] == {"input": 5, "output": 2, "total": 7}
    assert "costDetails" not in body


def test_a_reply_with_unusable_usage_still_traces_without_numbers():
    wire = Wire()
    bad = Reply("fine", usage={"prompt_tokens": "x", "cost": True})
    chat(tracer_for(wire), FakeModel(bad))
    body = wire.events("generation-create")[0]["body"]
    assert "usageDetails" not in body and "costDetails" not in body


def test_a_failed_model_call_is_an_error_generation_and_the_error_still_reaches_the_run():
    wire = Wire()
    failure = ModelError("the model service answered with an error (500)")
    out = chat(tracer_for(wire), FakeModel(failure))
    assert out[-1]["event"] == "error"
    body = wire.events("generation-create")[0]["body"]
    assert body["level"] == "ERROR" and "500" in body["statusMessage"]


def test_tool_calls_are_spans_with_the_tool_name_and_the_outcome_only():
    wire = Wire()
    results = {"get_criterion": {"status": "refused", "reason": "not allowed"}}
    chat(tracer_for(wire), script(), results=results)
    [span] = wire.events("span-create")
    body = span["body"]
    assert body["name"] == "get_criterion" and body["traceId"] == "RUN-C1"
    assert body["metadata"]["outcome"] == "refused" and body["level"] == "WARNING"
    assert body["startTime"] <= body["endTime"]
    assert "TR-1" not in wire.text() and "not allowed" not in wire.text()


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
    for gen in wire.events("generation-create"):
        assert "input" not in gen["body"] and "output" not in gen["body"]
    names = [e["body"]["name"] for e in wire.events("span-create")]
    assert names == ["get_criterion"], "the tool name and nothing else"


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
    first, second = [e["body"] for e in wire.events("generation-create")]
    assert any("Tell me about the reading" in m["content"] for m in second["input"])
    assert "reading in the Form 1 range" in second["output"]["text"], "capture is really on"
    assert first["output"]["tool_calls"] == ["execute_sql"]
    tool_messages = [m for m in second["input"] if m["role"] == "tool"]
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
    [gen] = wire.events("generation-create")
    system, user = gen["body"]["input"]
    assert system["content"] == lesson_prompt.system_prompt(), "capture is really on"
    assert user["content"] == tracing.WITHHELD
    assert gen["body"]["output"]["text"] == tracing.WITHHELD


def test_a_lesson_trace_has_the_graph_tools_the_outcome_and_the_repair_count():
    wire = Wire()
    lesson(tracer_for(wire), FakeModel(say(GOOD)))
    spans = [e["body"]["name"] for e in wire.events("span-create")]
    assert spans == ["class_baseline", "guard_lesson_notes", "save_lesson_plan"]
    created = [e["body"] for e in wire.events("trace-create")]
    first, last = created[0], created[-1]
    assert first["id"] == "RUN-L1" and first["metadata"]["graph_kind"] == "lesson"
    assert "sessionId" not in first, "a lesson has no conversation"
    assert last["id"] == "RUN-L1"
    assert last["metadata"] == {"outcome": "stored", "repair_rounds": 0, "model_calls": 1}
    text = wire.text()
    for fragment in (A_ASK, NEW_ASK, OBJECTIVE):
        assert fragment not in text


# --------------------------------------------------------------------------- who and which run
def test_the_user_is_the_id_the_session_is_the_conversation_and_the_trace_is_the_run():
    wire = Wire()
    chat(tracer_for(wire), script(), run_id="RUN-ABC", conv="conv-9")
    first = wire.events("trace-create")[0]["body"]
    assert first["id"] == "RUN-ABC"
    assert first["userId"] == "u-12" and first["sessionId"] == "conv-9"
    assert "Ada" not in wire.text() and "Teacher" not in wire.text()
    for e in wire.events():
        if e["type"] != "trace-create":
            assert e["body"]["traceId"] == "RUN-ABC"


@pytest.mark.parametrize("token", [None, "", "signed.token", "no-dot", "a.b.c"])
def test_a_token_that_cannot_be_read_gives_no_user_and_never_breaks_the_run(token):
    wire = Wire()
    chat(tracer_for(wire), script(), token=token)
    assert "userId" not in wire.events("trace-create")[0]["body"]


# --------------------------------------------------------------------------- the wire
def test_the_request_is_a_basic_auth_post_to_the_ingestion_route_and_has_the_batch_shape():
    wire = Wire()
    chat(tracer_for(wire), script())
    request = wire.requests[0]
    expected = "Basic " + base64.b64encode(f"{PK}:{SK}".encode()).decode()
    assert request.method == "POST"
    assert str(request.url) == HOST + "/api/public/ingestion"
    assert request.headers["authorization"] == expected
    assert request.headers["content-type"].startswith("application/json")
    events = wire.events()
    assert events and len({e["id"] for e in events}) == len(events)
    for e in events:
        assert set(e) == {"id", "type", "timestamp", "body"}
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z", e["timestamp"])
    assert {e["type"] for e in events} == {"trace-create", "generation-create", "span-create"}


def test_the_keys_are_in_no_event_and_no_log_line(caplog):
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
        {"status": 500, "answer": {"message": "oops"}},
    ],
    ids=["connect-error", "timeout", "401", "500"],
)
def test_a_failing_transport_changes_nothing_and_the_failures_are_counted(wire_args):
    wire = Wire(**wire_args)
    tracer = tracer_for(wire)
    assert chat(tracer, script()) == plain_run(script)
    assert tracer.stats["sent"] == 0
    assert tracer.stats["failed"] == tracer.stats["queued"] > 0


def test_a_207_answer_is_normal_and_failed_items_are_counted_not_quoted(caplog):
    caplog.set_level(logging.DEBUG)
    failed = {"id": "x", "status": 400, "message": "SECRET-BAD-FIELD"}
    answer = {"successes": [], "errors": [failed]}
    wire = Wire(status=207, answer=answer)
    tracer = tracer_for(wire)
    for k in range(3):  # no loop is running, so the three wait and go in one batch
        tracer.emit("span-create", {"id": f"s{k}", "traceId": "RUN-M1", "name": "t"})
    asyncio.run(tracer.flush())
    assert len(wire.requests) == 1
    assert tracer.stats["rejected"] == 1 and tracer.stats["sent"] == 2
    assert "SECRET-BAD-FIELD" not in caplog.text


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

    monkeypatch.setattr(tracer, "emit", boom)
    assert chat(tracer, script()) == plain_run(script)
    assert tracer.stats["errors"] > 0 and wire.requests == []


def test_the_queue_is_bounded_and_the_overflow_is_counted():
    tracer = tracing.Tracer(SETTINGS, client=Wire().client())
    for k in range(tracing.QUEUE_MAX + 5):
        tracer.emit("span-create", {"id": f"s{k}", "traceId": "RUN-Q"})
    assert len(tracer._queue) == tracing.QUEUE_MAX
    assert tracer.stats["dropped"] == 5


def test_the_flush_on_shutdown_sends_each_queued_event_once():
    wire = Wire()
    tracer = tracer_for(wire)
    trace = tracer.start(run_id="RUN-F1", kind="chat", token=TOKEN, conversation_id="c")
    trace.finish("ok")  # two events are queued; no loop is running, so nothing was sent
    assert wire.requests == []
    asyncio.run(tracer.flush())
    assert len(wire.requests) == 1 and len(wire.events()) == 2
    asyncio.run(tracer.flush())
    assert len(wire.requests) == 1, "a second flush has nothing left to send"
    assert tracer.stats["sent"] == 2


def test_a_flush_right_after_the_events_does_not_send_any_of_them_twice():
    wire = Wire()
    tracer = tracer_for(wire)

    async def go():
        trace = tracer.start(run_id="RUN-F2", kind="chat", token=TOKEN, conversation_id="c")
        for k in range(120):  # more than one batch
            tracer.emit("span-create", {"id": f"s{k}", "traceId": "RUN-F2", "name": "t"})
        trace.finish("ok")
        await tracer.flush()

    asyncio.run(go())
    ids = [e["id"] for e in wire.events()]
    assert len(ids) == 122 and len(set(ids)) == 122
    assert all(len(json.loads(r.content)["batch"]) <= tracing.BATCH_MAX for r in wire.requests)


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
    last = wire.events("trace-create")[-1]["body"]["metadata"]
    assert last["repair_rounds"] == 1 and last["model_calls"] == 2
    assert len(wire.events("generation-create")) == 2


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
    created = [e["body"] for e in wire.events("trace-create")]
    assert created[0]["id"] == final["run_id"] and created[0]["sessionId"] == "c-1"
    assert created[0]["userId"] == "u-12"
    assert created[-1]["metadata"]["outcome"] == "ok"
    assert len(wire.events("generation-create")) == 1
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
    assert wire.events("trace-create")[-1]["body"]["metadata"]["outcome"] == "stored"
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


# --------------------------------------------------------------------------- the code itself
def test_no_new_dependency_and_no_literal_key_in_the_tracing_module():
    source = (ROOT / "src" / "orchestrator" / "tracing.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import|from)\s+langfuse", source, re.M)
    assert "langfuse" not in (ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    assert not re.search(r"[ps]k-lf-[A-Za-z0-9]{8}", source)


def test_only_the_orchestrator_names_langfuse_in_source():
    src = ROOT / "src"
    mentions = {
        p.relative_to(src).parts[0]
        for p in src.rglob("*.py")
        if "langfuse" in p.read_text(encoding="utf-8").lower()
    }
    assert mentions == {"orchestrator"}
