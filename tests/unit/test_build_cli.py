"""The command `python -m orchestrator.build_cli` (T-071): arguments, the exit code and the shape of
what it prints. The server and the model are fakes; nothing is called over a network."""

import json
from contextlib import asynccontextmanager

import pytest
from build_fakes import EXTRACT, FUNCTION, REVIEW, PureServer, ScriptedModel
from build_kit import extraction, moved_proposal, rule_evidence, rule_moved, rule_window

from orchestrator import build_cli as cli

ENV = {"OPENROUTER_MODEL": "test-model", "OPENROUTER_API_KEY": "not-a-real-key"}


def opener_for(server):
    seen = {}

    @asynccontextmanager
    async def opener(settings, run_id):
        seen["run_id"] = run_id
        yield server

    return opener, seen


def good_model():
    return ScriptedModel({
        (EXTRACT, "6.4"): [extraction(rule_moved(), rule_window(), rule_evidence())],
        (FUNCTION, "6.4"): [json.dumps(moved_proposal())],
        (REVIEW, "6.4"): [json.dumps({"result": "pass", "reasons": []})],
    })  # fmt: skip


def test_it_runs_the_graph_and_prints_the_answer_as_json(capsys):
    server = PureServer()
    opener, seen = opener_for(server)
    code = cli.main(["D-000100", "--build-anyway", "6.4", "--engine", "plain"], env=ENV,
                    model=good_model(), opener=opener)  # fmt: skip
    answer = json.loads(capsys.readouterr().out)
    assert code == 0 and answer["outcome"] == "finished"
    assert answer["build_anyway"] == ["6.4"] and answer["run_id"] == seen["run_id"]
    assert seen["run_id"].startswith("BUILD-")
    assert [v["status"] for v in server.versions] == ["awaiting_demo"]


def test_a_build_that_stops_on_a_conflict_exits_with_one(capsys):
    from build_kit import occ_content

    scale = {
        "setting": "score_scale_maximum", "name": "score_scale", "value_type": "integer",
        "value": 100, "unit": "points", "quote": "Score scale | 0 to 100, whole numbers",
    }  # fmt: skip
    model = ScriptedModel({(EXTRACT, "6.4"): [extraction(rule_moved(), scale)]})
    opener, _ = opener_for(PureServer(content=occ_content(), version_name="v1.1"))
    code = cli.main(["D-000100"], env=ENV, model=model, opener=opener)
    assert code == 1 and json.loads(capsys.readouterr().out)["outcome"] == "setting_conflict"


def test_without_a_model_it_refuses_before_opening_the_server(capsys):
    def never(settings, run_id):
        raise AssertionError("the server must not be opened")

    code = cli.main(["D-000100"], env={}, opener=never)
    assert code == 1 and "OPENROUTER" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [[], ["D-000100", "--engine", "other"]])
def test_the_arguments_are_checked(argv):
    with pytest.raises(SystemExit):
        cli.main(argv, env=ENV)


def test_the_command_has_no_argument_that_names_a_person_or_a_token():
    import inspect

    source = inspect.getsource(cli)
    for word in ("--approver", "--token", "--user", "--by", "--teacher", "X-Approver"):
        assert word not in source, word
