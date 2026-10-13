"""Charts in the orchestrator (E-23): the model asks for one by naming subjects, the system draws
it under the answer from the tool's result, the model never sees or writes the picture, and a
picture in the history is not sent on. Scripted fake model and server; no network, no key."""

import asyncio
import re

from orchestrator_fakes import MDEIS_LISTING, FakeModel, FakeServer, ask, say

from orchestrator import chart_reply, graph, loop, tools
from orchestrator.config import load_settings

ENV = {"ORCHESTRATOR_API_KEY": "k", "OPENROUTER_API_KEY": "x", "OPENROUTER_MODEL": "m"}
SETTINGS = load_settings({**ENV, "ORCHESTRATOR_MODE": "graph"})
TOKEN = "signed.token.value"
CHART = {
    "status": "ok",
    "learner_id": "L-1",
    "title": "Mathematics, Chemistry · L-1",
    "subtitle": "Rulebook v1",
    "scale_max": "20.00",
    "terms": [{"label": "T1", "group": "2025/26 · Form 1", "band": None},
              {"label": "T2", "group": "2025/26 · Form 1", "band": None}],
    "series": [{"name": "Mathematics", "values": ["12.00", "13.00"]},
               {"name": "Chemistry", "values": ["8.00", None]}],
    "band_legend": "",
    "gap": None,
    "excluded": [{"skill": "Physics", "reason": "not_enough_evidence",
                  "scores_available": 2, "scores_needed": 3}],
    "record_ids": ["A-000001", "A-000002", "A-000007"],
    "clause_ids": ["TR-1"],
    "source": "Report-card scores only. Not a prediction.",
    "result_hash": "abc",
}
IMAGE = re.compile(
    r"!\[Chart: Mathematics, Chemistry\]\(data:image/svg\+xml;base64,[A-Za-z0-9+/=]+\)"
)
ASK = ("make_chart", {"learner_id": "L-1", "skills": ["Mathematics", "Chemistry", "Physics"]})
USER = [{"role": "user", "content": "Show Mathematics and Chemistry over the terms for L-1"}]


def run_loop(messages, model, server):
    async def collect():
        return [e async for e in loop.run(messages=messages, token=TOKEN, run_id="RUN-1",
                                          settings=SETTINGS, model=model,
                                          servers={tools.MDEIS: server})]

    return asyncio.run(collect())


def run_graph(messages, model, server):
    async def collect():
        return [e async for e in graph.run(messages=messages, token=TOKEN, run_id="RUN-1",
                                           settings=SETTINGS, model=model,
                                           servers={tools.MDEIS: server}, engine="plain")]

    return asyncio.run(collect())


def final(events):
    return next(e["data"] for e in events if e["event"] == "final")


def test_the_tool_is_on_the_allow_list():
    assert "make_chart" in tools.MDEIS_TOOLS


def test_the_picture_goes_under_the_models_words_with_a_caption_in_both_engines():
    for runner in (run_loop, run_graph):
        server = FakeServer(MDEIS_LISTING, {"make_chart": CHART})
        model = FakeModel(ask(*ASK), say("Mathematics rose and Chemistry has one score so far."))
        answer = final(runner(USER, model, server))["answer"]
        assert answer.startswith("Mathematics rose and Chemistry has one score so far.")
        assert IMAGE.search(answer), runner.__name__
        assert "Records: [A-000001] [A-000002] [A-000007]. Rules: [TR-1]." in answer
        assert "Not drawn: Physics (only 2 of the 3 scores needed are on record)." in answer


def test_the_model_is_never_shown_the_picture():
    for runner in (run_loop, run_graph):
        server = FakeServer(MDEIS_LISTING, {"make_chart": CHART})
        model = FakeModel(ask(*ASK), say("Done."))
        runner(USER, model, server)
        seen = " ".join(str(message) for call in model.seen for message in call[0])
        assert "data:image" not in seen and "<svg" not in seen and "base64" not in seen


def test_an_image_the_model_writes_itself_is_removed():
    evil = "Here: ![x](https://elsewhere.example/a.png) and ![y](data:image/svg+xml;base64,AAAA)"
    for runner in (run_loop, run_graph):
        model = FakeModel(say(evil))
        answer = final(runner(USER, model, FakeServer(MDEIS_LISTING)))["answer"]
        assert "elsewhere.example" not in answer and "base64" not in answer
        assert answer.count(chart_reply.SEEN) == 2


def test_a_picture_from_an_earlier_turn_is_not_sent_to_the_model_again():
    image = chart_reply.picture(CHART)
    history = [
        {"role": "user", "content": "Show Mathematics"},
        {"role": "assistant", "content": f"It rose.\n\n{image}\n\nRecords: [A-000001]."},
        {"role": "user", "content": "And what does the rulebook say about a move?"},
    ]
    for runner in (run_loop, run_graph):
        model = FakeModel(say("A move is described in the rulebook."))
        runner(history, model, FakeServer(MDEIS_LISTING))
        sent = " ".join(m["content"] or "" for m in model.seen[0][0])
        assert "base64" not in sent and chart_reply.SEEN in sent and "Records: [A-000001]" in sent


def test_nothing_drawn_still_says_why():
    nothing = {"status": "not_drawn", "series": [], "terms": [],
               "excluded": [{"skill": "Chemistry", "reason": "blocked"}], "record_ids": [],
               "clause_ids": []}
    server = FakeServer(MDEIS_LISTING, {"make_chart": nothing})
    model = FakeModel(ask("make_chart", {"learner_id": "L-1", "skills": ["Chemistry"]}),
                      say("I could not draw it."))
    answer = final(run_loop(USER, model, server))["answer"]
    assert "![" not in answer
    assert "Not drawn: Chemistry (a question about its records has to be answered first)." in answer


def test_a_refused_or_failed_chart_call_draws_nothing():
    for result in ({"status": "refused", "reason": "no such subject"}, {"status": "error"}):
        server = FakeServer(MDEIS_LISTING, {"make_chart": result})
        model = FakeModel(ask(*ASK), say("It did not work."))
        answer = final(run_loop(USER, model, server))["answer"]
        assert answer == "It did not work."
