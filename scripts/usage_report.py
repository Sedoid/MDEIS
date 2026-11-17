#!/usr/bin/env python3
"""Cost and token report from the orchestrator's usage log. Standard library only.

    python3 scripts/usage_report.py usage.jsonl                      last 24 hours
    python3 scripts/usage_report.py usage.jsonl --since 7d
    python3 scripts/usage_report.py - --since 2026-11-01 < usage.jsonl
    python3 scripts/usage_report.py usage.jsonl --outcomes outcomes.json
    python3 scripts/usage_report.py usage.jsonl --format json
    python3 scripts/usage_report.py --print-cutoff --since 7d        the UTC start of the window, then stop

`make usage` runs this for you: it copies the file from the orchestrator and builds the outcomes
file from the database.

The usage file has one JSON line per finished run (schema v1, written by the orchestrator). It holds
numbers, the model name, tool names and run ids. It holds no prompt and no reply, and this script
prints nothing that is not in that schema.

The outcomes file is a JSON array of
    {"outcome_id", "kind": "report"|"lesson_plan", "run_id", "conversation_id" or null,
     "completed_at", "who"}
For a report, every chat run of the same conversation that started at or before completed_at is
counted (the run that drafted it is found by run_id, and its conversation_id comes from the usage
line, because the reports table does not store one). For a lesson plan, only the plan's run.

A malformed usage line is counted and skipped. A file that cannot be read, or one with no usable
line at all while it has malformed ones, is an error. Exit codes: 0 fine, 2 bad input.
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

KINDS = ("chat", "lesson", "build")
OUTCOME_KINDS = ("report", "lesson_plan")
LOWER_BOUND = "Some runs reported no cost, so the cost totals are a lower bound."
RECONCILE = (
    "Reconcile: the total cost above should match the OpenRouter activity page for the same period "
    "(https://openrouter.ai/activity), within rounding. Per answer is not per outcome."
)


class BadInput(Exception):
    pass


# ── Reading ──────────────────────────────────────────────────────────

def parse_time(text) -> datetime:
    if not isinstance(text, str):
        raise ValueError("not a string")
    parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_number(value) -> bool:
    return isinstance(value, (int, Decimal)) and not isinstance(value, bool)


def check_line(rec) -> dict:
    """Return the record with times parsed, or raise ValueError saying what is wrong."""
    if not isinstance(rec, dict):
        raise ValueError("not an object")
    if rec.get("v") != 1:
        raise ValueError("unsupported schema version")
    if not isinstance(rec.get("run_id"), str) or not rec["run_id"]:
        raise ValueError("no run_id")
    if rec.get("kind") not in KINDS:
        raise ValueError("unknown kind")
    for name in ("model_calls", "tool_calls", "input_tokens", "output_tokens", "total_tokens",
                 "cost_reported_calls"):
        if not is_int(rec.get(name)) or rec[name] < 0:
            raise ValueError(f"{name} is not a count")
    if not is_number(rec.get("latency_ms")):
        raise ValueError("latency_ms is not a number")
    if rec.get("cost_usd") is not None and not is_number(rec["cost_usd"]):
        raise ValueError("cost_usd is neither a number nor null")
    if rec["cost_usd"] is not None:
        rec["cost_usd"] = Decimal(rec["cost_usd"])
    rec["started"] = parse_time(rec.get("started_at"))
    rec["conversation_id"] = rec.get("conversation_id") or None
    return rec


def read_usage(path: str):
    """Return (records, malformed_count). Raises BadInput when the file cannot be used at all."""
    try:
        if path == "-":
            text = sys.stdin.read()
        else:
            with open(path, encoding="utf-8") as f:
                text = f.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise BadInput(f"cannot read the usage file {path!r}: {exc}")
    records, bad = [], 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            records.append(check_line(json.loads(line, parse_float=Decimal)))
        except (ValueError, TypeError, ArithmeticError):
            bad += 1
    if bad and not records:
        raise BadInput(f"the usage file {path!r} has no readable line: all {bad} line(s) are "
                       "malformed. Is it the usage log (one JSON line per run, schema v1)?")
    return records, bad


def read_outcomes(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as exc:
        raise BadInput(f"cannot read the outcomes file {path!r}: {exc}")
    if not isinstance(raw, list):
        raise BadInput(f"the outcomes file {path!r} must be a JSON array")
    out = []
    for i, item in enumerate(raw):
        try:
            if not isinstance(item, dict) or item.get("kind") not in OUTCOME_KINDS:
                raise ValueError("kind must be report or lesson_plan")
            if not isinstance(item.get("outcome_id"), str) or not item["outcome_id"]:
                raise ValueError("no outcome_id")
            item["completed"] = parse_time(item.get("completed_at"))
        except ValueError as exc:
            raise BadInput(f"the outcomes file {path!r}, item {i}: {exc}")
        out.append(item)
    return out


# ── The window ───────────────────────────────────────────────────────

def cutoff_for(since: str, now: datetime) -> datetime:
    match = re.fullmatch(r"(\d+)([hd])", since)
    if match:
        n = int(match.group(1))
        return now - (timedelta(hours=n) if match.group(2) == "h" else timedelta(days=n))
    try:
        return datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise BadInput(f"--since {since!r} is not 24h, 7d or YYYY-MM-DD")


# ── Arithmetic (exact, decimal, no model in the loop) ────────────────

def stats(values):
    """Count, average, min and max of a list of numbers; None for an empty list."""
    if not values:
        return None
    total = sum(values)
    return {"n": len(values), "avg": total / len(values), "min": min(values), "max": max(values),
            "sum": total}


def run_cost_complete(run) -> bool:
    return run["cost_usd"] is not None and run["cost_reported_calls"] >= run["model_calls"]


def summarise(runs):
    costs = [r["cost_usd"] for r in runs if r["cost_usd"] is not None]
    return {
        "runs": len(runs),
        "model_calls": stats([r["model_calls"] for r in runs]),
        "tool_calls": stats([r["tool_calls"] for r in runs]),
        "total_tokens": stats([r["total_tokens"] for r in runs]),
        "latency_s": stats([Decimal(r["latency_ms"]) / 1000 for r in runs]),
        "cost_usd": stats(costs),
        "runs_with_cost": len(costs),
        "runs_with_incomplete_cost": sum(1 for r in runs if not run_cost_complete(r)),
    }


def count_outcome_runs(outcome, runs):
    """The usage runs that count toward one outcome, oldest first."""
    own = [r for r in runs if r["run_id"] == outcome.get("run_id")]
    if outcome["kind"] == "lesson_plan":
        return own
    conversation = outcome.get("conversation_id") or (own[0]["conversation_id"] if own else None)
    if not conversation:
        return own
    chosen = {r["run_id"]: r for r in own}
    for r in runs:
        if r["conversation_id"] == conversation and r["started"] <= outcome["completed"]:
            chosen[r["run_id"]] = r
    return sorted(chosen.values(), key=lambda r: (r["started"], r["run_id"]))


def work_out(outcome, runs):
    counted = count_outcome_runs(outcome, runs)
    row = {"outcome_id": outcome["outcome_id"], "kind": outcome["kind"],
           "completed_at": outcome["completed"], "who": outcome.get("who") or "",
           "run_ids": [r["run_id"] for r in counted], "runs": counted}
    if counted:
        costs = [r["cost_usd"] for r in counted if r["cost_usd"] is not None]
        row.update(
            model_calls=sum(r["model_calls"] for r in counted),
            tokens=sum(r["total_tokens"] for r in counted),
            cost_usd=sum(costs) if costs else None,
            cost_lower_bound=any(not run_cost_complete(r) for r in counted),
        )
    return row


def outcome_summary(rows):
    have = [r for r in rows if r["runs"]]
    costed = [r["cost_usd"] for r in have if r["cost_usd"] is not None]
    return {
        "outcomes": len(rows),
        "with_usage": len(have),
        "no_usage_recorded": len(rows) - len(have),
        "with_incomplete_cost": sum(1 for r in have if r["cost_lower_bound"]),
        "cost_usd": stats(costed),
        "model_calls": stats([r["model_calls"] for r in have]),
        "tokens": stats([r["tokens"] for r in have]),
    }


# ── Printing ─────────────────────────────────────────────────────────

def usd(value) -> str:
    return "n/a" if value is None else f"${value:.6f}"


def num(value, places=1) -> str:
    return "-" if value is None else f"{value:,.{places}f}"


def when(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%SZ")


def short(run_id: str) -> str:
    return (run_id[4:] if run_id.startswith("RUN-") else run_id)[:8]


def table(headers, rows, right=()):
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    def fmt(row):
        return "  ".join(
            str(cell).rjust(w) if i in right else str(cell).ljust(w)
            for i, (cell, w) in enumerate(zip(row, widths))).rstrip()
    return [fmt(headers), fmt(["-" * w for w in widths]), *[fmt(r) for r in rows]]


def print_runs(runs):
    print("PER RUN")
    rows = [[when(r["started"]), r["kind"], r.get("model") or "-", r["model_calls"], r["tool_calls"],
             f"{r['total_tokens']:,}", usd(r["cost_usd"]), f"{Decimal(r['latency_ms']) / 1000:.1f}",
             r.get("outcome") or "-", short(r["run_id"])] for r in runs]
    headers = ["time (UTC)", "kind", "model", "model calls", "tool calls", "tokens", "cost USD",
               "seconds", "outcome", "run"]
    for line in table(headers, rows, right=(3, 4, 5, 6, 7)):
        print(line)


def stat_row(label, st, money=False, places=1):
    if st is None:
        return [label, "-", "-", "-"]
    f = usd if money else (lambda v: num(v, places))
    return [label, f(st["avg"]), f(st["min"]), f(st["max"])]


def print_summary_block(title, s):
    print(f"{title}: {s['runs']} run(s)")
    rows = [stat_row("model calls per run", s["model_calls"]),
            stat_row("tool calls per run", s["tool_calls"]),
            stat_row("tokens per run", s["total_tokens"], places=0),
            stat_row("cost USD per run", s["cost_usd"], money=True),
            stat_row("seconds per run", s["latency_s"])]
    for line in table(["", "average", "min", "max (worst)"], rows, right=(1, 2, 3)):
        print("  " + line)
    total = s["cost_usd"]["sum"] if s["cost_usd"] else None
    print(f"  total cost: {usd(total)}   total tokens: {s['total_tokens']['sum']:,}   "
          f"total model calls: {s['model_calls']['sum']}")
    if s["cost_usd"] is not None and s["runs_with_cost"] < s["runs"]:
        print(f"  cost average, min and max use the {s['runs_with_cost']} run(s) that reported a cost.")
    if s["runs_with_incomplete_cost"]:
        print(f"  {s['runs_with_incomplete_cost']} of {s['runs']} run(s) had a cost reported for "
              "fewer calls than they made, or none. " + LOWER_BOUND)


def print_models(runs):
    by = {}
    for r in runs:
        by.setdefault((r.get("model") or "-", r.get("provider") or "-"), []).append(r)
    print("BY MODEL (tokens per answer, from the log)")
    rows = []
    for (model, provider), group in sorted(by.items()):
        st = stats([r["total_tokens"] for r in group])
        rows.append([model, provider, len(group), num(st["avg"], 0), f"{st['max']:,}",
                     num(stats([r["input_tokens"] for r in group])["avg"], 0),
                     num(stats([r["output_tokens"] for r in group])["avg"], 0)])
    headers = ["model", "provider", "runs", "avg tokens", "max tokens", "avg input", "avg output"]
    for line in table(headers, rows, right=(2, 3, 4, 5, 6)):
        print(line)


def print_outcomes(rows, overlap):
    print("COST PER COMPLETED OUTCOME (working shown)")
    print("A report counts every chat run of its conversation up to the time it was released. "
          "A lesson plan counts the run that made it.")
    for row in rows:
        who = f" by {row['who']}" if row["who"] else ""
        label = "lesson plan approved" if row["kind"] == "lesson_plan" else "report released"
        print(f"\n{row['outcome_id']}  {label} {when(row['completed_at'])}{who}")
        if not row["runs"]:
            print("  no usage recorded")
            continue
        for r in row["runs"]:
            print(f"  {short(r['run_id'])}  {r['kind']:6} {when(r['started'])}  "
                  f"{r['model_calls']} model calls  {r['tool_calls']} tool calls  "
                  f"{r['total_tokens']:,} tokens  {usd(r['cost_usd'])}")
        flag = "  (lower bound: cost not reported for every call)" if row["cost_lower_bound"] else ""
        print(f"  sum: {len(row['runs'])} run(s), {row['model_calls']} model calls, "
              f"{row['tokens']:,} tokens, {usd(row['cost_usd'])}{flag}")
    print()
    groups = [(k, [r for r in rows if r["kind"] == k]) for k in OUTCOME_KINDS]
    groups = [(k, g) for k, g in groups if g]
    if len(groups) > 1:
        groups.append(("all outcomes", rows))
    for name, group in groups:
        s = outcome_summary(group)
        title = name.replace("_", " ") + ("s" if name in OUTCOME_KINDS else "")
        print(f"{title}: {s['outcomes']} completed, {s['with_usage']} with usage recorded")
        if s["no_usage_recorded"]:
            print(f"  {s['no_usage_recorded']} had no usage recorded and are not in the averages.")
        if s["with_usage"]:
            lines = [stat_row("cost USD per outcome", s["cost_usd"], money=True),
                     stat_row("model calls per outcome", s["model_calls"]),
                     stat_row("tokens per outcome", s["tokens"], places=0)]
            for line in table(["", "average", "min", "max (worst)"], lines, right=(1, 2, 3)):
                print("  " + line)
            if s["with_incomplete_cost"]:
                print(f"  {s['with_incomplete_cost']} outcome(s) had a cost reported for fewer calls "
                      "than were made. " + LOWER_BOUND)
    if overlap:
        print("A run that served two outcomes is counted in each, so the outcome sums can add up to "
              "more than the total cost above.")


def to_plain(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, dict):
        return {k: to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(v) for v in value]
    return value


# ── Main ─────────────────────────────────────────────────────────────

def build(args, now):
    cutoff = cutoff_for(args.since, now)
    all_runs, bad = read_usage(args.usage_file)
    runs = sorted((r for r in all_runs if r["started"] >= cutoff), key=lambda r: r["started"])
    outcomes = []
    if args.outcomes:
        outcomes = [o for o in read_outcomes(args.outcomes) if o["completed"] >= cutoff]
        outcomes.sort(key=lambda o: (o["completed"], o["outcome_id"]))
    rows = [work_out(o, all_runs) for o in outcomes]
    seen, overlap = set(), False
    for row in rows:
        for run_id in row["run_ids"]:
            overlap = overlap or run_id in seen
            seen.add(run_id)
    return cutoff, runs, bad, rows, overlap


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tokens, cost and model calls from the usage log.")
    ap.add_argument("usage_file", nargs="?", help="usage log file, or - for stdin")
    ap.add_argument("--since", default="24h", help="24h, 7d or YYYY-MM-DD (default 24h)")
    ap.add_argument("--outcomes", help="JSON array of completed outcomes (see the top of this file)")
    ap.add_argument("--format", choices=("text", "json"), default="text")
    ap.add_argument("--print-cutoff", action="store_true", help="print the UTC start of the window and stop")
    ap.add_argument("--now", help=argparse.SUPPRESS)  # fixed clock, for tests
    args = ap.parse_args(argv)
    try:
        now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
        if args.print_cutoff:
            print(cutoff_for(args.since, now).strftime("%Y-%m-%dT%H:%M:%SZ"))
            return 0
        if not args.usage_file:
            ap.error("give the usage file, or - for stdin")
        cutoff, runs, bad, rows, overlap = build(args, now)
    except BadInput as exc:
        print(f"usage_report: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"usage_report: bad --now: {exc}", file=sys.stderr)
        return 2

    kinds = [k for k in KINDS if any(r["kind"] == k for r in runs)]
    overall = summarise(runs) if runs else None
    if bad:
        print(f"usage_report: warning: {bad} malformed line(s) in the usage file were skipped.",
              file=sys.stderr)

    if args.format == "json":
        out = {
            "window": {"since": cutoff, "until": now}, "malformed_lines": bad,
            "overall": overall,
            "by_kind": {k: summarise([r for r in runs if r["kind"] == k]) for k in kinds},
            "cost_is_lower_bound": bool(overall and overall["runs_with_incomplete_cost"]),
            "runs": [{k: r.get(k) for k in ("run_id", "conversation_id", "kind", "started_at", "model",
                                            "provider", "model_calls", "tool_calls", "input_tokens",
                                            "output_tokens", "total_tokens", "cost_usd",
                                            "cost_reported_calls", "latency_ms", "outcome")}
                     for r in runs],
            "outcomes": [{k: v for k, v in row.items() if k != "runs"} for row in rows],
            "outcome_summary": {k: outcome_summary([r for r in rows if r["kind"] == k])
                                for k in OUTCOME_KINDS if any(r["kind"] == k for r in rows)},
            "reconcile": RECONCILE,
        }
        print(json.dumps(to_plain(out), indent=2))
        return 0

    print(f"MDEIS usage report: {when(cutoff)} to {when(now)} (UTC)")
    if bad:
        print(f"{bad} malformed line(s) in the usage file were skipped.")
    print()
    if not runs:
        print("No runs in this window.")
    else:
        print_runs(runs)
        print("\nSUMMARY")
        print_summary_block("All runs", overall)
        if len(kinds) > 1:
            for k in kinds:
                print()
                print_summary_block(f"Kind {k}", summarise([r for r in runs if r["kind"] == k]))
        print()
        print_models(runs)
        if overall["runs_with_incomplete_cost"]:
            print("\n" + LOWER_BOUND)
    if args.outcomes:
        print()
        if rows:
            print_outcomes(rows, overlap)
        else:
            print("COST PER COMPLETED OUTCOME: no report was released and no lesson plan approved "
                  "in this window.")
    print("\n" + RECONCILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
