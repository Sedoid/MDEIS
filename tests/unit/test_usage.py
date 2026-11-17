"""The usage log (T-074): one JSON line for each finished run, kept apart from Langfuse.

Written before the code. The orchestrator appends one line (schema v1) to the file named by
USAGE_LOG_FILE when a chat or lesson run ends. It works with tracing off, never raises, never slows
or changes an answer, and holds no prompt, reply, tool argument or result, learner name, lesson
note, key or token. There is no network: tracing, when on, talks to `httpx.MockTransport`. Expected
numbers are typed by hand (the sums in the comments are worked out on paper).
"""

import ast
import asyncio
import dataclasses
import json
import logging
import re
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from lesson_graph_fakes import LessonTools
from lessonkit import A_ASK, FINDING_ID, LESSON, OBJECTIVE
from orchestrator_fakes import MDEIS_LISTING, FakeModel, FakeServer, ask, say

from mdeis.core import approver_token
from orchestrator import loop, tools, tracing, usage
from orchestrator.config import load_settings
from orchestrator.lessons import graph as lesson_graph
from orchestrator.model import ModelError, Reply

ROOT = Path(__file__).resolve().parents[2]
PK, SK = "test-public-key-0001", "test-secret-key-0002"  # made up; not real keys
MODEL_KEY = "SENTINEL-OR-KEY"
BASE = {
    "ORCHESTRATOR_API_KEY": "k",
    "OPENROUTER_API_KEY": MODEL_KEY,
    "OPENROUTER_MODEL": "open/weights-model",
}
LANGFUSE = {
    "LANGFUSE_PUBLIC_KEY": PK, "LANGFUSE_SECRET_KEY": SK, "LANGFUSE_HOST": "https://lf.test",
}
TOKEN = approver_token.sign("test-secret-not-real", "u-12", "Ada Teacher", school="TST")
NEW_ASK = "Ask what the passage tells us and why."
GOOD = json.dumps({"edits": [{
    "id": "e1", "type": "reword", "anchor_quote": A_ASK, "replacement": NEW_ASK,
    "finding_ids": [FINDING_ID], "resource_ids": [],
    "suggestion": "Give learners a question that asks for a reason.",
}]})
CALL1 = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120, "cost": 0.0001}
CALL2 = {"prompt_tokens": 200, "completion_tokens": 50, "total_tokens": 250, "cost": 0.00025}
NO_COST = {"prompt_tokens": 200, "completion_tokens": 50, "total_tokens": 250}
TOP_KEYS = [
    "v", "run_id", "conversation_id", "kind", "started_at", "ended_at", "latency_ms", "outcome",
    "user_id", "model", "provider", "model_calls", "tool_calls", "input_tokens", "output_tokens",
    "total_tokens", "cost_usd", "cost_reported_calls", "repair_rounds", "calls", "tools",
]
CALL_KEYS = ["n", "input_tokens", "output_tokens", "cost_usd", "latency_ms", "tools_requested"]
TOOL_KEYS = ["name", "latency_ms", "outcome"]
STAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
WARNING = re.compile(r"^usage log: line not written, reason=\w+$")
VOLATILE = ("started_at", "ended_at", "latency_ms")


def cfg(path=None, **over):
    env = {**BASE, **over}
    if path is not None:
        env["USAGE_LOG_FILE"] = str(path)
    return load_settings(env)


def with_usage(reply, **fields):
    return dataclasses.replace(reply, usage=fields)


def two_calls():
    return FakeModel(
        dataclasses.replace(with_usage(ask("get_criterion", {"query": "TR-1"}), **CALL1),
                            provider="Alpha"),
        dataclasses.replace(with_usage(say("Reading is steady."), **CALL2), provider="Alpha"),
    )


def tracer_for(path, *, traced=False, **over):
    """A tracer with the usage log at `path` (None: log off). `traced`: Langfuse on, with a stand-in
    for the network."""
    if not traced:
        return tracing.tracer_for(cfg(path, **over))
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    client = httpx.AsyncClient(transport=transport)
    return tracing.Tracer(cfg(path, **LANGFUSE, **over), client=client)


async def chat_async(tracer, model, *, text="How is the reading going?", results=None,
                     run_id="RUN-U1", conv="conv-1"):
    servers = {tools.MDEIS: FakeServer(MDEIS_LISTING, results or {})}
    trace = tracer.start(run_id=run_id, kind="chat", token=TOKEN, conversation_id=conv)
    outcome, out = "error", []
    async for item in loop.run(
        messages=[{"role": "user", "content": text}], token=TOKEN, run_id=run_id,
        settings=cfg(), model=trace.wrap_model(model), servers=trace.wrap_servers(servers),
    ):
        outcome = tracing.chat_outcome(item, outcome)
        out.append(item)
    trace.finish(outcome)
    await tracer.flush()
    return out


def chat(tracer, model, **kwargs):
    out = asyncio.run(chat_async(tracer, model, **kwargs))
    usage.wait_all()
    return out


async def lesson_async(tracer, model, run_id="RUN-L1"):
    trace = tracer.start(run_id=run_id, kind="lesson", token=TOKEN, mask=(LESSON, OBJECTIVE))
    answer = await lesson_graph.run(
        stage="Form 3", area="literacy", term=2, objective=OBJECTIVE, notes=LESSON,
        run_id=run_id, settings=cfg(), model=trace.wrap_model(model),
        servers=trace.wrap_servers({tools.MDEIS: LessonTools()}), engine="plain",
    )
    trace.finish(
        answer["outcome"], repair_rounds=answer["repair_rounds"], model_calls=answer["model_calls"]
    )
    await tracer.flush()
    return answer


def lesson(tracer, model, **kwargs):
    answer = asyncio.run(lesson_async(tracer, model, **kwargs))
    usage.wait_all()
    return answer


def raw(path):
    return Path(path).read_text(encoding="utf-8")


def lines(path):
    return [json.loads(text) for text in raw(path).splitlines()]


def steady(line):
    """A line without what changes from run to run."""
    line = {k: v for k, v in line.items() if k not in VOLATILE}
    line["calls"] = [{k: v for k, v in c.items() if k != "latency_ms"} for c in line["calls"]]
    line["tools"] = [{k: v for k, v in t.items() if k != "latency_ms"} for t in line["tools"]]
    return line


# --------------------------------------------------------------------------- the setting
def test_the_setting_is_off_by_default_and_read_from_the_variable(tmp_path):
    assert load_settings({}).usage_log_file == ""
    assert cfg(tmp_path / "u.jsonl").usage_log_file == str(tmp_path / "u.jsonl")
    assert cfg(USAGE_LOG_FILE="  ").usage_log_file == ""


def test_the_variable_is_in_the_env_example_and_the_compose_file():
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^USAGE_LOG_FILE=", env, re.M)
    compose = (ROOT / "deploy" / "compose" / "orchestrator.yml").read_text(encoding="utf-8")
    assert "USAGE_LOG_FILE: ${USAGE_LOG_FILE" in compose


@pytest.mark.parametrize("traced", [False, True], ids=["tracing-off", "tracing-on"])
def test_off_by_default_writes_nothing_and_reports_off(tmp_path, monkeypatch, traced):
    monkeypatch.chdir(tmp_path)
    chat(tracer_for(None, traced=traced), two_calls())
    lesson(tracer_for(None, traced=traced), FakeModel(say(GOOD)))
    assert list(tmp_path.iterdir()) == []
    assert usage.log_for(cfg()).summary() == {"on": False, "lines_written": 0, "lines_failed": 0}


# --------------------------------------------------------------------------- the line
@pytest.mark.parametrize("traced", [False, True], ids=["tracing-off", "tracing-on"])
def test_a_chat_run_writes_one_line_with_the_exact_keys_and_the_sums(tmp_path, traced):
    path = tmp_path / "usage.jsonl"
    before = time.time()
    chat(tracer_for(path, traced=traced), two_calls())
    [line] = lines(path)
    assert list(line) == TOP_KEYS
    assert line["v"] == usage.SCHEMA_VERSION == 1
    assert (line["run_id"], line["conversation_id"], line["kind"]) == ("RUN-U1", "conv-1", "chat")
    assert (line["outcome"], line["user_id"]) == ("ok", "u-12")
    assert (line["model"], line["provider"]) == ("open/weights-model", "Alpha")
    assert (line["model_calls"], line["tool_calls"]) == (2, 1)
    assert line["input_tokens"] == 300  # 100 + 200
    assert line["output_tokens"] == 70  # 20 + 50
    assert line["total_tokens"] == 370  # 120 + 250
    assert line["cost_usd"] == pytest.approx(0.00035)  # 0.0001 + 0.00025
    assert line["cost_reported_calls"] == 2 and line["repair_rounds"] is None
    assert STAMP.match(line["started_at"]) and STAMP.match(line["ended_at"])
    started = datetime.fromisoformat(line["started_at"])
    ended = datetime.fromisoformat(line["ended_at"])
    assert started.tzinfo is not None and started.utcoffset().total_seconds() == 0
    assert abs(started.timestamp() - before) < 60 and ended >= started
    assert abs((ended - started).total_seconds() * 1000 - line["latency_ms"]) <= 2
    first, second = line["calls"]
    assert list(first) == CALL_KEYS and list(second) == CALL_KEYS
    assert (first["n"], first["input_tokens"], first["output_tokens"]) == (1, 100, 20)
    assert first["cost_usd"] == pytest.approx(0.0001)
    assert first["tools_requested"] == ["get_criterion"]
    assert (second["n"], second["input_tokens"], second["output_tokens"]) == (2, 200, 50)
    assert second["cost_usd"] == pytest.approx(0.00025) and second["tools_requested"] == []
    assert all(isinstance(c["latency_ms"], int) and c["latency_ms"] >= 0 for c in line["calls"])
    [one] = line["tools"]
    assert list(one) == TOOL_KEYS and (one["name"], one["outcome"]) == ("get_criterion", "ok")
    assert isinstance(one["latency_ms"], int)
    assert raw(path).endswith("\n") and raw(path).count("\n") == 1


def test_a_run_adds_one_line_each_and_never_rewrites_the_earlier_ones(tmp_path):
    path = tmp_path / "usage.jsonl"
    chat(tracer_for(path), two_calls(), run_id="RUN-A")
    first = raw(path)
    chat(tracer_for(path), two_calls(), run_id="RUN-B")
    assert raw(path).startswith(first)
    assert [x["run_id"] for x in lines(path)] == ["RUN-A", "RUN-B"]


def test_a_cost_that_no_call_reported_is_null_and_a_partial_sum_says_how_many(tmp_path):
    path = tmp_path / "usage.jsonl"
    none = FakeModel(with_usage(ask("get_criterion", {"query": "x"}), **NO_COST),
                     with_usage(say("ok"), **NO_COST))
    chat(tracer_for(path), none, run_id="RUN-N")
    some = FakeModel(with_usage(ask("get_criterion", {"query": "x"}), **CALL1),
                     with_usage(say("ok"), **NO_COST))
    chat(tracer_for(path), some, run_id="RUN-P")
    bare = FakeModel(ask("get_criterion", {"query": "x"}), say("ok"))
    chat(tracer_for(path), bare, run_id="RUN-Q")
    no_cost, partial, no_usage = lines(path)
    assert no_cost["cost_usd"] is None and no_cost["cost_reported_calls"] == 0
    assert all(c["cost_usd"] is None for c in no_cost["calls"]) and no_cost["input_tokens"] == 400
    assert partial["cost_usd"] == pytest.approx(0.0001) and partial["cost_reported_calls"] == 1
    assert partial["calls"][1]["cost_usd"] is None
    assert no_usage["total_tokens"] == 0 and no_usage["cost_usd"] is None
    assert [c["input_tokens"] for c in no_usage["calls"]] == [None, None]
    assert no_usage["provider"] is None


def test_a_lesson_line_has_no_conversation_and_counts_its_repair_rounds(tmp_path):
    path = tmp_path / "usage.jsonl"
    model = FakeModel(with_usage(say("not json"), **CALL1), with_usage(say(GOOD), **CALL2))
    answer = lesson(tracer_for(path), model)
    [line] = lines(path)
    assert list(line) == TOP_KEYS
    assert (line["kind"], line["conversation_id"], line["user_id"]) == ("lesson", None, "u-12")
    assert (line["outcome"], line["repair_rounds"]) == (answer["outcome"], 1)
    assert line["model_calls"] == 2 and line["input_tokens"] == 300
    assert line["tool_calls"] == len(line["tools"]) >= 1


def test_a_failed_model_call_is_a_call_with_no_numbers_and_the_outcome_is_the_runs(tmp_path):
    path = tmp_path / "usage.jsonl"
    chat(tracer_for(path), FakeModel(ModelError("the model service answered with an error (500)")))
    [line] = lines(path)
    assert line["outcome"] == "error" and line["model_calls"] == 1
    assert line["calls"][0]["input_tokens"] is None and line["calls"][0]["cost_usd"] is None


def test_a_run_with_no_calls_has_empty_lists_and_zero_counts(tmp_path):
    path = tmp_path / "usage.jsonl"
    tracer_for(path).start(run_id="RUN-R", kind="chat", token=TOKEN).finish("refused")
    usage.wait_all()
    [line] = lines(path)
    assert (line["outcome"], line["model_calls"], line["tool_calls"]) == ("refused", 0, 0)
    assert line["calls"] == [] and line["tools"] == [] and line["total_tokens"] == 0
    assert line["cost_usd"] is None and line["model"] == "open/weights-model"


def test_a_run_finished_twice_writes_one_line(tmp_path):
    path = tmp_path / "usage.jsonl"
    trace = tracer_for(path).start(run_id="RUN-D", kind="chat", token=TOKEN)
    trace.finish("ok")
    trace.finish("error")
    usage.wait_all()
    assert [x["outcome"] for x in lines(path)] == ["ok"]


def test_a_user_without_a_readable_token_is_null(tmp_path):
    path = tmp_path / "usage.jsonl"
    tracer_for(path).start(run_id="RUN-V", kind="chat", token="not-a-token").finish("ok")
    tracer_for(path).start(run_id="RUN-W", kind="chat", token=None).finish("ok")
    usage.wait_all()
    assert [x["user_id"] for x in lines(path)] == [None, None]


def test_reasoning_tokens_are_an_optional_integer_on_a_call_and_on_the_run(tmp_path):
    path = tmp_path / "usage.jsonl"
    first = with_usage(ask("get_criterion", {"query": "x"}), **CALL1, reasoning_tokens=7)
    chat(tracer_for(path), FakeModel(first, with_usage(say("ok"), **CALL2)), run_id="RUN-X")
    chat(tracer_for(path), two_calls(), run_id="RUN-Y")
    with_it, without = lines(path)
    assert with_it["reasoning_tokens"] == 7 and with_it["calls"][0]["reasoning_tokens"] == 7
    assert "reasoning_tokens" not in with_it["calls"][1]
    assert "reasoning_tokens" not in without and list(without) == TOP_KEYS
    assert set(with_it) - set(TOP_KEYS) == {"reasoning_tokens"}
    assert set(with_it["calls"][0]) - set(CALL_KEYS) == {"reasoning_tokens"}


# --------------------------------------------------------------------------- what is never in it
SENTINELS = (
    "SENTINEL-PROMPT", "SENTINEL-ARG", "SENTINEL-RESULT", "SENTINEL-REPLY", "Quillfeather",
    "SENTINEL-REASONING", MODEL_KEY, PK, SK, TOKEN, TOKEN.split(".")[0], "Ada Teacher",
)


@pytest.mark.parametrize("traced", [False, True], ids=["tracing-off", "tracing-on"])
def test_no_prompt_reply_argument_result_name_note_key_or_token_is_in_the_file(tmp_path, traced):
    path = tmp_path / "usage.jsonl"
    extra = {"LANGFUSE_CAPTURE_CONTENT": "true", "LANGFUSE_CAPTURE_TOOL_IO": "true"}
    reasoning = dataclasses.replace(
        with_usage(ask("get_criterion", {"query": "SENTINEL-ARG"}), **CALL1),
        reasoning="SENTINEL-REASONING about Quillfeather",
    )
    model = FakeModel(reasoning, with_usage(say("SENTINEL-REPLY the trend is steady."), **CALL2))
    results = {"get_criterion": {
        "status": "ok", "text": "SENTINEL-RESULT", "display_name": "Zanele Quillfeather",
    }}
    chat(tracer_for(path, traced=traced, **extra), model,
         text="SENTINEL-PROMPT how is the reading going?", results=results)
    lesson(tracer_for(path, traced=traced, **extra), FakeModel(say(GOOD)))
    assert len(lines(path)) == 2
    text = raw(path)
    for sentinel in (*SENTINELS, LESSON, OBJECTIVE, A_ASK, NEW_ASK):
        assert sentinel not in text, sentinel


def test_a_tool_name_the_model_made_up_is_cut_to_a_named_length(tmp_path):
    path = tmp_path / "usage.jsonl"
    chat(tracer_for(path), FakeModel(ask("x" * 500), say("ok")))
    [line] = lines(path)
    [name] = line["calls"][0]["tools_requested"]
    assert 0 < len(name) <= usage.MAX_NAME_CHARS < 500


# --------------------------------------------------------------------------- never raises or slows
@pytest.mark.parametrize("traced", [False, True], ids=["tracing-off", "tracing-on"])
def test_a_path_that_cannot_be_written_is_swallowed_counted_and_logged_once(
    tmp_path, caplog, traced
):
    caplog.set_level(logging.DEBUG)
    baseline = chat(tracer_for(None, traced=traced), two_calls())
    missing = tmp_path / "no-such-directory" / "usage.jsonl"
    result = chat(tracer_for(missing, traced=traced), two_calls())
    assert result == baseline
    assert usage.log_for(cfg(missing)).summary() == {
        "on": True, "lines_written": 0, "lines_failed": 1,
    }
    ours = [r for r in caplog.records if r.name == "orchestrator.usage"]
    assert len(ours) == 1 and WARNING.match(ours[0].getMessage())
    assert str(tmp_path) not in ours[0].getMessage()


def test_a_directory_instead_of_a_file_is_counted_too(tmp_path):
    result = chat(tracer_for(tmp_path), two_calls())
    assert result[-1]["event"] == "final"
    assert usage.log_for(cfg(tmp_path)).summary()["lines_failed"] == 1


def test_a_full_disk_is_counted_and_the_next_run_still_writes(tmp_path, monkeypatch):
    path = tmp_path / "usage.jsonl"
    real = usage._write_line

    def full(where, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(usage, "_write_line", full)
    baseline = chat(tracer_for(None), two_calls())
    # (the same run id as the baseline: the final event carries it, so it must match)
    assert chat(tracer_for(path), two_calls()) == baseline
    assert usage.log_for(cfg(path)).summary() == {"on": True, "lines_written": 0, "lines_failed": 1}
    monkeypatch.setattr(usage, "_write_line", real)
    chat(tracer_for(path), two_calls(), run_id="RUN-F2")
    assert [x["run_id"] for x in lines(path)] == ["RUN-F2"]
    assert usage.log_for(cfg(path)).summary() == {"on": True, "lines_written": 1, "lines_failed": 1}


@pytest.mark.parametrize(
    "target", ["RunUsage.finish", "RunUsage.call", "RunUsage.tool", "UsageLog.submit"]
)
def test_a_bug_inside_the_usage_code_changes_no_answer(tmp_path, monkeypatch, target):
    baseline = chat(tracer_for(None), two_calls())
    baseline_lesson = lesson(tracer_for(None), FakeModel(say(GOOD)))

    def boom(*args, **kwargs):
        raise RuntimeError("usage bug")

    owner, name = target.split(".")
    monkeypatch.setattr(getattr(usage, owner), name, boom)
    for traced in (False, True):
        path = tmp_path / f"u-{traced}.jsonl"
        assert chat(tracer_for(path, traced=traced), two_calls()) == baseline
        assert lesson(tracer_for(path, traced=traced), FakeModel(say(GOOD))) == baseline_lesson


@pytest.mark.parametrize("traced", [False, True], ids=["tracing-off", "tracing-on"])
def test_a_slow_disk_does_not_hold_up_the_run(tmp_path, monkeypatch, traced):
    path = tmp_path / "usage.jsonl"
    real = usage.UsageLog._append

    def slow(self, data):
        time.sleep(0.8)
        return real(self, data)

    monkeypatch.setattr(usage.UsageLog, "_append", slow)
    trace = tracer_for(path, traced=traced).start(run_id="RUN-S", kind="chat", token=TOKEN)
    started = time.monotonic()
    trace.finish("ok")
    assert time.monotonic() - started < 0.4, "the run does not wait for the disk"
    usage.wait_all(timeout=10)
    assert [x["run_id"] for x in lines(path)] == ["RUN-S"]
    assert usage.log_for(cfg(path)).summary()["lines_written"] == 1


# --------------------------------------------------------------------------- two runs at once
def test_two_concurrent_runs_write_two_intact_lines(tmp_path):
    path = tmp_path / "usage.jsonl"
    tracer = tracer_for(path)

    async def both():
        return await asyncio.gather(
            chat_async(tracer, two_calls(), run_id="RUN-A", conv="c-a"),
            chat_async(tracer, two_calls(), run_id="RUN-B", conv="c-b"),
        )

    asyncio.run(both())
    usage.wait_all()
    found = lines(path)
    assert sorted(x["run_id"] for x in found) == ["RUN-A", "RUN-B"]
    assert {x["conversation_id"] for x in found} == {"c-a", "c-b"}
    assert all(list(x) == TOP_KEYS for x in found)


def test_many_threads_writing_long_lines_never_interleave(tmp_path):
    path = tmp_path / "usage.jsonl"
    log = usage.log_for(cfg(path))

    def work(thread):
        for i in range(25):
            log.submit({"v": 1, "thread": thread, "i": i, "pad": "x" * 20_000})

    threads = [threading.Thread(target=work, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    usage.wait_all(timeout=30)
    found = lines(path)
    assert len(found) == 200 and {(x["thread"], x["i"]) for x in found} == {
        (t, i) for t in range(8) for i in range(25)
    }
    assert log.summary() == {"on": True, "lines_written": 200, "lines_failed": 0}


# --------------------------------------------------------------------------- nothing else changes
def test_the_chat_and_lesson_results_are_identical_with_the_log_and_tracing_on_or_off(tmp_path):
    chats, lessons, written = [], [], []
    for n, (logged, traced) in enumerate([(0, 0), (1, 0), (0, 1), (1, 1)]):
        path = tmp_path / f"u{n}.jsonl" if logged else None
        chats.append(chat(tracer_for(path, traced=bool(traced)), two_calls()))
        lessons.append(lesson(tracer_for(path, traced=bool(traced)), FakeModel(say(GOOD))))
        if logged:
            written.append([steady(x) for x in lines(path)])
    assert all(c == chats[0] for c in chats) and chats[0][-1]["event"] == "final"
    assert all(x == lessons[0] for x in lessons) and lessons[0]["outcome"] == "stored"
    assert written[0] == written[1], "tracing on or off gives the same usage lines"


def test_the_module_uses_only_the_standard_library_and_never_names_a_tracer():
    source = (ROOT / "src" / "orchestrator" / "usage.py").read_text(encoding="utf-8")
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            names.add((node.module or "").split(".")[0])
    assert names <= set(sys.stdlib_module_names), names
    assert "langfuse" not in source.lower()


# --------------------------------------------------------------------------- the service
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from orchestrator import app as service  # noqa: E402

AUTH = {"Authorization": "Bearer k"}


class Answers:
    def __init__(self, settings):
        self.settings = settings

    async def complete(self, messages, definitions):
        return with_usage(Reply("The records show a steady trend [A-000001]."), **CALL1)


class NoTools:
    async def list_tools(self):
        return []

    async def call(self, name, arguments):
        return {}


@asynccontextmanager
async def no_tool_servers(settings, token, run_id):
    yield {tools.MDEIS: NoTools()}


@asynccontextmanager
async def lesson_servers(settings, token, run_id):
    yield {tools.MDEIS: LessonTools()}


def service_env(monkeypatch, path):
    for key, value in BASE.items():
        monkeypatch.setenv(key, value)
    for key in LANGFUSE:
        monkeypatch.setenv(key, "")  # tracing is off: the log must work alone
    monkeypatch.setenv("USAGE_LOG_FILE", str(path) if path else "")
    monkeypatch.setattr(service, "_problems", lambda: [])


def run_body():
    return {
        "conversation_id": "c-1", "approver_token": TOKEN,
        "messages": [{"role": "user", "content": "SENTINEL-PROMPT how is the reading going?"}],
    }


def health_until(client, ready):
    deadline = time.monotonic() + 5
    while True:
        health = client.get("/healthz").json()
        if ready(health["usage_log"]) or time.monotonic() > deadline:
            return health
        time.sleep(0.05)


def test_the_service_with_tracing_off_writes_a_chat_line_and_reports_it_in_healthz(
    tmp_path, monkeypatch
):
    path = tmp_path / "usage.jsonl"
    service_env(monkeypatch, path)
    app = service.create_app(model_factory=Answers, servers_factory=no_tool_servers)
    with TestClient(app) as client:
        before = client.get("/healthz").json()
        assert before["usage_log"] == {"on": True, "lines_written": 0, "lines_failed": 0}
        response = client.post("/run", json=run_body(), headers=AUTH)
        after = health_until(client, lambda u: u["lines_written"] == 1)
    assert response.status_code == 200 and before["tracing"]["on"] is False
    assert after["usage_log"] == {"on": True, "lines_written": 1, "lines_failed": 0}
    assert {"status", "problems", "mode", "engine", "model_configured", "tracing"} <= set(after)
    [line] = lines(path)
    assert (line["kind"], line["conversation_id"], line["user_id"]) == ("chat", "c-1", "u-12")
    assert line["outcome"] == "ok" and line["model_calls"] == 1 and line["input_tokens"] == 100
    assert line["run_id"].startswith("RUN-") and "SENTINEL-PROMPT" not in raw(path)


def test_the_service_writes_a_lesson_line_without_the_notes(tmp_path, monkeypatch):
    path = tmp_path / "usage.jsonl"
    service_env(monkeypatch, path)
    monkeypatch.setenv("CLASS_VIEW_ENABLED", "true")
    app = service.create_app(
        model_factory=lambda settings: FakeModel(with_usage(say(GOOD), **CALL1)),
        lesson_servers_factory=lesson_servers,
    )
    body = {
        "approver_token": TOKEN, "stage": "Form 3", "area": "literacy", "term": 2,
        "objective": OBJECTIVE, "notes": LESSON,
    }
    with TestClient(app) as client:
        response = client.post("/lessons", json=body, headers=AUTH)
    usage.wait_all()
    assert response.json()["outcome"] == "stored"
    [line] = lines(path)
    assert (line["kind"], line["conversation_id"], line["outcome"]) == ("lesson", None, "stored")
    for text in (LESSON, OBJECTIVE, A_ASK, NEW_ASK):
        assert text not in raw(path)


def test_with_the_log_off_healthz_says_off_and_no_file_is_made(tmp_path, monkeypatch):
    service_env(monkeypatch, None)
    monkeypatch.chdir(tmp_path)
    app = service.create_app(model_factory=Answers, servers_factory=no_tool_servers)
    with TestClient(app) as client:
        client.post("/run", json=run_body(), headers=AUTH)
        health = client.get("/healthz").json()
    assert health["usage_log"] == {"on": False, "lines_written": 0, "lines_failed": 0}
    assert list(tmp_path.iterdir()) == []


def test_a_broken_usage_log_never_breaks_healthz_or_a_run(tmp_path, monkeypatch):
    service_env(monkeypatch, tmp_path / "usage.jsonl")

    def boom(settings):
        raise RuntimeError("usage bug")

    app = service.create_app(model_factory=Answers, servers_factory=no_tool_servers)
    monkeypatch.setattr(usage, "log_for", boom)
    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200 and health.json()["status"] == "ok"
        assert health.json()["usage_log"]["on"] is False
        events = client.post("/run", json=run_body(), headers=AUTH).text
    assert "event: final" in events


def test_a_failing_log_file_never_makes_the_service_unhealthy(tmp_path, monkeypatch):
    service_env(monkeypatch, tmp_path / "nope" / "usage.jsonl")
    app = service.create_app(model_factory=Answers, servers_factory=no_tool_servers)
    with TestClient(app) as client:
        response = client.post("/run", json=run_body(), headers=AUTH)
        health = health_until(client, lambda u: u["lines_failed"] == 1)
    assert "event: final" in response.text
    assert health["status"] == "ok" and health["usage_log"]["lines_failed"] == 1
