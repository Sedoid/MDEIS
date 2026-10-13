"""The agent graph: the ask-and-resolve flow, the citation check, the redraft limit and the release
guard, with a scripted fake model and fake tool servers (no network, no key).

Every test runs the plain engine, which uses the same nodes and routes as LangGraph; the last test
runs the real LangGraph and checks it gives the same events (skipped where it is not installed).
"""

import asyncio
import json

import pytest
from orchestrator_fakes import MDEIS_LISTING, FakeModel, FakeServer, ask, say

from orchestrator import graph, tools
from orchestrator.config import load_settings
from orchestrator.model import ModelError

TOKEN = "signed.token.value"
ENV = {"ORCHESTRATOR_API_KEY": "k", "OPENROUTER_API_KEY": "x", "OPENROUTER_MODEL": "m"}
SETTINGS = load_settings({**ENV, "ORCHESTRATOR_MODE": "graph"})
BLOCKING = {
    "issues": [
        {
            "issue_id": "I-000045",
            "severity": "blocking",
            "answered": None,
            "subject": "English",
            "question": "Was English not assessed in term 2, or is it missing from the card?",
            "possible_answers": ["confirm_absent", "use_value"],
        }
    ],
    "blocked_subjects": ["English"],
}
CLEAN = {"issues": [], "blocked_subjects": []}
DRAFTED = {"status": "pending_review", "report_id": "R-000012"}
FAILED = {"status": "fail", "problems": [{"reason": "unknown_number", "detail": "2.50"}]}
PASSED = {"status": "ok", "problems": []}
DRAFT_ARGS = {"learner_id": "L-1", "text": "x [A-000001].", "finding_hashes": ["h"]}


def run(messages, model, server, settings=SETTINGS, engine="plain", token=TOKEN):
    async def collect():
        return [
            e
            async for e in graph.run(
                messages=messages,
                token=token,
                run_id="RUN-1",
                settings=settings,
                model=model,
                servers={tools.MDEIS: server},
                engine=engine,
            )
        ]

    return asyncio.run(collect())


def user(text):
    return [{"role": "user", "content": text}]


def final(events):
    return next(e["data"] for e in events if e["event"] == "final")


def names(server):
    return [name for name, _ in server.calls]


# --------------------------------------------------------------------------- ask and resolve
def test_a_blocking_problem_ends_the_turn_with_a_plain_question():
    server = FakeServer(MDEIS_LISTING, {"ingest_assessments": BLOCKING})
    model = FakeModel(ask("ingest_assessments", {"learner_id": "L-1"}))
    events = run(user("How is L-1 doing in English?"), model, server)
    answer = final(events)
    assert answer["needs_answer"] is True and "I-000045" in answer["answer"]
    assert "Was English not assessed in term 2" in answer["answer"]
    assert "confirm_absent or use_value" in answer["answer"]
    assert len(model.seen) == 1, "the model was not asked to word the question"
    assert names(server) == ["ingest_assessments"]


def test_the_next_message_resumes_and_the_same_problem_is_not_asked_twice():
    history = [
        *user("How is L-1 doing in English?"),
        {"role": "assistant", "content": graph.ask_text(BLOCKING["issues"])},
        {"role": "user", "content": "It was not assessed that term. Leave it out."},
    ]
    server = FakeServer(
        MDEIS_LISTING,
        {"ingest_assessments": BLOCKING, "resolve_issue": {"status": "resolved"}},
    )
    model = FakeModel(
        ask("ingest_assessments", {"learner_id": "L-1"}),
        ask("resolve_issue", {"issue_id": "I-000045", "resolution": "confirm_absent"}, id="c2"),
        say("Recorded. English is worked out from the other terms."),
    )
    events = run(history, model, server)
    assert "needs_answer" not in final(events), "not asked again"
    assert names(server) == ["ingest_assessments", "resolve_issue"]
    assert final(events)["answer"].startswith("Recorded.")


def test_a_blocked_subject_found_by_compute_trend_is_asked_about_too():
    blocked = {
        "status": "blocked",
        "reasons": [
            {"issue_id": "I-000046", "kind": "missing_term", "subject": "Chemistry",
             "question": "Is the Chemistry score for term 1 missing from the card?"}
        ],
    }  # fmt: skip
    server = FakeServer(MDEIS_LISTING, {"compute_trend": blocked})
    model = FakeModel(ask("compute_trend", {"learner_id": "L-1", "skill": "Chemistry"}))
    answer = final(run(user("Chemistry trend for L-1"), model, server))
    assert answer["needs_answer"] is True and "I-000046" in answer["answer"]


def test_a_clean_learner_goes_straight_on_without_a_question():
    server = FakeServer(MDEIS_LISTING, {"ingest_assessments": CLEAN})
    model = FakeModel(ask("ingest_assessments", {"learner_id": "L-1"}), say("Nothing is wrong."))
    assert "needs_answer" not in final(run(user("Check L-1"), model, server))


# ------------------------------------------------------------------------- the draft and the check
def test_the_check_runs_after_every_draft_without_the_model_asking_for_it():
    server = FakeServer(MDEIS_LISTING, {"draft_summary": DRAFTED, "check_citations": PASSED})
    model = FakeModel(
        ask("draft_summary", DRAFT_ARGS),
        say("Here is the draft: English is steady."),
    )
    events = run(user("Write the report for L-1"), model, server)
    assert names(server) == ["draft_summary", "check_citations"]
    assert server.calls[1][1] == {"report_id": "R-000012"}
    answer = final(events)["answer"]
    assert answer.startswith("Here is the draft")
    assert "word for word" in answer and "> x [A-000001]." in answer, "the saved text is shown"
    assert "The draft R-000012 has passed the citation check" in answer
    assert "waiting for your release" in answer
    assert "Nothing has been released" in answer
    assert model.seen[1][1] == [], "once it has passed, the model is given no tools"
    assert "release_report" not in final(events)["tools"]


def test_a_failed_check_sends_the_problems_back_to_the_model_at_most_twice():
    server = FakeServer(
        MDEIS_LISTING,
        {"draft_summary": DRAFTED, "check_citations": [FAILED, FAILED, FAILED]},
    )
    model = FakeModel(
        ask("draft_summary", DRAFT_ARGS, id="d1"),
        ask("draft_summary", DRAFT_ARGS, id="d2"),
        ask("draft_summary", DRAFT_ARGS, id="d3"),
        say("this must never be reached"),
    )
    events = run(user("Write the report for L-1"), model, server)
    assert names(server).count("draft_summary") == 3, "the first draft and two redrafts"
    assert names(server).count("check_citations") == 3
    answer = final(events)
    assert answer["incomplete"] is True and "cannot be released" in answer["answer"]
    assert "unknown_number" in answer["answer"] and "R-000012" in answer["answer"]
    assert len(model.seen) == 3 and model.script, "the fourth model call never happened"
    told = json.dumps(model.seen[1][0][-1])
    assert "unknown_number" in told and "Fix exactly these problems" in told


def test_a_redraft_that_passes_ends_with_the_draft_waiting_for_the_approver():
    server = FakeServer(
        MDEIS_LISTING, {"draft_summary": DRAFTED, "check_citations": [FAILED, PASSED]}
    )
    model = FakeModel(
        ask("draft_summary", DRAFT_ARGS, id="d1"),
        ask("draft_summary", DRAFT_ARGS, id="d2"),
        say("The corrected draft is ready."),
    )
    answer = final(run(user("Write the report for L-1"), model, server))["answer"]
    assert "has passed the citation check" in answer


def test_the_redraft_limit_comes_from_the_environment():
    env = {**ENV, "ORCHESTRATOR_MODE": "graph", "ORCHESTRATOR_MAX_REDRAFTS": "0"}
    settings = load_settings(env)
    server = FakeServer(MDEIS_LISTING, {"draft_summary": DRAFTED, "check_citations": FAILED})
    model = FakeModel(ask("draft_summary", DRAFT_ARGS))
    answer = final(run(user("Write it"), model, server, settings))
    assert names(server) == ["draft_summary", "check_citations"] and answer["incomplete"] is True


# --------------------------------------------------------------------------- the release guard
def ready_history(report="R-000012"):
    return [
        *user("Write the report for L-1"),
        {"role": "assistant", "content": f"Draft...{graph.ready_footer(report)}"},
    ]


def offered(model, call=0):
    return {d["function"]["name"] for d in model.seen[call][1]}


def test_release_report_is_not_offered_before_a_checked_draft():
    model = FakeModel(say("hello"))
    run(user("Show me the rules"), model, FakeServer(MDEIS_LISTING))
    assert "release_report" not in offered(model) and "compute_trend" in offered(model)


def test_release_report_is_offered_only_after_a_checked_draft_was_shown_in_an_earlier_turn():
    model = FakeModel(say("hello"))
    history = [*ready_history(), {"role": "user", "content": "I have read it. Release it."}]
    run(history, model, FakeServer(MDEIS_LISTING))
    assert "release_report" in offered(model)


def test_only_the_draft_that_was_shown_can_be_released_and_only_when_asked():
    server = FakeServer(MDEIS_LISTING, {"release_report": {"status": "released"}})
    asked = [*ready_history(), {"role": "user", "content": "Release R-000012 please."}]
    model = FakeModel(ask("release_report", {"report_id": "R-000012"}), say("Done."))
    run(asked, model, server)
    assert server.calls == [("release_report", {"report_id": "R-000012"})]

    server = FakeServer(MDEIS_LISTING, {"release_report": {"status": "released"}})
    other = FakeModel(ask("release_report", {"report_id": "R-000099"}), say("No."))
    run(asked, other, server)
    assert server.calls == [], "a report that was never shown cannot be released"

    quiet = [*ready_history(), {"role": "user", "content": "Thanks, and what about French?"}]
    third = FakeModel(ask("release_report", {"report_id": "R-000012"}), say("No."))
    run(quiet, third, server)
    assert server.calls == [], "the approver did not ask for a release"


def test_a_draft_written_in_this_turn_cannot_be_released_in_it():
    server = FakeServer(MDEIS_LISTING, {"draft_summary": DRAFTED, "check_citations": PASSED})
    model = FakeModel(
        ask("draft_summary", DRAFT_ARGS),
        say("Here it is."),
    )
    events = run(user("Write the report and release it"), model, server)
    assert "release_report" not in names(server)
    assert "release_report" not in offered(model, 0), "not even offered in this turn"
    assert final(events)["answer"].endswith("Nothing has been released.")


# --------------------------------------------------------------------------- the other guards
def test_a_ranking_request_is_refused_at_the_first_node_with_no_model_call():
    model = FakeModel()
    events = run(user("Who is the best in the class?"), model, FakeServer(MDEIS_LISTING))
    assert model.seen == [] and final(events)["refused"] is True


def test_the_step_limit_ends_the_run_with_what_was_found_and_an_incomplete_mark():
    settings = load_settings({**ENV, "ORCHESTRATOR_MODE": "graph", "ORCHESTRATOR_MAX_STEPS": "2"})
    one, two = ask("get_criterion", {"query": "TR-1"}), ask("get_criterion", {"query": "TR-2"})
    model = FakeModel(one, two, say("The tools showed TR-1 and TR-2. I did not write a draft."))
    events = run(user("rules"), model, FakeServer(MDEIS_LISTING), settings)
    answer = final(events)
    assert answer["incomplete"] is True and "step limit" in answer["answer"]
    assert "The tools showed TR-1 and TR-2" in answer["answer"], "what was found is not thrown away"
    assert len(model.seen) == 3 and model.seen[2][1] == [], "the last call has no tools"
    assert "no steps left" in json.dumps(model.seen[2][0][-1])


def test_a_draft_that_passed_the_check_is_still_shown_when_the_step_limit_is_reached():
    settings = load_settings({**ENV, "ORCHESTRATOR_MODE": "graph", "ORCHESTRATOR_MAX_STEPS": "1"})
    server = FakeServer(MDEIS_LISTING, {"draft_summary": DRAFTED, "check_citations": PASSED})
    model = FakeModel(ask("draft_summary", DRAFT_ARGS), say("Here is the draft."))
    answer = final(run(user("Write it"), model, server, settings))
    assert "incomplete" not in answer and "step limit" not in answer["answer"]
    assert "has passed the citation check" in answer["answer"]
    assert "> x [A-000001]." in answer["answer"]


def test_a_model_failure_ends_with_an_error_event_and_no_final():
    model = FakeModel(ModelError("the model service could not be reached"))
    events = run(user("hi"), model, FakeServer(MDEIS_LISTING))
    assert [e["event"] for e in events][-1] == "error"
    assert "final" not in [e["event"] for e in events]


def test_the_events_come_in_order_status_then_tokens_then_final():
    long = " ".join(f"w{k}" for k in range(20))
    events = run(user("hello"), FakeModel(say(long)), FakeServer(MDEIS_LISTING))
    order = [e["event"] for e in events]
    assert order[0] == "status" and order[-1] == "final" and order.count("token") >= 2
    assert order.index("token") > max(i for i, n in enumerate(order) if n == "status")


# --------------------------------------------------------------------------- what the graph holds
def test_the_state_and_every_update_hold_no_approver_and_no_token():
    assert not {k for k in graph.State.__annotations__ if "token" in k or "approver" in k}
    keys, texts = [], []
    original = graph.build

    def spying(deps):
        machine = original(deps)
        for name, node in list(machine.nodes.items()):
            async def spy(state, node=node):
                update = await node(state)
                keys.extend(update)
                texts.append(json.dumps(update, default=str))
                return update

            machine.nodes[name] = spy
        return machine

    server = FakeServer(MDEIS_LISTING, {"draft_summary": DRAFTED, "check_citations": PASSED})
    messages = [{"role": "user", "content": f"my pass is {TOKEN}; write the report for L-1"}]
    model = FakeModel(ask("draft_summary", DRAFT_ARGS), say("Draft ready."))
    graph.build = spying
    try:
        events = run(messages, model, server)
    finally:
        graph.build = original
    assert keys, "the nodes ran"
    assert not [k for k in keys if "approver" in k or "token" in k], "no node writes an approver"
    assert all(TOKEN not in text for text in texts)
    assert TOKEN not in json.dumps(events) + json.dumps(model.seen)


def a_full_run(engine="plain"):
    """A learner with no problems, a draft that fails the check once, then a draft that passes."""
    results = {
        "ingest_assessments": CLEAN,
        "draft_summary": DRAFTED,
        "check_citations": [FAILED, PASSED],
    }
    server = FakeServer(MDEIS_LISTING, results)
    model = FakeModel(
        ask("ingest_assessments", {"learner_id": "L-1"}),
        ask("draft_summary", DRAFT_ARGS, id="d1"),
        ask("draft_summary", DRAFT_ARGS, id="d2"),
        say("Ready."),
    )
    events = run(user("Report for L-1"), model, server, engine=engine)
    return events, server.calls


def test_two_runs_of_the_same_script_make_the_same_tool_calls():
    first, second = a_full_run(), a_full_run()
    assert first == second
    called = [name for name, _ in first[1]]
    assert called == [
        "ingest_assessments",
        "draft_summary",
        "check_citations",
        "draft_summary",
        "check_citations",
    ]


# --------------------------------------------------------------------------- LangGraph itself
def test_langgraph_gives_the_same_events_as_the_plain_engine():
    pytest.importorskip("langgraph")

    assert a_full_run("langgraph") == a_full_run("plain")
    blocked = FakeServer(MDEIS_LISTING, {"ingest_assessments": BLOCKING})
    model = FakeModel(ask("ingest_assessments", {"learner_id": "L-1"}))
    asked = run(user("L-1?"), model, blocked, engine="langgraph")
    assert final(asked)["needs_answer"] is True
