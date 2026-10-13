"""E-23: the chart tool (docs/step-21.md). It prepares what a chart shows from the same records and
rulebook as compute_trend, so the scores on a chart are the scores the trend was worked out from.

Runs on the practice cohort in the throwaway schema, like test_tools_contract.py."""

import importlib
import json

import psycopg
import pytest

pytestmark = pytest.mark.contract


def tool(name):
    """The tool function, or a plain failure saying it is not built yet."""
    try:
        return getattr(importlib.import_module("mdeis.tools"), name)
    except (ImportError, AttributeError) as e:
        pytest.fail(f"the tool {name!r} is not built yet ({type(e).__name__}: {e})", pytrace=False)


@pytest.fixture(scope="module")
def fx(files, key):
    """handle -> the fixture's row (with its DB learner id) and its expected results."""
    out = {}
    for f in json.loads(files["truth/fixtures.json"]):
        out[f["handle"]] = {**f, "expected": key["learners"][f["school_ref"]]["by_template"]}
    return out


def chart(ctx, learner, skills, version="v1"):
    return tool("make_chart")(ctx, learner_id=learner, skills=skills, template_version=version)


def test_a_subjects_chart_ends_with_the_scores_compute_trend_used(make_ctx, fx):
    learner = fx["FX-01"]["gen_id"]
    ctx = make_ctx()
    trend = tool("compute_trend")(ctx, learner_id=learner, skill="English", template_version="v1")
    drawn = chart(ctx, learner, ["English"])
    assert drawn["status"] == "ok" and drawn["template_version"] == "v1"
    (line,) = drawn["series"]
    window = trend["window"]
    assert line["values"][-len(window):] == [w["score"] for w in window]
    assert [ids[0] for ids in line["record_ids"][-len(window):]] == [w["record_id"] for w in window]
    assert set(trend["record_ids"]) <= set(drawn["record_ids"])
    assert drawn["scale_max"] == "20.00"


def test_an_areas_chart_has_the_range_the_trend_names(make_ctx, fx):
    learner = fx["FX-08"]["gen_id"]
    ctx = make_ctx()
    trend = tool("compute_trend")(ctx, learner_id=learner, skill="literacy", template_version="v1")
    drawn = chart(ctx, learner, ["literacy"])
    assert drawn["terms"][-1]["band"] == trend["stage_gap"]["range"]
    window = trend["window"]
    assert drawn["series"][0]["values"][-len(window):] == [w["skill_value"] for w in window]
    # the chart shows every term, so it names the range rule of each stage it draws; the trend names
    # only the stages of its window. The last stage's range rule is in both.
    assert drawn["band_clauses"][-1] in trend["clause_ids"]
    assert drawn["band_legend"].startswith("Expected range for the stage (")


def test_the_same_input_gives_the_same_chart_and_hash(make_ctx, fx):
    learner = fx["FX-01"]["gen_id"]
    first, second = (chart(make_ctx(), learner, ["English", "Mathematics"]) for _ in range(2))
    assert first == second and first["result_hash"]
    assert "RUN-" not in json.dumps(first)


def test_a_subject_with_an_open_problem_is_left_off_with_the_question_that_blocks_it(make_ctx, fx):
    learner = fx["FX-04a"]["gen_id"]
    ctx = make_ctx()
    tool("ingest_assessments")(ctx, learner_id=learner)
    drawn = chart(ctx, learner, ["English"])
    assert drawn["status"] == "not_drawn" and drawn["series"] == []
    (left_off,) = drawn["excluded"]
    assert left_off["reason"] == "blocked" and left_off["reasons"]
    assert left_off["reasons"][0]["question"]


def test_the_other_subjects_are_still_drawn_when_one_is_left_off(make_ctx, fx):
    learner = fx["FX-04a"]["gen_id"]
    ctx = make_ctx()
    tool("ingest_assessments")(ctx, learner_id=learner)
    drawn = chart(ctx, learner, ["English", "Mathematics"])
    assert [line["name"] for line in drawn["series"]] == ["Mathematics"]
    assert [item["skill"] for item in drawn["excluded"]] == ["English"]


def test_an_unknown_learner_name_or_nothing_asked_is_refused(make_ctx, fx):
    learner = fx["FX-01"]["gen_id"]
    assert chart(make_ctx(), "L-9999", ["English"])["status"] == "refused"
    assert chart(make_ctx(), learner, ["Astrology"])["status"] == "refused"
    assert chart(make_ctx(), learner, [])["status"] == "refused"
    too_many = [f"Subject {n}" for n in range(9)]
    assert chart(make_ctx(), learner, too_many)["status"] == "refused"


def test_the_call_is_in_the_diary_and_takes_no_approver(make_ctx, fx):
    import inspect

    assert "approver" not in inspect.signature(tool("make_chart")).parameters
    ctx = make_ctx()
    drawn = chart(ctx, fx["FX-01"]["gen_id"], ["English"])
    assert drawn["status"] == "ok"
    with psycopg.connect(ctx.database_url) as conn:
        lines = conn.execute(
            "SELECT tool, outcome FROM action_log WHERE run_id = %s ORDER BY event_id",
            (ctx.run_id,),
        ).fetchall()
    assert lines == [("make_chart", "ok")]
