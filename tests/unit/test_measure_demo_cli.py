"""The invitation to the teacher (T-073): when a build leaves a measure waiting at the demo, the
command that ran it says so, names the measure and says where to open it. Deterministic text made by
code from the build's own answer. No model writes it and nothing is sent to anyone.
"""

import json
from contextlib import asynccontextmanager

from build_fakes import EXTRACT, FUNCTION, REVIEW, PureServer, ScriptedModel
from build_kit import extraction, moved_proposal, rule_evidence, rule_moved, rule_window

from orchestrator import build_cli as cli

ENV = {"OPENROUTER_MODEL": "test-model", "OPENROUTER_API_KEY": "not-a-real-key"}


def waiting(**more):
    return {"section_id": "6.4", "outcome": "awaiting_demo", "measure_id": "MS-0001", "version": 1,
            "patch_id": "TP-000001", **more}


def test_a_measure_waiting_at_the_demo_is_named_with_where_to_open_it():
    answer = {"sections": [waiting()]}
    (line,) = cli.waiting_lines(answer)
    assert "MS-0001" in line and "version 1" in line and "6.4" in line
    assert "Methods" in line and "#/methods" in line
    assert "teacher" in line.lower()


def test_each_waiting_measure_gets_its_own_line_in_the_order_built():
    answer = {"sections": [waiting(), waiting(section_id="6.3", measure_id="MS-0002", version=2)]}
    lines = cli.waiting_lines(answer)
    found = [("MS-0001" in text, "MS-0002" in text) for text in lines]
    assert found == [(True, False), (False, True)]


def test_a_measure_held_by_the_automated_review_is_said_to_be_held_not_invited():
    answer = {"sections": [waiting(outcome="held", review_reasons=["the quote does not say 10"])]}
    (line,) = cli.waiting_lines(answer)
    assert "held" in line.lower() and "#/methods" not in line and "MS-0001" in line


def test_nothing_is_said_when_nothing_is_waiting_or_held():
    assert cli.waiting_lines({"sections": []}) == []
    assert cli.waiting_lines({"sections": [{"section_id": "6.4", "outcome": "failed"}]}) == []
    assert cli.waiting_lines({}) == []


def test_the_lines_use_no_number_but_the_ones_the_build_gave_and_no_label():
    answer = {"sections": [waiting()]}
    text = " ".join(cli.waiting_lines(answer)).lower()
    for word in ("weak", "remedial", "gifted", "rank", "best", "worst"):
        assert word not in text


def test_the_command_prints_the_json_on_stdout_unchanged_and_the_invitation_on_stderr(capsys):
    server = PureServer()

    @asynccontextmanager
    async def opener(settings, run_id):
        yield server

    model = ScriptedModel({
        (EXTRACT, "6.4"): [extraction(rule_moved(), rule_window(), rule_evidence())],
        (FUNCTION, "6.4"): [json.dumps(moved_proposal())],
        (REVIEW, "6.4"): [json.dumps({"result": "pass", "reasons": []})],
    })
    code = cli.main(["D-000100", "--build-anyway", "6.4", "--engine", "plain"], env=ENV,
                    model=model, opener=opener)
    seen = capsys.readouterr()
    answer = json.loads(seen.out)  # stdout is still one JSON document
    assert code == 0 and answer["outcome"] == "finished"
    assert "Methods" in seen.err and "#/methods" in seen.err
    entry = next(s for s in answer["sections"] if s.get("outcome") == "awaiting_demo")
    assert entry["measure_id"] in seen.err
