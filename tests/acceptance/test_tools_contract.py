"""Step 7: what "correct" means for the tools, written before the tools exist (docs/step-07.md).

These tests call the tools as plain Python functions, `tool(ctx, **arguments)`, and compare what
comes back with the answer key made in Step 6 (`data/synthetic/truth/answer_key.json`). The key was
worked out by hand and by separate code, so these tests do not trust the tools to mark their own
work.

They are marked `contract`. Until the tools are built they FAIL, each one with the plain reason
"the tool is not built yet". That is the point of writing them first. `make test` leaves them out;
`make test-contract` runs them. When Step 8 is done they must all pass, and the marker is dropped.

The interface they define (docs/step-07.md, decisions D7-1 to D7-6):

    mdeis.tools.context.ToolContext(conn, run_id, approver=None, database_url=None, commit=True)
    mdeis.tools.context.Approver(id, name)          set from the verified token, never an argument
    ingest_assessments(ctx, learner_id, template_version=None)
    compute_trend(ctx, learner_id, skill, template_version=None)       skill: a subject or an area
    resolve_issue(ctx, issue_id, resolution, value=None, note=None)
    get_criterion(ctx, query, template_version=None, k=3)
    draft_summary(ctx, learner_id, text, finding_hashes)
    check_citations(ctx, report_id)
    release_report(ctx, report_id)

Numbers come back as text with two decimals, rounded half up (the key's own form), so "-2.00" is
exactly "-2.00". The tools do not commit when `ctx.commit` is false, which is how these tests
roll back.
"""

import ast
import importlib
import inspect
import json
import re
from pathlib import Path

import psycopg
import pytest
import yaml

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[2]
TOOL_NAMES = [
    "ingest_assessments",
    "resolve_issue",
    "compute_trend",
    "get_criterion",
    "draft_summary",
    "check_citations",
    "release_report",
]
HANDLES = [
    "FX-01", "FX-02", "FX-03a", "FX-03b", "FX-04a", "FX-04b", "FX-04c", "FX-04d", "FX-05",
    "FX-08", "FX-09a", "FX-09b", "FX-22",
]  # fmt: skip
VERSIONS = ["v1", "v2-strict"]
APPROVER = ("u-ada", "Ada Teacher")
NOT_AN_APPROVER_ARGUMENT = {"approver", "approver_id", "approver_name", "user", "user_id", "by"}
RULE_WORDS = ("THRESHOLD", "MIN", "MAX", "RANGE", "WINDOW", "PASS", "MARGIN", "BAND", "WEIGHT")


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


def diary_lines(url, run_id, tool_name=None):
    with psycopg.connect(url) as c:
        sql = "SELECT tool, outcome, approver_name FROM action_log WHERE run_id = %s"
        args = [run_id]
        if tool_name:
            sql += " AND tool = %s"
            args.append(tool_name)
        return c.execute(sql + " ORDER BY event_id", args).fetchall()


def has_number(result, *names):
    return any(result.get(n) is not None for n in names)


def check_subject(result, want, version):
    """One subject's result against the key's."""
    assert result["template_version"] == version
    if want["label"] == "blocked":
        assert result["status"] == "blocked", result
        assert not has_number(result, "change", "level", "trend"), "no number for a blocked subject"
        assert result["reasons"], "a blocked result says why"
        return
    assert result["status"] == "ok", result
    assert result["trend"] == want["label"], (want["subject"], result)
    if want["label"] == "not_enough_evidence":
        assert not has_number(result, "change", "level"), "no number without enough evidence"
        assert "TR-1" in result["clause_ids"]
        return
    assert result["change"] == want["change"], (want["subject"], result)
    assert result["level"] == want["level"], (want["subject"], result)
    assert set(want["clauses"]) <= set(result["clause_ids"])
    assert len(result["record_ids"]) == want["records_in_window"]  # cites the records it used


def check_area(result, want, version):
    """One area (a skill) against the key's: the level and the gap to the stage's range."""
    assert result["template_version"] == version
    if want["level_name"] == "blocked":
        assert result["status"] == "blocked", result
        assert not has_number(result, "level") and not result.get("stage_gap")
        return
    assert result["status"] == "ok", result
    if want["level_name"] == "not_enough_evidence":
        assert result["trend"] == "not_enough_evidence"
        assert not has_number(result, "level") and not result.get("stage_gap")
        return
    gap = result["stage_gap"]
    assert result["level"] == want["level"], (want["area"], result)
    assert gap["gap"] == want["gap"], (want["area"], gap)
    assert gap["direction"] == want["gap_direction"], (want["area"], gap)
    assert gap["level_name"] == want["level_name"]
    assert gap["stage"] == want["stage"]
    assert gap["range"] == want["range"]
    assert set(want["clauses"]) <= set(result["clause_ids"])


def issue_projection(issues):
    """What identifies a problem: its kind, severity, subject, term and field."""
    return {
        (i["kind"], i["severity"], i.get("subject"), i.get("term"), i.get("field")) for i in issues
    }


def trend(ctx, learner, skill, version=None):
    return tool("compute_trend")(ctx, learner_id=learner, skill=skill, template_version=version)


def ingest(ctx, learner, version=None):
    return tool("ingest_assessments")(ctx, learner_id=learner, template_version=version)


def results_of(ctx, learner, version, expected):
    """compute_trend for every subject and area in the key. Returns {name: (result, want)}."""
    compute = tool("compute_trend")
    out = {}
    for name, want in expected["subjects"].items():
        out[name] = (compute(ctx, learner_id=learner, skill=name, template_version=version), want)
    for name, want in expected["areas"].items():
        out[name] = (compute(ctx, learner_id=learner, skill=name, template_version=version), want)
    return out


# =============================================================================================
# 0. The tools exist, and are built the way the promises need
# =============================================================================================
def test_every_tool_exists_takes_the_context_first_and_has_no_approver_argument():
    for name in TOOL_NAMES:
        parameters = list(inspect.signature(tool(name)).parameters)
        assert parameters[0] == "ctx", f"{name} must take the context first"
        assert not NOT_AN_APPROVER_ARGUMENT & set(parameters), (
            f"{name} must not take an approver or user argument: the approver is in the context"
        )


def test_the_server_offers_exactly_these_tools_and_none_takes_an_approver():
    import asyncio

    try:
        from mdeis.server import mcp
    except ImportError as e:
        pytest.fail(f"mdeis.server is not built yet ({e})", pytrace=False)
    listed = asyncio.run(mcp.list_tools())
    assert {t.name for t in listed} == set(TOOL_NAMES), "no placeholder tool, no extra tool"
    for t in listed:
        properties = set((t.inputSchema or {}).get("properties", {}))
        assert not NOT_AN_APPROVER_ARGUMENT & properties, t.name


def test_no_school_number_is_written_in_the_tool_code():
    """Only 0 and 1, and named constants whose names are not about a rule (a timeout is fine, a
    threshold or a window is not). The rulebook is the only place school numbers live."""
    files = sorted((ROOT / "src" / "mdeis" / "tools").glob("*.py")) + [
        p for p in sorted((ROOT / "src" / "mdeis" / "core").glob("*.py")) if p.name != "template.py"
    ]
    code = [p for p in files if p.name != "__init__.py" and p.stat().st_size]
    assert code, "there is no tool code to check yet"
    for path in code:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        allowed = set()
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign):
                names = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
                if names and all(n.isupper() for n in names):
                    assert not any(w in n for n in names for w in RULE_WORDS), (
                        f"{path.name}: {names} looks like a school rule. It belongs in the rulebook"
                    )
                    allowed |= {id(n) for n in ast.walk(stmt)}
        found = {
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant)
            and isinstance(n.value, (int, float))
            and not isinstance(n.value, bool)
            and id(n) not in allowed
        }
        assert found <= {0, 1}, f"{path.name}: numbers in the code: {sorted(found - {0, 1})}"


# =============================================================================================
# 1. The same input always gives the same output
# =============================================================================================
def test_the_same_input_gives_the_same_output_and_the_same_hash(make_ctx, fx):
    learner = fx["FX-01"]["gen_id"]
    first = trend(make_ctx(), learner, "English", "v1")
    second = trend(make_ctx(), learner, "English", "v1")
    assert first == second
    assert first["result_hash"] == second["result_hash"] and first["result_hash"]


def test_reading_the_same_learner_twice_in_one_run_gives_the_same_issues(make_ctx, fx):
    ctx = make_ctx()
    learner = fx["FX-04d"]["gen_id"]
    assert tool("ingest_assessments")(ctx, learner_id=learner) == tool("ingest_assessments")(
        ctx, learner_id=learner
    )


def test_a_result_carries_no_run_number_and_no_time(make_ctx, fx):
    result = tool("compute_trend")(make_ctx(), learner_id=fx["FX-01"]["gen_id"], skill="English")
    text = json.dumps(result)
    assert "RUN-" not in text
    assert not re.search(r"\d{4}-\d{2}-\d{2}T", text)


# =============================================================================================
# 2. Every threshold comes from the rulebook, and the strict rulebook changes the answer
# =============================================================================================
@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("handle", HANDLES)
def test_a_fixture_learner_gets_the_answer_keys_result_under_each_rulebook(
    make_ctx, fx, handle, version
):
    expected = fx[handle]["expected"][version]
    results = results_of(make_ctx(), fx[handle]["gen_id"], version, expected)
    for name, (result, want) in results.items():
        check = check_area if name in expected["areas"] else check_subject
        check(result, want, version)


def test_every_learner_in_the_cohort_gets_the_answer_keys_result_under_both_rulebooks(
    make_ctx, key, learners
):
    ctx = make_ctx()
    by_ref = {ln["ref"]: ln["gen_id"] for ln in learners}
    for ref, entry in key["learners"].items():
        for version in VERSIONS:
            expected = entry["by_template"][version]
            ingest = tool("ingest_assessments")(
                ctx, learner_id=by_ref[ref], template_version=version
            )
            blocked = set(ingest["blocked_subjects"])
            assert blocked == set(expected["blocked_subjects"]), (ref, version)
            for name, (result, want) in results_of(ctx, by_ref[ref], version, expected).items():
                check = check_area if name in expected["areas"] else check_subject
                check(result, want, version)


def test_the_strict_rulebook_changes_the_answer_and_cites_the_same_rules(make_ctx, fx):
    learner = fx["FX-08"]["gen_id"]
    normal = trend(make_ctx(), learner, "literacy", "v1")
    strict = trend(make_ctx(), learner, "literacy", "v2-strict")
    assert normal["stage_gap"]["gap"] != strict["stage_gap"]["gap"]
    assert normal["stage_gap"]["direction"] != strict["stage_gap"]["direction"]  # steady, widening
    assert {"CA-5", "TR-4"} <= set(normal["clause_ids"]) & set(strict["clause_ids"])
    assert normal["result_hash"] != strict["result_hash"]


def test_a_clear_decline_cites_its_records_and_its_rules(make_ctx, fx):
    """E-01. The window is the last scores that count (TR-1); the result cites those records."""
    for version, records, change in (("v1", 3, "-2.00"), ("v2-strict", 4, "-3.00")):
        result = tool("compute_trend")(
            make_ctx(), learner_id=fx["FX-01"]["gen_id"], skill="English", template_version=version
        )
        assert result["trend"] == "declining" and result["change"] == change
        assert len(result["record_ids"]) == records
        assert all(re.fullmatch(r"A-\d{6,}", r) for r in result["record_ids"])
        assert {"TR-1", "TR-2"} <= set(result["clause_ids"])


def test_a_score_out_of_100_gives_the_same_trend_as_the_same_score_out_of_20(make_ctx, fx):
    """E-03. FX-03b's marks are out of 100: converted with a warning, the same result as FX-03a."""
    ctx = make_ctx()  # one context: two open ones would wait on each other's uncommitted problem rows
    out_of_20 = trend(ctx, fx["FX-03a"]["gen_id"], "Mathematics")
    out_of_100 = trend(ctx, fx["FX-03b"]["gen_id"], "Mathematics")
    for field in ("trend", "change", "level"):
        assert out_of_20[field] == out_of_100[field], field
    ingest = tool("ingest_assessments")(ctx, learner_id=fx["FX-03b"]["gen_id"])
    assert any(i["kind"] == "denominator_unexpected" for i in ingest["issues"])
    converted = [r["score"] for r in ingest["records"] if r["subject"] == "Mathematics"]
    assert converted == ["10.00", "12.00", "14.00"]


def test_the_gap_to_the_stage_standard_has_a_direction_and_no_label(make_ctx, fx):
    """E-09. Amina's literacy sits below Form 3's range: a gap, a direction, and no label on her."""
    result = tool("compute_trend")(make_ctx(), learner_id=fx["FX-08"]["gen_id"], skill="literacy")
    assert result["stage_gap"]["level_name"] == "below_expected_range"
    assert result["stage_gap"]["gap"] == "0.70"
    assert result["stage_gap"]["direction"] == "steady"
    assert "ST-3" in result["clause_ids"]
    for other, direction in (("FX-09a", "widening"), ("FX-09b", "closing")):
        got = tool("compute_trend")(make_ctx(), learner_id=fx[other]["gen_id"], skill="literacy")
        assert got["stage_gap"]["direction"] == direction, other


def test_a_change_of_exactly_the_threshold_is_not_a_move(make_ctx, fx):
    """Amina's Form 3 English is 11, 11.5, 12: a change of exactly 1.00. TR-2 says 'more than 1'."""
    result = tool("compute_trend")(make_ctx(), learner_id=fx["FX-08"]["gen_id"], skill="English")
    assert result["change"] == "1.00" and result["trend"] == "steady"


# =============================================================================================
# 3. Each kind of messy data becomes a named problem, never a silent fix
# =============================================================================================
@pytest.mark.parametrize("handle", ["FX-03b", "FX-04a", "FX-04b", "FX-04c", "FX-04d", "FX-05"])
def test_ingest_names_exactly_the_planted_problems(make_ctx, fx, handle):
    expected = fx[handle]["expected"]["v1"]
    ingest = tool("ingest_assessments")(make_ctx(), learner_id=fx[handle]["gen_id"])
    assert issue_projection(ingest["issues"]) == issue_projection(expected["issues"])
    assert set(ingest["blocked_subjects"]) == set(expected["blocked_subjects"])
    for issue in ingest["issues"]:
        assert issue["severity"] in ("blocking", "warning")
        assert re.fullmatch(r"I-\d{6,}", issue["issue_id"])
        assert issue["question"], "every problem has a plain question for the approver"


def test_the_plain_cards_have_no_problems(make_ctx, fx):
    for handle in ("FX-01", "FX-08", "FX-09a", "FX-09b", "FX-22"):
        ingest = tool("ingest_assessments")(make_ctx(), learner_id=fx[handle]["gen_id"])
        assert ingest["issues"] == [] and ingest["blocked_subjects"] == [], handle


def test_reading_a_learner_changes_none_of_their_records(make_ctx, fx):
    ctx = make_ctx()
    learner = fx["FX-04b"]["gen_id"]
    sql = (
        "SELECT md5(string_agg(a::text, '|' ORDER BY assessment_id)) "
        "FROM assessments a WHERE learner_id = %s"
    )
    before = ctx.conn.execute(sql, (learner,)).fetchone()[0]
    tool("ingest_assessments")(ctx, learner_id=learner)
    tool("compute_trend")(ctx, learner_id=learner, skill="Chemistry")
    assert ctx.conn.execute(sql, (learner,)).fetchone()[0] == before, "nothing is fixed in silence"


@pytest.mark.parametrize(
    "handle, subject",
    [("FX-04a", "English"), ("FX-04b", "Chemistry"), ("FX-04c", "Mathematics")],
)
def test_no_number_is_worked_out_for_a_blocked_subject_even_if_nobody_read_the_learner_first(
    make_ctx, fx, handle, subject
):
    result = tool("compute_trend")(make_ctx(), learner_id=fx[handle]["gen_id"], skill=subject)
    assert result["status"] == "blocked"
    assert not has_number(result, "change", "level", "trend")


def test_a_subject_that_is_not_blocked_is_still_worked_out(make_ctx, fx):
    result = trend(make_ctx(), fx["FX-04a"]["gen_id"], "Mathematics")
    assert result["status"] == "ok" and result["trend"] == "steady"


def test_a_printed_total_that_is_wrong_is_a_warning_and_the_scores_are_still_used(make_ctx, fx):
    ctx = make_ctx()
    learner = fx["FX-04d"]["gen_id"]
    issues = ingest(ctx, learner)["issues"]
    kinds = {i["kind"]: i["severity"] for i in issues}
    assert kinds == {"printed_value_mismatch": "warning"}
    history = tool("compute_trend")(ctx, learner_id=learner, skill="History")
    assert history["status"] == "ok" and history["level"] == "12.50"  # the terms, not the print


def test_a_remark_that_disagrees_with_the_scores_is_shown_not_settled(make_ctx, fx):
    """E-05, the data half. The tool reports both: the scores fall, the remark says improving."""
    ctx = make_ctx()
    learner = fx["FX-05"]["gen_id"]
    ingest = tool("ingest_assessments")(ctx, learner_id=learner)
    conflict = next(i for i in ingest["issues"] if i["kind"] == "note_conflict")
    assert conflict["severity"] == "warning" and conflict["subject"] == "Geography"
    assert conflict["terms"] == ["15.00", "14.00", "13.00"]
    assert tool("compute_trend")(ctx, learner_id=learner, skill="Geography")["trend"] == "declining"


def test_too_little_evidence_is_said_plainly_with_no_number(make_ctx, fx):
    """E-02. Two scores: not enough evidence, TR-1 cited, no change and no level."""
    result = tool("compute_trend")(make_ctx(), learner_id=fx["FX-02"]["gen_id"], skill="English")
    assert result["status"] == "ok" and result["trend"] == "not_enough_evidence"
    assert not has_number(result, "change", "level")
    assert "TR-1" in result["clause_ids"]


# =============================================================================================
# 4. The approver is the signed-in person, set by the system, and actions are refused without one
# =============================================================================================
def first_issue(ctx, learner, kind):
    ingest = tool("ingest_assessments")(ctx, learner_id=learner)
    return next(i for i in ingest["issues"] if i["kind"] == kind)


def test_resolving_a_problem_is_refused_and_logged_when_nobody_is_signed_in(make_ctx, fx):
    ctx = make_ctx()
    issue = first_issue(ctx, fx["FX-04a"]["gen_id"], "missing_term")
    result = tool("resolve_issue")(ctx, issue_id=issue["issue_id"], resolution="confirm_absent")
    assert result["status"] == "refused" and "approver" in result["reason"].lower()
    assert ("resolve_issue", "refused", None) in diary_lines(ctx.database_url, ctx.run_id)
    again = tool("compute_trend")(ctx, learner_id=fx["FX-04a"]["gen_id"], skill="English")
    assert again["status"] == "blocked", "a refused answer unblocks nothing"


def test_a_name_made_up_in_the_arguments_is_not_accepted(make_ctx, fx):
    ctx = make_ctx()
    issue = first_issue(ctx, fx["FX-04a"]["gen_id"], "missing_term")
    with pytest.raises(TypeError):
        tool("resolve_issue")(
            ctx, issue_id=issue["issue_id"], resolution="confirm_absent", approver="Dr Fake"
        )


def test_the_signed_in_approver_can_answer_a_problem_and_it_is_logged_with_their_name(make_ctx, fx):
    ctx = make_ctx(APPROVER)
    learner = fx["FX-04a"]["gen_id"]
    issue = first_issue(ctx, learner, "missing_term")
    result = tool("resolve_issue")(ctx, issue_id=issue["issue_id"], resolution="confirm_absent")
    assert result["status"] == "resolved" and isinstance(result["logged_event_id"], int)
    assert result["unblocked_subjects"] == ["English"]
    assert ("resolve_issue", "ok", "Ada Teacher") in diary_lines(ctx.database_url, ctx.run_id)
    after = tool("compute_trend")(ctx, learner_id=learner, skill="English")
    assert after["status"] == "ok" and len(after["record_ids"]) > 0


def test_an_answer_that_does_not_fit_the_problem_is_refused(make_ctx, fx):
    ctx = make_ctx(APPROVER)
    issue = first_issue(ctx, fx["FX-04b"]["gen_id"], "value_out_of_range")
    wrong_kind = tool("resolve_issue")(ctx, issue_id=issue["issue_id"], resolution="confirm_absent")
    no_value = tool("resolve_issue")(ctx, issue_id=issue["issue_id"], resolution="use_value")
    unknown = tool("resolve_issue")(ctx, issue_id="I-999999", resolution="keep_as_is")
    assert [r["status"] for r in (wrong_kind, no_value, unknown)] == ["refused"] * 3


def test_every_call_leaves_one_line_in_the_diary_whatever_the_outcome(make_ctx, fx):
    ctx = make_ctx()
    tool("compute_trend")(ctx, learner_id=fx["FX-01"]["gen_id"], skill="English")  # ok
    tool("compute_trend")(ctx, learner_id=fx["FX-04a"]["gen_id"], skill="English")  # blocked
    tool("ingest_assessments")(ctx, learner_id=fx["FX-04a"]["gen_id"])
    lines = diary_lines(ctx.database_url, ctx.run_id)
    assert [(t, o) for t, o, _ in lines] == [
        ("compute_trend", "ok"),
        ("compute_trend", "blocked"),
        ("ingest_assessments", "ok"),
    ]


# =============================================================================================
# 5. A report with a number or a source no tool returned is rejected, and release is the one gate
# =============================================================================================
def banned_words():
    d = yaml.safe_load((ROOT / "templates" / "v1.yaml").read_text(encoding="utf-8"))
    return d["wording"][0]["banned"]


def a_good_draft(ctx, fx):
    """Draft a sentence from FX-01's real result. Returns (report_id, result)."""
    result = tool("compute_trend")(ctx, learner_id=fx["FX-01"]["gen_id"], skill="English")
    sources = " ".join(f"[{r}]" for r in result["record_ids"])
    text = f"English scores fell by 2.00 points over the last terms {sources} [TR-2]."
    draft = tool("draft_summary")(
        ctx, learner_id=fx["FX-01"]["gen_id"], text=text, finding_hashes=[result["result_hash"]]
    )
    assert draft["status"] == "pending_review" and re.fullmatch(r"R-\d{6,}", draft["report_id"])
    return draft["report_id"], result


def draft_with(ctx, fx, text):
    result = tool("compute_trend")(ctx, learner_id=fx["FX-01"]["gen_id"], skill="English")
    return tool("draft_summary")(
        ctx, learner_id=fx["FX-01"]["gen_id"], text=text, finding_hashes=[result["result_hash"]]
    )["report_id"], result


def reasons(checked):
    return {p["reason"] for p in checked["problems"]}


def test_a_draft_built_only_from_what_the_tools_returned_passes_the_check(make_ctx, fx):
    ctx = make_ctx()
    report_id, _ = a_good_draft(ctx, fx)
    checked = tool("check_citations")(ctx, report_id=report_id)
    assert checked["status"] == "ok" and checked["problems"] == []
    assert any(s["id"].startswith("A-") for s in checked["sources"]), "each source is resolved"


def test_a_number_no_tool_returned_is_rejected(make_ctx, fx):
    ctx = make_ctx()
    result = tool("compute_trend")(ctx, learner_id=fx["FX-01"]["gen_id"], skill="English")
    sources = " ".join(f"[{r}]" for r in result["record_ids"])
    report_id, _ = draft_with(ctx, fx, f"English scores fell by 2.50 points {sources} [TR-2].")
    checked = tool("check_citations")(ctx, report_id=report_id)
    assert checked["status"] == "fail" and "unknown_number" in reasons(checked)


def test_a_record_or_a_rule_no_tool_returned_is_rejected(make_ctx, fx):
    ctx = make_ctx()
    result = tool("compute_trend")(ctx, learner_id=fx["FX-01"]["gen_id"], skill="English")
    good = " ".join(f"[{r}]" for r in result["record_ids"])
    report_id, _ = draft_with(ctx, fx, f"English scores fell by 2.00 points {good} [A-099999].")
    assert "unknown_record" in reasons(tool("check_citations")(ctx, report_id=report_id))
    report_id, _ = draft_with(ctx, fx, f"English scores fell by 2.00 points {good} [TR-9].")
    assert "unknown_clause" in reasons(tool("check_citations")(ctx, report_id=report_id))


def test_a_sentence_that_states_a_finding_without_a_source_is_rejected(make_ctx, fx):
    ctx = make_ctx()
    report_id, _ = draft_with(ctx, fx, "English scores fell by 2.00 points over the last terms.")
    checked = tool("check_citations")(ctx, report_id=report_id)
    assert checked["status"] == "fail" and "uncited_sentence" in reasons(checked)


def test_a_banned_word_from_the_rulebook_is_rejected(make_ctx, fx):
    ctx = make_ctx()
    word = banned_words()[0]
    result = tool("compute_trend")(ctx, learner_id=fx["FX-01"]["gen_id"], skill="English")
    sources = " ".join(f"[{r}]" for r in result["record_ids"])
    text = f"English is {word} and fell by 2.00 points {sources} [TR-2]."
    report_id, _ = draft_with(ctx, fx, text)
    assert "banned_word" in reasons(tool("check_citations")(ctx, report_id=report_id))


def test_nothing_in_any_tool_output_uses_a_banned_word(make_ctx, fx):
    ctx = make_ctx()
    words = re.compile(r"\b(" + "|".join(banned_words()) + r")\b", re.I)
    for handle in HANDLES:
        learner = fx[handle]["gen_id"]
        text = json.dumps(tool("ingest_assessments")(ctx, learner_id=learner))
        for skill in ("English", "Mathematics", "literacy", "numeracy"):
            text += json.dumps(tool("compute_trend")(ctx, learner_id=learner, skill=skill))
        assert not words.search(text), handle


def test_release_is_refused_and_logged_with_nobody_signed_in(make_ctx, fx):
    ctx = make_ctx()
    report_id, _ = a_good_draft(ctx, fx)
    assert tool("check_citations")(ctx, report_id=report_id)["status"] == "ok"
    result = tool("release_report")(ctx, report_id=report_id)
    assert result["status"] == "refused" and "approver" in result["reason"].lower()
    assert ("release_report", "refused", None) in diary_lines(ctx.database_url, ctx.run_id)


def test_release_is_refused_when_the_citation_check_has_not_passed(make_ctx, fx):
    ctx = make_ctx(APPROVER)
    report_id, _ = a_good_draft(ctx, fx)  # never checked
    result = tool("release_report")(ctx, report_id=report_id)
    assert result["status"] == "refused" and "check" in result["reason"].lower()
    report_id, _ = draft_with(ctx, fx, "English scores fell by 2.00 points over the last terms.")
    assert tool("check_citations")(ctx, report_id=report_id)["status"] == "fail"
    assert tool("release_report")(ctx, report_id=report_id)["status"] == "refused"


def test_the_signed_in_approver_can_release_a_checked_report_and_is_named(make_ctx, fx):
    ctx = make_ctx(APPROVER)
    report_id, _ = a_good_draft(ctx, fx)
    assert tool("check_citations")(ctx, report_id=report_id)["status"] == "ok"
    result = tool("release_report")(ctx, report_id=report_id)
    assert result["status"] == "released" and result["approver"] == "Ada Teacher"
    assert result["at"] and isinstance(result["logged_event_id"], int)
    assert ("release_report", "ok", "Ada Teacher") in diary_lines(ctx.database_url, ctx.run_id)
    state = ctx.conn.execute(
        "SELECT status, released_by_name, template_version FROM reports WHERE report_id = %s",
        (report_id,),
    ).fetchone()
    assert state == ("released", "Ada Teacher", "v1"), "pinned to its rulebook version"


def test_a_released_report_cannot_be_released_again(make_ctx, fx):
    ctx = make_ctx(APPROVER)
    report_id, _ = a_good_draft(ctx, fx)
    tool("check_citations")(ctx, report_id=report_id)
    assert tool("release_report")(ctx, report_id=report_id)["status"] == "released"
    assert tool("release_report")(ctx, report_id=report_id)["status"] == "refused"


def test_release_is_refused_when_the_rulebook_changed_after_the_draft(make_ctx, fx):
    ctx = make_ctx(APPROVER)
    report_id, _ = a_good_draft(ctx, fx)
    assert tool("check_citations")(ctx, report_id=report_id)["status"] == "ok"
    ctx.conn.execute("UPDATE templates SET active = false WHERE active")
    ctx.conn.execute("UPDATE templates SET active = true WHERE version_name = 'v2-strict'")
    result = tool("release_report")(ctx, report_id=report_id)
    assert result["status"] == "refused" and result["reason"] == "stale_pin"
    assert ("release_report", "refused", "Ada Teacher") in diary_lines(ctx.database_url, ctx.run_id)


# =============================================================================================
# 6. Finding a rule
# =============================================================================================
def test_a_rule_can_be_found_by_its_id_with_its_own_words(make_ctx):
    template = yaml.safe_load((ROOT / "templates" / "v1.yaml").read_text(encoding="utf-8"))
    wanted = next(r for r in template["trend"] if r["id"] == "TR-2")
    found = tool("get_criterion")(make_ctx(), query="TR-2", template_version="v1")
    assert found["clauses"][0]["clause_id"] == "TR-2"
    assert found["clauses"][0]["text"] == wanted["text"]


def test_a_rule_id_that_does_not_exist_returns_nothing_not_an_error(make_ctx):
    assert tool("get_criterion")(make_ctx(), query="TR-99", template_version="v1")["clauses"] == []
