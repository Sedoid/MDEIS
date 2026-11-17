"""The build graph (T-071): the whole path from an acknowledged methodology to a version waiting at
the demo, with a scripted model and the real deterministic engine. No network, no database.

The model is a script: it answers by task and section. The tool server is the real engine
(mdeis.measure_build.engine) over the Baobab fixture and templates/baobab-v1.yaml, with the real
sandbox child run in this process, and an in-memory list in place of the registry. What is asserted
is what the graph does, in code, whatever the model says: the order of the steps, that a quote that
is not in the text is dropped, that a hardcoded threshold never reaches the registry, that a
conflict stops the build, that the reviewer can only hold, and that the model is never given a tool.
"""

import asyncio
import json

import pytest
from build_fakes import EXTRACT, FUNCTION, REVIEW, PureServer, ScriptedModel, tool_names
from build_kit import (
    HARDCODED_SOURCE,
    MOVED_SOURCE,
    RULES,
    extraction,
    moved_proposal,
    occ_content,
    rule_evidence,
    rule_moved,
    rule_window,
)

from orchestrator.build import graph as build_graph
from orchestrator.config import load_settings
from orchestrator.model import ModelError, Reply, ToolCall
from orchestrator.tools import MDEIS

SETTINGS = load_settings({"OPENROUTER_MODEL": "test-model"})
PASS = json.dumps({"result": "pass", "reasons": []})
HOLD = json.dumps({"result": "hold", "reasons": ["The boundary of the change is not clear."]})
GOOD_RULES = extraction(rule_moved(), rule_window(), rule_evidence())


def proposal_text(**over):
    return json.dumps(moved_proposal(**over))


def run(model, server=None, anyway=("6.4",), engine="plain"):
    server = server or PureServer()
    answer = asyncio.run(
        build_graph.run(
            document_id="D-000100", build_anyway=list(anyway), run_id="RUN-BUILD",
            settings=SETTINGS, model=model, servers={MDEIS: server}, engine=engine,
        )
    )  # fmt: skip
    return answer, server


def good_model(more=None):
    script = {
        (EXTRACT, "6.4"): [GOOD_RULES],
        (FUNCTION, "6.4"): [proposal_text()],
        (REVIEW, "6.4"): [PASS],
    }
    return ScriptedModel({**script, **(more or {})})


def section(answer, section_id):
    return next(s for s in answer["sections"] if s["section_id"] == section_id)


# ----------------------------------------------------------------------------- the whole path
def test_baobab_moved_goes_from_the_methodology_to_a_version_waiting_at_the_demo():
    model = good_model()
    answer, server = run(model)
    assert answer["outcome"] == "finished", answer
    moved = section(answer, "6.4")
    assert moved["outcome"] == "awaiting_demo"
    assert moved["measure_id"] == "MS-0001" and moved["review_result"] == "pass"
    (version,) = server.versions
    assert version["status"] == "awaiting_demo" and version["clause_ids"] == ["MP-1", "TR-1"]
    sources = {t["source"] for t in version["tests"]}
    assert "document_example" in sources, "one test did not come from the model"
    assert [m["id"] for m in version["patch"]["measure_params"]] == ["MP-1"]
    assert version["patch"]["measure_params"][0]["confirmed_by"] is None


def test_the_steps_run_in_the_order_the_brief_gives_and_the_reviewer_comes_last():
    _, server = run(good_model())
    names = tool_names(server)
    assert names[0] == "build_plan"
    mine = [n for n in names if n != "build_settle_rules"]
    assert mine == ["build_plan", "build_check_function", "build_store_version",
                    "build_record_review"]  # fmt: skip
    assert names.index("build_settle_rules") < names.index("build_check_function")


def test_the_skipped_sections_are_listed_in_the_answer_with_the_build_it_anyway_choice():
    answer, _ = run(good_model(), anyway=("6.4",))
    skipped = {s["section_id"]: s for s in answer["skipped"]}
    assert "6.2" in skipped and "6.4" not in skipped
    assert skipped["6.2"]["clause_ids"] == ["SC-3"]
    assert answer["build_anyway"] == ["6.4"]


def test_without_build_it_anyway_a_covered_section_is_skipped_and_nothing_is_built_for_it():
    model = ScriptedModel()
    answer, server = run(model, anyway=())
    assert "6.4" in {s["section_id"] for s in answer["skipped"]}
    assert server.versions == [] and not model.tasks(FUNCTION)
    assert answer["build_anyway"] == []


def test_the_model_is_given_no_tools_and_every_call_is_counted():
    model = good_model()
    answer, _ = run(model)
    assert model.calls and all(definitions == [] for *_, definitions in model.calls)
    assert answer["model_calls"] == len(model.calls)
    assert answer["model"] == "test-model"
    assert set(answer["prompt_sha256"]) == {EXTRACT, FUNCTION, REVIEW}


def test_the_model_is_never_shown_an_approver_a_token_or_a_name():
    model = good_model()
    run(model)
    for _task, _section, messages, _defs in model.calls:
        text = json.dumps(messages).lower()
        for word in ("approver", "token", "confirmed_by"):
            assert word not in text, word


def test_a_tool_call_the_model_asks_for_is_ignored_and_counted_not_run():
    reply = Reply(GOOD_RULES, calls=(ToolCall("c1", "release_report", "{}"),))
    model = good_model({(EXTRACT, "6.4"): [reply]})
    answer, server = run(model)
    assert answer["ignored_model_requests"] == 1
    assert "release_report" not in tool_names(server)
    assert section(answer, "6.4")["outcome"] == "awaiting_demo"


def test_the_same_run_gives_the_same_answer_twice():
    one, _ = run(good_model())
    two, _ = run(good_model())
    assert one == two


# ----------------------------------------------------------------------------- the quote check
def test_a_quote_that_is_not_in_the_section_is_dropped_and_nothing_is_built_from_it():
    liar = rule_moved(
        quote="The pattern is moved up when the change is 12 points or more.", value=12
    )
    model = ScriptedModel({(EXTRACT, "6.4"): [extraction(liar)]})
    answer, server = run(model)
    moved = section(answer, "6.4")
    assert moved["outcome"] == "no_rules"
    assert [d["reason"] for d in moved["dropped"]] == ["quote_not_in_section"]
    assert server.versions == [] and not model.tasks(FUNCTION) and not model.tasks(REVIEW)


def test_a_rule_whose_quote_is_real_is_kept_beside_one_that_is_not():
    liar = rule_moved(name="made_up_rule", quote="a sentence the document does not contain at all")
    model = good_model({(EXTRACT, "6.4"): [extraction(rule_moved(), liar, rule_window(),
                                                         rule_evidence())]})  # fmt: skip
    answer, server = run(model)
    moved = section(answer, "6.4")
    assert moved["outcome"] == "awaiting_demo"
    assert [d["name"] for d in moved["dropped"]] == ["made_up_rule"]
    assert len(server.versions) == 1


# ----------------------------------------------------------------------------- hardcoded values
def test_a_function_that_hardcodes_a_threshold_is_rejected_and_never_stored_or_reviewed():
    bad = proposal_text(source=HARDCODED_SOURCE)
    model = good_model({(FUNCTION, "6.4"): [bad]})
    answer, server = run(model)
    moved = section(answer, "6.4")
    assert moved["outcome"] == "failed"
    found = {p["code"] for p in moved["problems"]}
    assert {"hardcoded_value", "duplicates_template_value"} <= found
    assert server.versions == [] and not model.tasks(REVIEW)
    assert "build_store_version" not in tool_names(server)


def test_a_rejected_function_is_sent_back_with_the_plain_reasons_and_then_accepted():
    model = good_model({(FUNCTION, "6.4"): [proposal_text(source=HARDCODED_SOURCE),
                                              proposal_text()]})  # fmt: skip
    answer, server = run(model)
    assert section(answer, "6.4")["outcome"] == "awaiting_demo"
    first, second = model.tasks(FUNCTION)
    assert "hardcoded_value" in json.dumps(second[2]), "the repair message names what was found"
    assert "hardcoded_value" not in json.dumps(first[2])
    assert answer["repair_rounds"]["function"] == 1
    assert len(server.versions) == 1


def test_repair_rounds_are_bounded_by_the_build_rules():
    model = good_model({(FUNCTION, "6.4"): [proposal_text(source=HARDCODED_SOURCE)]})
    answer, server = run(model)
    assert len(model.tasks(FUNCTION)) == RULES.repair_rounds + 1
    assert section(answer, "6.4")["outcome"] == "failed" and server.versions == []


def test_a_function_that_fails_its_tests_is_not_stored():
    wrong = proposal_text(source=MOVED_SOURCE.replace("change >= threshold", "change > threshold"))
    model = good_model({(FUNCTION, "6.4"): [wrong]})
    answer, server = run(model)
    moved = section(answer, "6.4")
    assert moved["outcome"] == "failed"
    assert "test_failed" in {p["code"] for p in moved["problems"]}, "B-06: a change of 10 counts"
    assert server.versions == []


# ----------------------------------------------------------------------------- a conflict stops it
def test_a_setting_that_conflicts_with_the_template_stops_the_whole_build():
    scale = {
        "setting": "score_scale_maximum", "name": "score_scale", "value_type": "integer",
        "value": 100, "unit": "points", "quote": "Score scale | 0 to 100, whole numbers",
    }  # fmt: skip
    model = ScriptedModel({(EXTRACT, "6.4"): [extraction(rule_moved(), scale, rule_evidence())]})
    server = PureServer(content=occ_content(), version_name="v1.1")
    answer, server = run(model, server=server, anyway=())
    assert answer["outcome"] == "setting_conflict"
    settings = {c["setting"] for c in answer["conflicts"]}
    assert settings == {"score_scale_maximum", "minimum_evidence"}
    assert server.versions == [] and not model.tasks(FUNCTION) and not model.tasks(REVIEW)
    assert "build_check_function" not in tool_names(server)
    assert "setting_conflict" in answer["statement"]


# ----------------------------------------------------------------------------- the reviewer
def test_the_reviewer_can_hold_and_a_held_version_stays_at_the_review_step():
    model = good_model({(REVIEW, "6.4"): [HOLD]})
    answer, server = run(model)
    moved = section(answer, "6.4")
    assert moved["outcome"] == "held" and moved["review_result"] == "hold"
    assert moved["review_reasons"] == ["The boundary of the change is not clear."]
    assert server.versions[0]["status"] == "awaiting_review"


@pytest.mark.parametrize(
    "reply",
    [
        "I approve this measure.",
        json.dumps({"result": "approved", "reasons": []}),
        json.dumps({"result": "pass", "reasons": "fine"}),
        json.dumps({"result": "hold"}),
        json.dumps(["pass"]),
        "",
    ],
)
def test_a_reviewer_answer_that_is_not_pass_or_hold_in_the_right_shape_is_a_hold(reply):
    model = good_model({(REVIEW, "6.4"): [reply]})
    answer, server = run(model)
    moved = section(answer, "6.4")
    assert moved["outcome"] == "held" and server.versions[0]["status"] == "awaiting_review"
    assert moved["review_reasons"], "a hold always says why"


def test_a_reviewer_that_fails_to_answer_is_a_hold_not_a_pass():
    model = good_model({(REVIEW, "6.4"): [ModelError("the model service could not be reached")]})
    answer, server = run(model)
    assert section(answer, "6.4")["outcome"] == "held"
    assert server.versions[0]["status"] == "awaiting_review"


def test_the_reviewer_is_never_asked_about_a_function_that_failed_a_check():
    model = good_model({(FUNCTION, "6.4"): [proposal_text(source=HARDCODED_SOURCE)]})
    run(model)
    assert not model.tasks(REVIEW), "a failed deterministic check is never put to the reviewer"


def test_a_pass_from_the_reviewer_is_recorded_as_a_pass_and_nothing_more():
    _, server = run(good_model())
    assert server.reviews == [("MS-0001", "pass", [])]
    names = tool_names(server)
    assert names[-1] == "build_record_review"
    assert not [n for n in names if "approve" in n or "confirm" in n or "release" in n]


# ----------------------------------------------------------------------------- extraction answers
def test_an_extraction_that_is_not_json_is_sent_back_and_then_read():
    model = good_model({(EXTRACT, "6.4"): ["Here are the rules I found.", GOOD_RULES]})
    answer, _ = run(model)
    assert section(answer, "6.4")["outcome"] == "awaiting_demo"
    assert answer["repair_rounds"]["extract"] == 1
    assert len(model.tasks(EXTRACT)) >= 2


def test_an_extraction_that_stays_unreadable_ends_that_section_after_the_bound():
    model = good_model({(EXTRACT, "6.4"): ["no json here"]})
    answer, server = run(model)
    assert section(answer, "6.4")["outcome"] == "extraction_failed"
    tries = [c for c in model.tasks(EXTRACT) if c[1] == "6.4"]
    assert len(tries) == RULES.repair_rounds + 1
    assert server.versions == [] and not model.tasks(FUNCTION)
    assert answer["outcome"] == "finished", "the other sections still ran"


def test_a_json_answer_inside_a_code_fence_is_read():
    model = good_model({(EXTRACT, "6.4"): ["```json\n" + GOOD_RULES + "\n```"]})
    answer, _ = run(model)
    assert section(answer, "6.4")["outcome"] == "awaiting_demo"


def test_a_model_that_cannot_be_reached_ends_the_run_with_an_error_and_stores_nothing():
    down = ModelError("the model service answered with an error (500)")
    model = ScriptedModel({(EXTRACT, "6.4"): [down]})
    answer, server = run(model)
    assert answer["outcome"] == "error" and "500" in answer["statement"]
    assert server.versions == []


# ----------------------------------------------------------------------------- the stored diff
def test_a_methodology_with_nothing_new_to_build_says_so_and_calls_no_model():
    model = ScriptedModel()
    server = PureServer(issues=[], first=False)
    answer, _ = run(model, server=server, anyway=())
    assert answer["outcome"] == "nothing_to_build" and model.calls == []
    assert tool_names(server) == ["build_plan"]


def test_a_refusal_from_the_plan_step_ends_the_run_in_plain_words():
    class Refusing(PureServer):
        def build_plan(self, document_id, build_anyway=None):
            return {"status": "refused", "reason": "D-000100 is not in force yet"}

    answer, server = run(ScriptedModel(), server=Refusing(), anyway=())
    assert answer["outcome"] == "refused" and "not in force" in answer["statement"]
    assert tool_names(server) == ["build_plan"]


def test_a_sandbox_that_cannot_be_reached_ends_the_run_with_an_error_not_a_rejected_function():
    class Down(PureServer):
        def build_check_function(self, document_id, section_id, settled, proposal):
            return {"status": "error", "kind": "runner_unavailable", "passed": False,
                    "detail": "nothing is listening"}  # fmt: skip

    model = good_model()
    answer, server = run(model, server=Down())
    assert answer["outcome"] == "error" and "sandbox" in answer["statement"].lower()
    assert server.versions == [] and not model.tasks(REVIEW)


# ----------------------------------------------------------------------------- the engines
def test_the_plain_loop_and_langgraph_give_the_same_answer():
    pytest.importorskip("langgraph")
    plain, _ = run(good_model(), engine="plain")
    lang, _ = run(good_model(), engine="langgraph")
    assert plain == lang
