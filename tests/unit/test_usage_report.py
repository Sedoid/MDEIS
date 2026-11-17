"""scripts/usage_report.py: the numbers, worked out by hand from a small usage file.

The sample: two chat runs in one conversation (the second reports no cost), a third chat run in that
conversation after the report was released, one lesson run, and one run older than the window. The
clock is fixed with --now, so the window is 2026-11-16 12:00Z to 2026-11-17 12:00Z.

By hand, runs in the window (A, B, D, L):
    model calls   3, 2, 1, 4   sum 10   average 2.5   min 1   max 4
    tokens        6400, 3100, 1000, 8000   sum 18500   average 4625   min 1000   max 8000
    cost USD      0.004, none, 0.0005, 0.006   sum 0.0105   average over the three 0.0035
    seconds       12, 5, 2.2, 20   average 9.8
Report R-000001 counts A and B (D started after the release): 5 calls, 9500 tokens, 0.004
(lower bound).
Lesson plan LP-000001 counts L: 4 calls, 8000 tokens, 0.006.
"""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "usage_report.py"
NOW = "2026-11-17T12:00:00Z"


def line(run_id, kind, conversation, started, ended, calls, tools, tokens_in, tokens_out, cost,
         reported, latency_ms):
    return json.dumps({
        "v": 1, "run_id": run_id, "conversation_id": conversation, "kind": kind,
        "started_at": started, "ended_at": ended, "latency_ms": latency_ms, "outcome": "answered",
        "user_id": "u-ada", "model": "vendor/model-x", "provider": "ProviderY",
        "model_calls": calls, "tool_calls": tools, "input_tokens": tokens_in,
        "output_tokens": tokens_out, "total_tokens": tokens_in + tokens_out, "cost_usd": cost,
        "cost_reported_calls": reported, "repair_rounds": None,
        "calls": [], "tools": [],
    })


USAGE = [
    line("RUN-aaaaaaaa-0000", "chat", "C-1", "2026-11-17T09:00:00Z", "2026-11-17T09:00:12Z",
         3, 4, 6000, 400, 0.004, 3, 12000),
    line("RUN-bbbbbbbb-0000", "chat", "C-1", "2026-11-17T09:05:00Z", "2026-11-17T09:05:05Z",
         2, 1, 3000, 100, None, 0, 5000),
    line("RUN-dddddddd-0000", "chat", "C-1", "2026-11-17T11:00:00Z", "2026-11-17T11:00:02Z",
         1, 0, 900, 100, 0.0005, 1, 2200),
    line("RUN-llllllll-0000", "lesson", None, "2026-11-17T10:00:00Z", "2026-11-17T10:00:20Z",
         4, 3, 7000, 1000, 0.006, 4, 20000),
    line("RUN-oooooooo-0000", "chat", "C-0", "2026-11-10T09:00:00Z", "2026-11-10T09:00:01Z",
         1, 0, 10, 10, 0.1, 1, 1000),
]

OUTCOMES = [
    {"outcome_id": "R-000001", "kind": "report", "run_id": "RUN-aaaaaaaa-0000",
     "conversation_id": None, "completed_at": "2026-11-17T09:30:00Z", "who": "Ada Teacher"},
    {"outcome_id": "R-000002", "kind": "report", "run_id": "RUN-unknown-0000",
     "conversation_id": None, "completed_at": "2026-11-17T09:50:00Z", "who": "Ada Teacher"},
    {"outcome_id": "LP-000001", "kind": "lesson_plan", "run_id": "RUN-llllllll-0000",
     "conversation_id": None, "completed_at": "2026-11-17T10:10:00Z", "who": "Ada Teacher"},
]


def run_script(*args, stdin=None):
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args, "--now", NOW],
                          input=stdin, capture_output=True, text=True)


def files(tmp_path, usage=None, outcomes=None):
    usage_file = tmp_path / "usage.jsonl"
    usage_file.write_text("\n".join(USAGE if usage is None else usage) + "\n")
    outcomes_file = tmp_path / "outcomes.json"
    outcomes_file.write_text(json.dumps(OUTCOMES if outcomes is None else outcomes))
    return usage_file, outcomes_file


def test_the_window_keeps_four_runs_and_leaves_out_the_old_one(tmp_path):
    usage_file, _ = files(tmp_path)
    result = run_script(str(usage_file))
    assert result.returncode == 0, result.stderr
    assert "All runs: 4 run(s)" in result.stdout
    assert "aaaaaaaa" in result.stdout and "oooooooo" not in result.stdout
    assert "vendor/model-x" in result.stdout


def test_summary_numbers_for_all_runs(tmp_path):
    usage_file, _ = files(tmp_path)
    out = run_script(str(usage_file), "--format", "json").stdout
    overall = json.loads(out)["overall"]
    assert overall["runs"] == 4
    assert overall["model_calls"] == {"n": 4, "avg": 2.5, "min": 1, "max": 4, "sum": 10}
    assert overall["total_tokens"]["sum"] == 18500
    assert overall["total_tokens"]["avg"] == 4625
    assert (overall["total_tokens"]["min"], overall["total_tokens"]["max"]) == (1000, 8000)
    assert overall["cost_usd"]["sum"] == 0.0105
    assert overall["cost_usd"]["avg"] == 0.0035
    assert (overall["cost_usd"]["min"], overall["cost_usd"]["max"]) == (0.0005, 0.006)
    assert overall["runs_with_cost"] == 3 and overall["runs_with_incomplete_cost"] == 1
    assert round(overall["latency_s"]["avg"], 6) == 9.8
    assert overall["latency_s"]["max"] == 20


def test_summary_per_kind(tmp_path):
    usage_file, _ = files(tmp_path)
    data = json.loads(run_script(str(usage_file), "--format", "json").stdout)
    chat, lesson = data["by_kind"]["chat"], data["by_kind"]["lesson"]
    assert chat["runs"] == 3 and chat["model_calls"]["avg"] == 2.0
    assert chat["model_calls"]["max"] == 3
    assert chat["total_tokens"]["sum"] == 10500 and chat["total_tokens"]["avg"] == 3500
    assert chat["cost_usd"]["sum"] == 0.0045
    assert lesson["runs"] == 1 and lesson["cost_usd"]["sum"] == 0.006


def test_text_says_totals_are_a_lower_bound_when_a_cost_is_missing(tmp_path):
    usage_file, _ = files(tmp_path)
    text = run_script(str(usage_file)).stdout
    assert "Some runs reported no cost, so the cost totals are a lower bound." in text
    assert "total cost: $0.010500" in text
    assert "1 of 4 run(s) had a cost reported for fewer calls than they made, or none." in text


def test_no_lower_bound_wording_when_every_cost_is_reported(tmp_path):
    usage_file, _ = files(tmp_path, usage=[USAGE[0], USAGE[3]])
    text = run_script(str(usage_file)).stdout
    assert "lower bound" not in text
    assert "total cost: $0.010000" in text


def test_outcome_working_and_averages(tmp_path):
    usage_file, outcomes_file = files(tmp_path)
    text = run_script(str(usage_file), "--outcomes", str(outcomes_file)).stdout
    # The report counts both runs up to its release, and not the later one.
    report = text.split("R-000001")[1].split("R-000002")[0]
    assert "aaaaaaaa" in report and "bbbbbbbb" in report and "dddddddd" not in report
    assert "sum: 2 run(s), 5 model calls, 9,500 tokens, $0.004000" in report
    assert "lower bound: cost not reported for every call" in report
    plan = text.split("LP-000001")[1].split("lesson plans:")[0]
    assert "sum: 1 run(s), 4 model calls, 8,000 tokens, $0.006000" in plan
    data = json.loads(run_script(str(usage_file), "--outcomes", str(outcomes_file),
                                 "--format", "json").stdout)
    both = data["outcome_summary"]
    assert both["report"]["with_usage"] == 1 and both["report"]["no_usage_recorded"] == 1
    assert both["report"]["cost_usd"]["avg"] == 0.004
    assert both["lesson_plan"]["model_calls"]["max"] == 4
    assert "all outcomes: 3 completed, 2 with usage recorded" in text
    # Average over the two outcomes with usage: calls (5 + 4) / 2, worst 5;
    # cost (0.004 + 0.006) / 2.
    all_text = text.split("all outcomes:")[1]
    assert "4.5" in all_text and "$0.005000" in all_text


def test_an_outcome_without_usage_is_never_zero(tmp_path):
    usage_file, outcomes_file = files(tmp_path)
    text = run_script(str(usage_file), "--outcomes", str(outcomes_file)).stdout
    block = text.split("R-000002")[1].split("LP-000001")[0]
    assert "no usage recorded" in block
    assert "$0" not in block and "0 model calls" not in block


def test_no_outcomes_prints_only_the_runs_and_the_summary(tmp_path):
    usage_file, _ = files(tmp_path)
    text = run_script(str(usage_file)).stdout
    assert "PER RUN" in text and "SUMMARY" in text
    assert "COST PER COMPLETED OUTCOME" not in text
    empty = run_script(str(usage_file), "--outcomes", str(files(tmp_path, outcomes=[])[1])).stdout
    assert "no report was released and no lesson plan approved" in empty


def test_a_malformed_line_is_counted_and_skipped(tmp_path):
    usage = [USAGE[0], "this is not json", '{"v": 2, "run_id": "RUN-x"}', USAGE[3]]
    usage_file, _ = files(tmp_path, usage=usage)
    result = run_script(str(usage_file))
    assert result.returncode == 0
    assert "2 malformed line(s) in the usage file were skipped." in result.stdout
    assert "warning: 2 malformed" in result.stderr
    assert "All runs: 2 run(s)" in result.stdout


def test_a_file_with_only_malformed_lines_fails_with_a_message(tmp_path):
    usage_file, _ = files(tmp_path, usage=["nonsense", "{}"])
    result = run_script(str(usage_file))
    assert result.returncode == 2
    assert "no readable line" in result.stderr


def test_a_missing_file_and_a_bad_outcomes_file_fail(tmp_path):
    missing = run_script(str(tmp_path / "nope.jsonl"))
    assert missing.returncode == 2 and "cannot read the usage file" in missing.stderr
    usage_file, outcomes_file = files(tmp_path)
    outcomes_file.write_text('{"not": "an array"}')
    bad = run_script(str(usage_file), "--outcomes", str(outcomes_file))
    assert bad.returncode == 2 and "must be a JSON array" in bad.stderr


def test_an_empty_file_is_not_an_error(tmp_path):
    empty = tmp_path / "usage.jsonl"
    empty.write_text("")
    result = run_script(str(empty))
    assert result.returncode == 0 and "No runs in this window." in result.stdout


def test_reads_stdin_and_ends_with_the_reconcile_line():
    result = run_script("-", stdin="\n".join(USAGE) + "\n")
    assert result.returncode == 0
    last = result.stdout.strip().splitlines()[-1]
    assert last == (
        "Reconcile: the total cost above should match the OpenRouter activity page for the same "
        "period (https://openrouter.ai/activity), within rounding. Per answer is not per outcome.")


def test_since_accepts_a_date_and_prints_the_cutoff():
    cutoff = run_script("--print-cutoff", "--since", "2026-11-01")
    assert cutoff.stdout.strip() == "2026-11-01T00:00:00Z"
    assert run_script("--print-cutoff", "--since", "7d").stdout.strip() == "2026-11-10T12:00:00Z"
    assert run_script("--print-cutoff", "--since", "24h").stdout.strip() == "2026-11-16T12:00:00Z"
    bad = run_script("--print-cutoff", "--since", "yesterday")
    assert bad.returncode == 2 and "is not 24h, 7d or YYYY-MM-DD" in bad.stderr
