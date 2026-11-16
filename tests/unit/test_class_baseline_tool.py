"""class_baseline as a tool, on a real database. Marked `integration`.

A throwaway schema is built once for the file (migrations, then the four rulebook versions: v1 and
v2-strict, v1.1 and v2.1-strict, with v1.1 last so it is active). Each test makes its own school
with its own learners, so tests do not touch each other. Set-up commits on its own connection and
closes it; the tool then gets one connection of its own (never two open at once, because two open
transactions on the same rows wait for each other). Expected values are worked out by hand.

Every learner here has Mathematics scores only, so numeracy is placed and literacy has no usable
evidence. Scores (out of 20, Form 1 numeracy range 10 to 14 in v1.1): 8, 8, 8 is below, 12, 12, 12
is within, 16, 16, 16 is above, two terms only is not enough evidence, and a blank second term is a
blocked subject.
"""

import contextlib
import json
import os
import re
import uuid
from pathlib import Path

import psycopg
import pytest
from dbtools import run_migrate
from psycopg.conninfo import make_conninfo

import mdeis.tools as toolbox
from mdeis import rulebook
from mdeis.tools.context import Approver, ToolContext

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
T = ROOT / "templates"
ADA = Approver("u-ada", "Ada Teacher")
FIVE = ("below", "within", "above", "not_enough", "blocked")
SCORES = {
    "below": ["8", "8", "8"],
    "within": ["12", "12", "12"],
    "above": ["16", "16", "16"],
    "not_enough": ["12", "12"],
    "blocked": ["12", None, "12"],
}
BIN_NAMES = {
    "below": "below_expected_range",
    "within": "within_expected_range",
    "above": "above_expected_range",
    "not_enough": "not_enough_evidence",
    "blocked": "blocked",
}


@pytest.fixture(scope="module")
def schema():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is not set: integration tests skipped")
    name = "classbase_" + os.urandom(4).hex()
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{name}"')
    scoped = make_conninfo(url, options=f"-c search_path={name},public")
    try:
        built = run_migrate(scoped)
        assert built.returncode == 0, built.stderr
        for file in ("v2-strict", "v1", "v2.1-strict", "v1.1"):
            with psycopg.connect(scoped) as c:
                rulebook.load_file(c, T / f"{file}.yaml", url=scoped, by="test")
        yield scoped
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{name}" CASCADE')


@pytest.fixture(autouse=True)
def view_on(monkeypatch):
    monkeypatch.setenv("CLASS_VIEW_ENABLED", "true")


@pytest.fixture
def school():
    return "S" + uuid.uuid4().hex[:6].upper()


@pytest.fixture
def opened():
    connections = []
    yield connections
    for c in connections:
        c.rollback()
        c.close()


@pytest.fixture
def make_ctx(schema, opened, school):
    def make(approver=ADA, school_code=school):
        conn = psycopg.connect(schema)
        opened.append(conn)
        return ToolContext(
            conn=conn,
            run_id=f"RUN-{uuid.uuid4()}",
            approver=approver,
            database_url=schema,
            commit=False,
            school=school_code,
        )

    return make


def add_class(url, school, counts, stage="Form 1", year="2025/2026"):
    """Learners of `school` at `stage` in `year`: counts is how many in each of the five kinds."""
    with psycopg.connect(url) as conn:
        n = conn.execute("SELECT count(*) FROM learners WHERE school_code = %s", (school,))
        start = n.fetchone()[0]
        made = 0
        for kind, how_many in zip(FIVE, counts, strict=True):
            for _ in range(how_many):
                made += 1
                lid = conn.execute(
                    "INSERT INTO learners (school_code, school_ref, display_name) "
                    "VALUES (%s, %s, %s) RETURNING learner_id",
                    (school, f"{school}/{year[:4]}/{start + made:03d}", f"Pupil Name{made}"),
                ).fetchone()[0]
                for term, raw in enumerate(SCORES[kind], start=1):
                    conn.execute(
                        "INSERT INTO assessments (learner_id, subject, score, academic_year, "
                        "stage, term, coefficient, denominator, raw_score) "
                        "VALUES (%s, 'Mathematics', %s, %s, %s, %s, 4, 20, %s)",
                        (lid, raw, year, stage, term, raw),
                    )
        conn.commit()


@contextlib.contextmanager
def active(url, name):
    """Make `name` the active rulebook for a moment, then put v1.1 back."""
    with psycopg.connect(url) as c:
        rulebook.activate_version(c, name, url=url, by="test")
    try:
        yield
    finally:
        with psycopg.connect(url) as c:
            rulebook.activate_version(c, "v1.1", url=url, by="test")


def lines(url, ctx):
    with psycopg.connect(url) as c:
        return c.execute(
            "SELECT outcome, approver_name, inputs, outputs FROM action_log "
            "WHERE run_id = %s AND tool = 'class_baseline' ORDER BY event_id",
            (ctx.run_id,),
        ).fetchall()


def ask(ctx, stage="Form 1", term=3, school=None):
    """One call. `school` changes the school of the session first (the test's own set-up, standing
    in for a different signed-in person), so one connection serves the whole test."""
    if school is not None:
        ctx.school = school
    return toolbox.class_baseline(ctx, stage=stage, term=term)


def area(result, name):
    return next(a for a in result["areas"] if a["area"] == name)


def shown(a):
    return {b["bin"]: b["count"] for b in a["bins"] if b["shown"]}


def hidden(a):
    return [b["bin"] for b in a["bins"] if not b["shown"]]


# --------------------------------------------------------------------------- refusals, all logged
def test_with_the_view_off_it_is_refused_and_the_refusal_is_in_the_diary(
    make_ctx, schema, monkeypatch
):
    monkeypatch.delenv("CLASS_VIEW_ENABLED", raising=False)
    ctx = make_ctx()
    result = ask(ctx)
    assert result["status"] == "refused" and "class_view_locked" in result["reason"]
    assert [row[:2] for row in lines(schema, ctx)] == [("refused", "Ada Teacher")]


def test_with_no_approver_it_is_refused_and_the_diary_has_no_name(make_ctx, schema):
    ctx = make_ctx(approver=None)
    result = ask(ctx)
    assert result["status"] == "refused" and "approver" in result["reason"].lower()
    assert [row[:2] for row in lines(schema, ctx)] == [("refused", None)]


def test_with_no_school_in_the_session_it_is_refused_with_a_typed_reason(make_ctx, schema):
    ctx = make_ctx(school_code=None)
    result = ask(ctx)
    assert result["status"] == "refused"
    assert result["reason"].startswith("no_school:")
    assert [row[:2] for row in lines(schema, ctx)] == [("refused", "Ada Teacher")]


def test_an_empty_school_code_counts_as_no_school(make_ctx):
    assert ask(make_ctx(school_code=""))["reason"].startswith("no_school:")


@pytest.mark.parametrize("extra", ["school_id", "school", "template_version", "approver"])
def test_the_tool_has_no_argument_for_a_school_a_version_or_a_person(make_ctx, extra):
    with pytest.raises(TypeError):
        toolbox.class_baseline(make_ctx(), stage="Form 1", term=3, **{extra: "x"})


def test_a_stage_the_rulebook_does_not_have_is_refused(make_ctx):
    result = ask(make_ctx(), stage="Form 9")
    assert result["status"] == "refused" and "Form 9" in result["reason"]


def test_an_old_rulebook_with_no_class_view_rules_refuses_with_a_typed_reason(make_ctx, schema):
    with active(schema, "v1"):
        result = ask(make_ctx())
    assert result["status"] == "refused" and result["reason"].startswith("class_rules_missing:")


# --------------------------------------------------------------------------- the group
def test_only_the_sessions_school_is_counted(make_ctx, schema, school):
    other = school + "X"
    add_class(schema, school, (4, 4, 4, 0, 0))  # 12 learners
    add_class(schema, other, (6, 6, 0, 0, 0))  # 12 more, in another school
    ctx = make_ctx()
    result = ask(ctx)
    assert result["status"] == "ok" and result["group_size"] == 12
    assert result["school_id"] == school
    assert ask(ctx, school=other)["group_size"] == 12


def test_the_class_is_the_most_recent_school_year_at_the_stage(make_ctx, schema, school):
    add_class(schema, school, (0, 5, 5, 0, 0), year="2024/2025")  # an earlier Form 1
    add_class(schema, school, (4, 4, 4, 0, 0), year="2025/2026")  # the class: 12
    result = ask(make_ctx())
    assert result["academic_year"] == "2025/2026"
    assert result["group_size"] == 12, "the 10 learners of the earlier year are not counted"
    # A later year takes over.
    add_class(schema, school, (0, 10, 0, 0, 0), year="2026/2027")
    later = ask(make_ctx())
    assert later["academic_year"] == "2026/2027" and later["group_size"] == 10


def test_another_stage_is_its_own_class(make_ctx, schema, school):
    add_class(schema, school, (4, 4, 4, 0, 0), stage="Form 1")
    add_class(schema, school, (2, 2, 2, 0, 0), stage="Form 2")
    ctx = make_ctx()
    assert ask(ctx, stage="Form 1")["group_size"] == 12
    assert ask(ctx, stage="Form 2")["status"] == "suppressed"


def test_a_school_with_no_learners_gets_the_same_reply_as_a_small_class(make_ctx):
    result = ask(make_ctx())
    assert result["status"] == "suppressed" and "group_size" not in result
    assert result["academic_year"] is None


def test_a_group_of_exactly_the_minimum_is_shown_and_one_fewer_is_not_under_v1_1(
    make_ctx, schema, school
):
    add_class(schema, school, (5, 5, 0, 0, 0))
    add_class(schema, school + "Y", (5, 4, 0, 0, 0))
    ctx = make_ctx()
    full = ask(ctx)
    assert full["status"] == "ok" and full["group_size"] == 10
    short = ask(ctx, school=school + "Y")
    assert short["status"] == "suppressed" and short["minimum_group_size"] == 10


def test_a_group_of_exactly_the_minimum_is_shown_and_one_fewer_is_not_under_v2_1_strict(
    make_ctx, schema, school
):
    add_class(schema, school, (15, 0, 0, 0, 0))
    add_class(schema, school + "Y", (14, 0, 0, 0, 0))
    ctx = make_ctx()
    with active(schema, "v2.1-strict"):
        full = ask(ctx)
        short = ask(ctx, school=school + "Y")
    assert full["status"] == "ok" and full["group_size"] == 15
    assert full["template_version"] == "v2.1-strict" and full["minimum_cell_size"] == 10
    assert short["status"] == "suppressed" and short["minimum_group_size"] == 15


# --------------------------------------------------------------------------- the counts
def test_a_class_worked_by_hand_shows_the_counts_coverage_and_areas_in_order(
    make_ctx, schema, school
):
    # Group 20: numeracy 6 below, 8 within, 6 above. Every count is 5 or more, so none is hidden.
    add_class(schema, school, (6, 8, 6, 0, 0))
    result = ask(make_ctx())
    assert result["status"] == "ok" and result["group_size"] == 20
    assert result["template_version"] == "v1.1" and result["term"] == 3
    assert [a["area"] for a in result["areas"]] == ["literacy", "numeracy"]
    numeracy = area(result, "numeracy")
    assert shown(numeracy) == {
        "below_expected_range": 6, "within_expected_range": 8, "above_expected_range": 6,
        "not_enough_evidence": 0, "blocked": 0,
    }  # fmt: skip
    assert numeracy["coverage"] == {"with_enough_evidence": 20, "group_size": 20}
    assert numeracy["suppressed"] is False
    literacy = area(result, "literacy")
    assert literacy["status"] == "no_usable_evidence"
    assert literacy["coverage"] == {"with_enough_evidence": 0, "group_size": 20}


@pytest.mark.parametrize(
    "counts,short,blocked",
    [((5, 5, 5, 5, 0), 5, 0), ((5, 5, 5, 0, 5), 0, 5)],
    ids=["five short of evidence", "five with a blocked subject"],
)
def test_short_of_evidence_and_blocked_learners_are_counted_in_their_own_bins(
    make_ctx, schema, school, counts, short, blocked
):
    add_class(schema, school, counts)
    numeracy = area(ask(make_ctx()), "numeracy")
    assert shown(numeracy)["not_enough_evidence"] == short
    assert shown(numeracy)["blocked"] == blocked
    assert numeracy["coverage"] == {"with_enough_evidence": 15, "group_size": 20}


def test_a_small_count_is_hidden_with_its_partner_and_coverage_is_withheld(
    make_ctx, schema, school
):
    # Numeracy 3 below, 9 within, 8 above (20). 3 is hidden; of the others above 0 the smallest is
    # 8, so above is hidden too. Shown: within 9 and the two zeros.
    add_class(schema, school, (3, 9, 8, 0, 0))
    result = ask(make_ctx())
    numeracy = area(result, "numeracy")
    assert hidden(numeracy) == ["below_expected_range", "above_expected_range"]
    assert shown(numeracy) == {
        "within_expected_range": 9, "not_enough_evidence": 0, "blocked": 0,
    }  # fmt: skip
    assert numeracy["coverage"] is None and numeracy["suppressed"] is True
    assert numeracy["suppression"]["clause_ids"] == ["GM-2", "GM-3"]
    clauses = {r["id"]: r["text"] for r in result["rules"]}
    assert "never chosen" in clauses["GM-3"] and "fewer than 5" in clauses["GM-2"]


def test_the_counts_stop_at_the_term_asked_for(make_ctx, schema, school):
    add_class(schema, school, (6, 8, 6, 0, 0))
    by_term_two = ask(make_ctx(), term=2)
    # Two terms of scores is fewer than the rulebook's minimum, so nobody has enough evidence.
    assert area(by_term_two, "numeracy")["status"] == "no_usable_evidence"
    assert by_term_two["term"] == 2 and by_term_two["group_size"] == 20


def test_the_answer_holds_no_name_learner_number_record_number_or_school_reference(
    make_ctx, schema, school
):
    add_class(schema, school, (6, 8, 6, 0, 0))
    text = json.dumps(ask(make_ctx()))
    assert "Pupil Name" not in text
    assert not re.search(r"\bL-\d", text) and not re.search(r"\bA-\d", text)
    assert f"{school}/" not in text
    assert "learner_id" not in text


def test_each_area_and_the_answer_have_a_finding_number(make_ctx, schema, school):
    add_class(schema, school, (6, 8, 6, 0, 0))
    result = ask(make_ctx())
    for block in (result, *result["areas"]):
        assert block["finding_id"] == "CF-" + block["result_hash"][:8]


def test_the_same_question_twice_on_one_context_gives_the_same_answer(make_ctx, schema, school):
    add_class(schema, school, (3, 9, 8, 0, 0))
    ctx = make_ctx()
    assert ask(ctx) == ask(ctx)


def test_the_answer_is_the_same_on_a_second_school_with_the_same_counts_except_for_the_hash(
    make_ctx, schema, school
):
    add_class(schema, school, (6, 8, 6, 0, 0))
    add_class(schema, school + "Y", (6, 8, 6, 0, 0))
    ctx = make_ctx()
    first, second = ask(ctx), ask(ctx, school=school + "Y")
    assert first["areas"][1]["bins"] == second["areas"][1]["bins"]
    assert first["result_hash"] != second["result_hash"], "the school is part of what is hashed"


# ------------------------------------------------------------------ it writes nothing but the diary
def test_a_class_with_a_blocked_learner_saves_no_issue_rows(make_ctx, schema, school):
    add_class(schema, school, (0, 5, 5, 0, 5))
    ctx = make_ctx()
    numeracy = area(ask(ctx), "numeracy")
    assert shown(numeracy)["blocked"] == 5
    with psycopg.connect(schema) as c:
        saved = c.execute(
            "SELECT count(*) FROM issues i JOIN learners l USING (learner_id) "
            "WHERE l.school_code = %s",
            (school,),
        ).fetchone()[0]
    assert saved == 0, "a class view is a read: it saves nothing about the learners it looks at"


def test_one_diary_line_per_call_with_the_approver_and_only_stage_and_term_as_inputs(
    make_ctx, schema, school
):
    add_class(schema, school, (6, 8, 6, 0, 0))
    ctx = make_ctx()
    ask(ctx)
    ask(ctx, term=2)
    rows = lines(schema, ctx)
    assert [r[0] for r in rows] == ["ok", "ok"]
    assert {r[1] for r in rows} == {"Ada Teacher"}
    assert [r[2] for r in rows] == [{"stage": "Form 1", "term": 3}, {"stage": "Form 1", "term": 2}]
    assert "Pupil Name" not in json.dumps([r[3] for r in rows])


# --------------------------------------------------------------------------- the old tool
def test_stage_gap_counts_refuses_a_school_that_is_not_the_sessions_school(
    make_ctx, schema, school
):
    add_class(schema, school, (4, 4, 4, 0, 0))
    ctx = make_ctx()
    args = {"stage": "Form 1", "skill": "numeracy", "term": 3}
    refused = toolbox.stage_gap_counts(ctx, school_id=school + "Z", **args)
    assert refused["status"] == "refused" and refused["reason"].startswith("school_mismatch:")
    same = toolbox.stage_gap_counts(ctx, school_id=school, **args)
    assert same["status"] == "ok" and same["group_size"] == 12


def test_stage_gap_counts_with_no_school_in_the_session_behaves_as_before(
    make_ctx, schema, school
):
    add_class(schema, school, (4, 4, 4, 0, 0))
    ctx = make_ctx(school_code=None)
    result = toolbox.stage_gap_counts(
        ctx, school_id=school, stage="Form 1", skill="numeracy", term=3
    )
    assert result["status"] == "ok" and result["group_size"] == 12
