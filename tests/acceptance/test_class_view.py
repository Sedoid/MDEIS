"""E-11 (docs/TESTS.md): the class view, `stage_gap_counts`. LOCKED by default (docs/step-15.md).

The expected counts come from the practice school's answer-key code (synthetic/expected.py), which
does not import src/mdeis, applied to the same learners and cut at the same stage and term. The
tests also prove that the view is off, shows no names, covers one school, and hides a small group.
"""

import importlib
import json

import pytest
from synthetic import expected

APPROVER = ("u-ada", "Ada Teacher")
VERSIONS = ["v1", "v2-strict"]
MINIMUM = {"v1": 10, "v2-strict": 15}  # GM-1 in each rulebook file


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("CLASS_VIEW_ENABLED", "true")


def view(ctx, **overrides):
    import mdeis.tools as toolbox

    args = {"school_id": "OCC", "stage": "Form 3", "skill": "literacy", "term": 3, **overrides}
    return toolbox.stage_gap_counts(ctx, **args)


def lines(ctx):
    import psycopg

    with psycopg.connect(ctx.database_url) as c:
        return c.execute(
            "SELECT outcome, approver_name FROM action_log WHERE run_id = %s AND tool = %s "
            "ORDER BY event_id",
            (ctx.run_id, "stage_gap_counts"),
        ).fetchall()


def oracle_counts(learners, rules, stage, area, term):
    """Counts by the answer-key code: each learner's records up to `term` of their latest year at
    `stage`, placed by the key's own area function."""
    counts, group = {}, 0
    for ln in learners:
        cards = ln["cards"]
        primary = [c for c in cards if c.get("card_version", 1) == 1]
        records = expected.cards_to_records(primary)
        issues = expected.card_issues(cards, rules)
        blocked = expected.blocked_subjects(issues)
        if any(i["kind"] == "duplicate_card" for i in issues):
            blocked = sorted(set(blocked) | set(rules.subjects))
        years = [expected.when(r)[0] for r in records if r["stage"] == stage]
        if not years:
            continue
        group += 1
        upto = [r for r in records if expected.when(r) <= (max(years), term)]
        name = expected.skill_result(upto, area, rules, blocked)["level_name"]
        counts[name] = counts.get(name, 0) + 1
    return group, counts


# --------------------------------------------------------------------------- locked by default
def test_the_class_view_is_off_unless_it_is_switched_on_and_a_refusal_is_logged(
    make_ctx, monkeypatch
):
    monkeypatch.delenv("CLASS_VIEW_ENABLED", raising=False)
    ctx = make_ctx(APPROVER)
    result = view(ctx)
    assert result["status"] == "refused" and "class_view_locked" in result["reason"]
    assert lines(ctx) == [("refused", "Ada Teacher")]
    monkeypatch.setenv("CLASS_VIEW_ENABLED", "false")
    assert view(make_ctx(APPROVER))["status"] == "refused"


def test_the_server_lists_it_only_when_it_is_switched_on(monkeypatch):
    import asyncio

    from mdeis import server

    try:
        listed = {t.name for t in asyncio.run(server.mcp.list_tools())}
        assert "stage_gap_counts" not in listed and len(listed) == 8
        monkeypatch.setenv("CLASS_VIEW_ENABLED", "true")
        server = importlib.reload(server)
        assert "stage_gap_counts" in {t.name for t in asyncio.run(server.mcp.list_tools())}
    finally:
        monkeypatch.delenv("CLASS_VIEW_ENABLED", raising=False)
        importlib.reload(server)


def test_with_the_view_on_it_still_needs_a_signed_in_approver(make_ctx, on):
    ctx = make_ctx()
    result = view(ctx)
    assert result["status"] == "refused" and "approver" in result["reason"].lower()
    assert lines(ctx) == [("refused", None)]


# --------------------------------------------------------------------------- the counts
@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("stage", ["Form 3", "Form 2", "Form 1"])
def test_the_counts_equal_the_answer_key_code_under_each_rulebook(
    make_ctx, on, learners, built, stage, version
):
    rules = built[2][version]
    group, counts = oracle_counts(learners, rules, stage, "literacy", 3)
    ctx = make_ctx(APPROVER)
    result = view(ctx, stage=stage, template_version=version)
    assert result["status"] == "ok" and result["template_version"] == version
    assert result["group_size"] == group
    assert {k: v for k, v in result["counts"].items() if v} == counts
    assert sum(result["counts"].values()) == group
    assert "GM-1" in result["clause_ids"]
    assert result["minimum_group_size"] == MINIMUM[version]
    assert lines(ctx) == [("ok", "Ada Teacher")]


def test_the_answer_holds_no_names_no_learner_numbers_and_no_record_numbers(
    make_ctx, on, learners
):
    result = view(make_ctx(APPROVER))
    text = json.dumps(result)
    for ln in learners:
        assert ln["name"] not in text and ln["gen_id"] not in text and ln["ref"] not in text
    assert "A-0" not in text and "L-0" not in text
    assert set(result) == {
        "school_id", "stage", "skill", "term", "template_version", "minimum_group_size",
        "status", "group_size", "counts", "clause_ids", "result_hash",
        "rules",  # the rulebook's own wording for the rules applied: no learner data
    }  # fmt: skip


def test_the_same_question_gives_the_same_answer(make_ctx, on):
    assert view(make_ctx(APPROVER)) == view(make_ctx(APPROVER))


# ------------------------------------------------------------------- small groups, one school
INSERT_SCORE = (
    "INSERT INTO assessments (learner_id, subject, score, academic_year, stage, term, "
    "coefficient, denominator, raw_score) VALUES (%s, %s, 13, %s, 'Form 1', %s, 2, 20, '13.00')"
)


def add_school(ctx, code, how_many):
    """`how_many` learners of a made-up school, each with six terms of literacy scores of 13."""
    for k in range(how_many):
        learner = ctx.conn.execute(
            "INSERT INTO learners (school_code, school_ref) VALUES (%s, %s) RETURNING learner_id",
            (code, f"{code}/2025/{k:04d}"),
        ).fetchone()[0]
        for subject in ("English", "French", "Literature in English"):
            for year in ("2024/2025", "2025/2026"):
                for term in (1, 2, 3):
                    ctx.conn.execute(INSERT_SCORE, (learner, subject, year, term))


@pytest.mark.parametrize("version", VERSIONS)
def test_a_group_of_exactly_the_minimum_is_shown_and_one_fewer_is_not(make_ctx, on, version):
    minimum = MINIMUM[version]
    ctx = make_ctx(APPROVER)
    add_school(ctx, "TST", minimum)
    shown = view(ctx, school_id="TST", stage="Form 1", template_version=version)
    assert shown["status"] == "ok" and shown["group_size"] == minimum
    where = {"v1": "within_expected_range", "v2-strict": "below_expected_range"}[version]
    assert shown["counts"][where] == minimum, "13 out of 20 against each rulebook's Form 1 range"
    add_school(ctx, "TIN", minimum - 1)
    hidden = view(ctx, school_id="TIN", stage="Form 1", template_version=version)
    assert hidden["status"] == "suppressed" and "smaller" in hidden["reason"]
    assert "counts" not in hidden and "group_size" not in hidden, "not even how many there are"
    assert hidden["clause_ids"] == ["GM-1"]


def test_a_school_with_no_learners_is_suppressed_not_an_error(make_ctx, on):
    assert view(make_ctx(APPROVER), school_id="NOBODY")["status"] == "suppressed"


def test_one_school_only_another_schools_learners_are_never_counted(make_ctx, on):
    ctx = make_ctx(APPROVER)
    before = view(ctx, stage="Form 1")["group_size"]
    add_school(ctx, "TST", 12)
    assert view(ctx, stage="Form 1")["group_size"] == before


def test_an_unknown_stage_or_skill_is_refused(make_ctx, on):
    ctx = make_ctx(APPROVER)
    assert view(ctx, stage="Form 9")["status"] == "refused"
    assert view(ctx, skill="Mathematics")["status"] == "refused"  # a subject, not a learning area
