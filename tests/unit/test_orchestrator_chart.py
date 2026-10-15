"""Charts in the orchestrator: the model asks for one by naming subjects, the system draws
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
    "sources": [
        {"document_id": "D-000010", "file": "amina form1 2022-2023.pdf",
         "academic_year": "2022/2023", "stage": "Form 1"},
        {"document_id": "D-000011", "file": "amina form2 2023-2024.pdf",
         "academic_year": "2023/2024", "stage": "Form 2"},
    ],
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
        assert "Rules: [TR-1]." in answer
        assert "Records:" not in answer and "[A-000001]" not in answer
        assert answer.endswith(
            "\n\n---\n\n*Sources (report cards): amina form1 2022-2023.pdf; "
            "amina form2 2023-2024.pdf.*"
        )
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


# --------------------------------------------------------------------------- sources: file names
def sourced(*names):
    return {"status": "ok", "sources": [
        {"document_id": f"D-{n:06d}", "file": name, "academic_year": year, "stage": "Form 1"}
        for n, (name, year) in enumerate(names, start=1)
    ]}


TREND = {**sourced(("b form2.pdf", "2023/2024"), ("a form1.pdf", "2022/2023")), "trend": "steady"}
WORDS = "Mathematics is steady [A-000062] [A-000104], from card [D-000118] [d61ccfdb\u2026]."


def test_record_and_document_numbers_are_replaced_by_the_names_of_the_files():
    for runner in (run_loop, run_graph):
        server = FakeServer(MDEIS_LISTING, {"compute_trend": TREND})
        model = FakeModel(ask("compute_trend", {"learner_id": "L-1", "skill": "Mathematics"}),
                          say(WORDS))
        answer = final(runner(USER, model, server))["answer"]
        assert answer.startswith("Mathematics is steady, from card.")
        assert "[A-" not in answer and "[D-" not in answer and "d61ccfdb" not in answer
        assert answer.endswith("---\n\n*Sources (report cards): a form1.pdf; b form2.pdf.*")


def test_each_file_is_named_once_even_when_several_tools_return_it():
    server = FakeServer(MDEIS_LISTING, {"compute_trend": TREND, "ingest_assessments": TREND})
    model = FakeModel(ask("ingest_assessments", {"learner_id": "L-1"}),
                      ask("compute_trend", {"learner_id": "L-1", "skill": "Mathematics"}),
                      say("Steady."))
    answer = final(run_graph(USER, model, server))["answer"]
    assert answer.count("a form1.pdf") == 1 and answer.count("b form2.pdf") == 1


def test_without_source_files_the_models_words_are_left_as_written():
    model = FakeModel(say("English is steady [A-000001]."))
    answer = final(run_graph(USER, model, FakeServer(MDEIS_LISTING)))["answer"]
    assert answer == "English is steady [A-000001]." and "Sources" not in answer


def test_a_long_list_of_files_is_cut_after_a_dozen():
    many = sourced(*[(f"card {n:02d}.pdf", f"20{n:02d}/20{n + 1:02d}") for n in range(15)])
    server = FakeServer(MDEIS_LISTING, {"compute_trend": many})
    model = FakeModel(ask("compute_trend", {"learner_id": "L-1", "skill": "Mathematics"}),
                      say("Steady."))
    answer = final(run_loop(USER, model, server))["answer"]
    assert answer.endswith("; and 3 more.*") and answer.count(".pdf") == 12


def test_a_question_to_the_approver_has_no_sources_line():
    blocking = {**sourced(("a form1.pdf", "2022/2023")), "blocked_subjects": ["English"],
                "issues": [{"issue_id": "I-1", "severity": "blocking", "answered": None,
                            "subject": "English", "question": "Was English not assessed?"}]}
    server = FakeServer(MDEIS_LISTING, {"ingest_assessments": blocking})
    model = FakeModel(ask("ingest_assessments", {"learner_id": "L-1"}))
    answer = final(run_graph([{"role": "user", "content": "How is L-1 in English?"}],
                             model, server))["answer"]
    assert "[I-1]" in answer and "Sources" not in answer


def test_a_file_name_with_markdown_characters_cannot_change_the_line_around_it():
    odd = sourced(("a*b_c [x](y).pdf", "2022/2023"))
    server = FakeServer(MDEIS_LISTING, {"compute_trend": odd})
    model = FakeModel(ask("compute_trend", {"learner_id": "L-1", "skill": "Mathematics"}),
                      say("Steady."))
    answer = final(run_loop(USER, model, server))["answer"]
    assert answer.endswith("*Sources (report cards): a\\*b\\_c \\[x\\]\\(y\\).pdf.*")


# --------------------------------------------------------------------------- the rulebook's words
RULES = [
    {"id": "ST-3", "text": "Form 3 literacy is expected between 12 and 16 out of 20."},
    {"id": "GM-1", "text": "Groups smaller than 10 are never shown."},
]
RULED = {"status": "ok", "stage": "Form 3", "counts": {"below_expected_range": 6}, "rules": RULES}
CLASS_WORDS = (
    "Six learners are below [ST-3]. Groups under the minimum are hidden [GM-1].\n\n"
    "Sources: [GM-1] [ST-3]"
)
LITERACY = {"learner_id": "L-1", "skill": "literacy"}


def test_rule_markers_become_the_rulebooks_own_words():
    for runner in (run_loop, run_graph):
        server = FakeServer(MDEIS_LISTING, {"compute_trend": RULED})
        model = FakeModel(ask("compute_trend", LITERACY), say(CLASS_WORDS))
        answer = final(runner(USER, model, server))["answer"]
        assert "[ST-3]" not in answer and "[GM-1]" not in answer and "Sources:" not in answer
        assert answer.startswith("Six learners are below. Groups under the minimum are hidden.")
        assert answer.endswith(
            "---\n\n*Rules applied: ST-3 — Form 3 literacy is expected between 12 and 16 "
            "out of 20; GM-1 — Groups smaller than 10 are never shown.*"
        ), runner.__name__


def test_files_and_rules_share_one_footer_under_one_rule():
    server = FakeServer(MDEIS_LISTING, {"compute_trend": {**TREND, **RULED}})
    model = FakeModel(ask("compute_trend", LITERACY), say("Steady."))
    answer = final(run_graph(USER, model, server))["answer"]
    assert answer.count("---") == 1
    assert "*Sources (report cards): a form1.pdf; b form2.pdf.*\n\n*Rules applied:" in answer


def test_without_the_rulebooks_words_the_markers_are_left_in_place():
    model = FakeModel(say("English is steady [ST-3]."))
    answer = final(run_graph(USER, model, FakeServer(MDEIS_LISTING)))["answer"]
    assert answer == "English is steady [ST-3]."


# --------------------------------------------------------------------------- an assumption is said
ASSUMING = {"status": "ok", "trend": "improving", "level": "17.00",
            "assumed": [{"what": "stage weight", "stage": "Form 5 Science", "used": "1.00"}]}


def test_an_assumed_weight_is_always_said_under_the_answer_whatever_the_model_wrote():
    for runner in (run_loop, run_graph):
        server = FakeServer(MDEIS_LISTING, {"compute_trend": ASSUMING})
        model = FakeModel(ask("compute_trend", {"learner_id": "L-1", "skill": "Mathematics"}),
                          say("Mathematics is improving."))
        answer = final(runner(USER, model, server))["answer"]
        assert "*Assumed: the rulebook sets no weight for Form 5 Science, so each term" in answer


def test_nothing_is_said_when_nothing_was_assumed():
    server = FakeServer(MDEIS_LISTING, {"compute_trend": {"status": "ok", "trend": "steady"}})
    model = FakeModel(ask("compute_trend", {"learner_id": "L-1", "skill": "Mathematics"}),
                      say("Steady."))
    assert "Assumed" not in final(run_graph(USER, model, server))["answer"]
