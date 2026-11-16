"""The lesson store (T-060, slice 3). The first group needs no database; the second is integration
(DATABASE_URL) in a throwaway schema, one connection per test.

What these tests hold the code to: every function takes the context first and nothing that names a
person or a school; the author and the approver are the signed-in user; only `approve_lesson_plan`
can make a plan `approved`; notes that name a learner store nothing and leave no name in the diary;
nothing is stored when there is not enough class evidence; and the stored final text is what a fresh
apply of the accepted edits gives. Expected texts are typed by hand.
"""

import ast
import hashlib
import inspect
import json
import re

import psycopg
import pytest
from dbtools import run_migrate
from lessonkit import (
    A_ASK,
    A_PLENARY,
    A_STARTER,
    BINS,
    FINDING_ID,
    LESSON,
    OBJECTIVE,
    VERSION,
    content,
    limits,
)
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from mdeis.db import diary
from mdeis.lessons import store
from mdeis.tools.context import Approver, ToolContext

FUNCTIONS = [
    "guard_lesson_notes", "save_lesson_plan", "get_lesson_plan", "approve_lesson_plan",
    "reject_lesson_plan",
]
NAMING_A_PERSON_OR_SCHOOL = {
    "approver", "approver_id", "approver_name", "author", "author_id", "author_name", "user",
    "user_id", "user_name", "tutor", "teacher", "person", "who", "signed_in", "decided_by",
    "school", "school_id", "school_code", "roster", "learners", "names",
}


# --------------------------------------------------------------------------- no database needed
def test_the_store_offers_exactly_these_functions():
    public = {
        n for n, f in inspect.getmembers(store, inspect.isfunction)
        if not n.startswith("_") and f.__module__ == store.__name__
    }
    assert public == set(FUNCTIONS) | set(getattr(store, "PUBLIC_HELPERS", ()))


@pytest.mark.parametrize("name", FUNCTIONS)
def test_every_function_takes_the_context_first_and_no_argument_naming_a_person_or_school(name):
    parameters = list(inspect.signature(getattr(store, name)).parameters)
    assert parameters[0] == "ctx"
    assert not NAMING_A_PERSON_OR_SCHOOL & set(parameters), parameters


def test_the_arguments_of_the_two_functions_that_matter_most_are_exactly_these():
    assert list(inspect.signature(store.approve_lesson_plan).parameters) == [
        "ctx", "plan_id", "accepted_edit_ids",
    ]
    assert list(inspect.signature(store.save_lesson_plan).parameters) == [
        "ctx", "stage", "area", "term", "objective", "notes", "model_output", "model",
        "prompt_sha256",
    ]


def functions_in_store():
    tree = ast.parse(inspect.getsource(store))
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def strings_in(function):
    body = function.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return [
        n.value for stmt in body for n in ast.walk(stmt)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def test_only_the_approval_function_writes_the_approved_status():
    writers = {
        name for name, fn in functions_in_store().items()
        if any(re.search(r"status\s*=\s*'approved'", s) or s == "approved" for s in strings_in(fn))
    }
    assert writers == {"approve_lesson_plan"}


LOG_NAMES = {
    "guard_lesson_notes": "lesson_guard_notes", "save_lesson_plan": "lesson_save_plan",
    "get_lesson_plan": "lesson_get_plan", "approve_lesson_plan": "lesson_approve_plan",
    "reject_lesson_plan": "lesson_reject_plan",
}


def test_every_function_is_logged_under_its_own_name_and_the_text_arguments_are_redacted():
    source = inspect.getsource(store)
    for name, logged in LOG_NAMES.items():
        assert re.search(rf'logged_tool\(\s*"{logged}"', source), name
    assert re.search(r'"lesson_guard_notes",\s*redact=\("notes", "objective"\)', source)
    assert re.search(
        r'"lesson_save_plan",\s*redact=\("notes", "objective", "model_output"\)', source
    )


def test_the_signed_in_person_comes_from_the_context_in_every_function():
    assert "ctx.approver" in inspect.getsource(store._signed_in)
    for name in FUNCTIONS:
        body = ast.unparse(functions_in_store()[name])
        assert "_signed_in(ctx)" in body, name


# --------------------------------------------------------------------------- with a database
AUTHOR = Approver("u-1", "Ms Tutor")
OTHER = Approver("u-2", "Mr Other")
SCHOOL = "TST"
RUN = "RUN-T"

FOR_DB = pytest.mark.integration


@pytest.fixture
def db(scratch_schema):
    schema, url = scratch_schema
    built = run_migrate(url)
    assert built.returncode == 0, built.stderr
    with psycopg.connect(url) as conn:
        conn.schema, conn.url = schema, url
        install_template(conn, "t-lessons", content(), active=True)
        conn.execute(
            "INSERT INTO learners (school_code, school_ref, display_name) VALUES "
            "('TST', 'TST/2025/014', 'Amara Nfor'), ('TST', 'TST/2025/015', 'Joy Mbeki')"
        )
        yield conn
        conn.rollback()


def install_template(conn, name, body, active=False):
    conn.execute(
        "INSERT INTO templates (version_name, file_name, sha256, template_id, content, active) "
        "VALUES (%s, %s, repeat('a', 64), 'tid', %s, %s)",
        (name, name + ".yaml", Jsonb(body), active),
    )


def activate(conn, name):
    conn.execute("UPDATE templates SET active = false WHERE active")
    conn.execute("UPDATE templates SET active = true WHERE version_name = %s", (name,))


def ctx(db, who=AUTHOR, school=SCHOOL, run=RUN):
    made = ToolContext(conn=db, run_id=run, approver=who, database_url=db.url, commit=False)
    made.school = school  # the session's school; set by the server once the session carries one
    return made


def class_result(**over):
    base = {
        "status": "ok", "school_id": SCHOOL, "stage": "Form 3", "term": 2,
        "academic_year": "2025/2026", "template_version": VERSION, "group_size": 22,
        "finding_id": "CF-00000001", "clause_ids": ["GM-1"],
        "areas": [{
            "area": "literacy", "status": "ok", "bins": BINS, "finding_id": FINDING_ID,
            "clause_ids": ["SK-1", "ST-3", "TR-1", "CA-5", "GM-1", "GM-2", "GM-3"],
        }],
    }
    base.update(over)
    return base


def class_read(db, result=None, run=RUN, tool="class_baseline"):
    """What a class tool wrote to the diary in this run, as the lesson tools will find it."""
    diary.write(tool, run_id=run, inputs={"stage": "Form 3", "term": 2},
                outputs=class_result() if result is None else result, outcome="ok", url=db.url)


def edit(**over):
    base = {
        "id": "e1", "type": "reword", "anchor_quote": A_ASK,
        "replacement": "Ask what the passage tells us and why.", "finding_ids": [FINDING_ID],
        "resource_ids": [], "suggestion": "Give learners a question that asks for a reason.",
    }
    base.update(over)
    return base


def second_edit(**over):
    return edit(id="e2", type="add_check", anchor_quote=A_PLENARY,
                replacement=A_PLENARY + " Ask each pair for one reason.", **over)


def output(*edits):
    return json.dumps({"edits": list(edits)})


def save(db, edits=None, notes=LESSON, who=AUTHOR, **over):
    args = {
        "stage": "Form 3", "area": "literacy", "term": 2, "objective": OBJECTIVE, "notes": notes,
        "model_output": output(edit()) if edits is None else edits, "model": "model-x",
        "prompt_sha256": "b" * 64, **over,
    }
    return store.save_lesson_plan(ctx(db, who), **args)


def stored(db, plan_id):
    return db.cursor(row_factory=dict_row).execute(
        "SELECT * FROM lesson_plans WHERE plan_id = %s", (plan_id,)
    ).fetchone()


def plan_count(db):
    return db.execute("SELECT count(*) FROM lesson_plans").fetchone()[0]


def log_rows(db, tool, run=RUN):
    return db.cursor(row_factory=dict_row).execute(
        "SELECT * FROM action_log WHERE run_id = %s AND tool = %s ORDER BY event_id", (run, tool)
    ).fetchall()


def stored_ok(db, **over):
    class_read(db)
    result = save(db, **over)
    assert result["status"] == "stored", result
    return result


# --------------------------------------------------------------------------- saving a plan
@FOR_DB
def test_a_plan_is_stored_for_the_signed_in_author_with_the_evidence_it_rests_on(db):
    result = stored_ok(db)
    got = stored(db, result["plan_id"])
    assert got["status"] == "proposed"
    assert (got["author_id"], got["author_name"], got["school_code"]) == ("u-1", "Ms Tutor", "TST")
    assert (got["stage"], got["area"], got["term"]) == ("Form 3", "literacy", 2)
    assert got["original_text"] == LESSON and got["objective_text"] == OBJECTIVE
    assert got["original_sha256"] == hashlib.sha256(LESSON.encode()).hexdigest()
    assert got["revised_text"] == LESSON.replace(A_ASK, "Ask what the passage tells us and why.")
    assert got["template_version"] == VERSION
    assert got["pins"]["template_version"] == VERSION and got["pins"]["template_sha256"] == "a" * 64
    assert [e["id"] for e in got["edits"]["kept"]] == ["e1"] and got["edits"]["dropped"] == []
    assert got["check_result"]["passed"] is True
    assert (got["model"], got["prompt_sha256"], got["run_id"]) == ("model-x", "b" * 64, RUN)
    (found,) = got["evidence"]
    assert found["finding_id"] == FINDING_ID
    assert found["sentence"].splitlines()[0].startswith(
        "Evidence (literacy, Form 3, term 2): 14 of 22 learners."
    )
    assert found["sentence"].splitlines()[0].endswith(f"template {VERSION}, {FINDING_ID}]")


@FOR_DB
def test_the_result_pairs_each_kept_edit_with_its_evidence_sentence_and_lists_what_was_dropped(db):
    class_read(db)
    bad = edit(id="e2", anchor_quote=A_PLENARY, replacement="Share and say why.",
               suggestion="Give the weak readers a short passage.")
    result = save(db, output(edit(), bad))
    assert result["status"] == "stored"
    (kept,) = result["edits"]
    assert kept["id"] == "e1" and kept["replacement"] == edit()["replacement"]
    assert kept["evidence"][0].startswith("Evidence (literacy, Form 3, term 2): 14 of 22")
    (dropped,) = result["dropped"]
    assert dropped["id"] == "e2" and dropped["lc"] == ["LC-6"] and dropped["repairable"] is True
    assert "WD-1" in dropped["reason"]
    assert result["objective"] == OBJECTIVE
    assert result["revised_text"].count("Share and say why.") == 0


@FOR_DB
def test_a_dropped_edit_leaves_only_its_id_its_lc_and_a_plain_reason_in_the_stored_plan(db):
    class_read(db)
    bad = edit(id="e2", anchor_quote=A_PLENARY, replacement="Share and say why.",
               suggestion="Give the weak readers a short passage.")
    result = save(db, output(edit(), bad))
    row = stored(db, result["plan_id"])
    assert set(row["edits"]["dropped"][0]) == {"id", "lc", "reason"}
    everything = json.dumps({k: v for k, v in row.items()}, default=str)
    assert "weak readers" not in everything and "Share and say why" not in everything


@FOR_DB
def test_the_stored_revision_is_what_a_fresh_apply_of_the_kept_edits_gives(db):
    from mdeis.lessons.apply import apply_edits
    from mdeis.lessons.edits import parse_edits
    from mdeis.lessons.parse import parse_lesson

    class_read(db)
    result = save(db, output(edit(), second_edit()))
    row = stored(db, result["plan_id"])
    fresh = apply_edits(
        parse_lesson(row["original_text"], row["objective_text"]),
        parse_edits({"edits": row["edits"]["kept"]}).edits,
    )
    assert fresh.text == row["revised_text"]
    assert row["revised_text"] == (
        LESSON.replace(A_ASK, "Ask what the passage tells us and why.")
        .replace(A_PLENARY, A_PLENARY + " Ask each pair for one reason.")
    )


@FOR_DB
def test_the_author_is_the_signed_in_user_and_nobody_signed_in_stores_nothing(db):
    class_read(db)
    result = save(db, who=None)
    assert result["status"] == "refused" and "signed in" in result["reason"]
    assert plan_count(db) == 0


@FOR_DB
def test_a_session_with_no_school_stores_nothing(db):
    class_read(db)
    made = ctx(db, school=None)
    result = store.save_lesson_plan(
        made, stage="Form 3", area="literacy", term=2, objective=OBJECTIVE, notes=LESSON,
        model_output=output(edit()), model="m", prompt_sha256="b" * 64,
    )
    assert result["status"] == "refused" and "school" in result["reason"]
    assert plan_count(db) == 0


@FOR_DB
def test_a_selection_the_rulebook_does_not_know_stores_nothing(db):
    class_read(db)
    for over in (dict(stage="Form 9"), dict(area="poetry"), dict(term=4)):
        assert save(db, **over)["status"] == "refused"
    assert plan_count(db) == 0


# ------------------------------------------------------------------------ notes that name a learner
@FOR_DB
@pytest.mark.parametrize(
    "notes",
    ["## A\nLet Amara lead the reading.\n", "## A\nAsk Joy.\n", "## A\nSee L-0007 and read.\n",
     "## A\nCheck TST/2025/014 first.\n"],
)
def test_notes_that_name_a_learner_store_nothing_and_leave_no_name_in_the_diary(db, notes):
    class_read(db)
    result = save(db, notes=notes, edits=output(edit(anchor_quote="Ask what.")))
    assert result["status"] == "refused"
    assert "learner" in result["reason"]
    assert plan_count(db) == 0
    rows = log_rows(db, "lesson_save_plan")
    assert len(rows) == 1 and rows[0]["outcome"] == "refused"
    flat = json.dumps(rows[0], default=str).lower()
    for secret in ("amara", "joy", "l-0007", "tst/2025/014", "nfor", "mbeki"):
        assert secret not in flat, secret
    assert rows[0]["inputs"]["notes"]["sha256"] == hashlib.sha256(notes.encode()).hexdigest()


@FOR_DB
def test_an_objective_that_names_a_learner_is_refused_too(db):
    class_read(db)
    result = save(db, objective="Amara explains the main idea.")
    assert result["status"] == "refused" and plan_count(db) == 0


@FOR_DB
def test_the_guard_tool_refuses_names_empty_notes_and_notes_over_the_templates_limit(db):
    def guard(notes, objective=OBJECTIVE):
        return store.guard_lesson_notes(
            ctx(db), stage="Form 3", area="literacy", term=2, objective=objective, notes=notes
        )

    ok = guard(LESSON)
    assert ok["status"] == "ok"
    assert ok["notes_sha256"] == hashlib.sha256(LESSON.encode()).hexdigest()
    assert ok["length"] == len(LESSON) and "text" not in ok and "notes" not in ok
    assert guard("Let Amara read.")["status"] == "refused"
    assert guard("  \n")["status"] == "refused"
    assert guard(LESSON, objective="Joy explains it.")["status"] == "refused"
    small = content(limits(max_notes_chars=20))
    install_template(db, "t-small", small)
    activate(db, "t-small")
    assert guard(LESSON)["status"] == "refused"
    flat = json.dumps(log_rows(db, "lesson_guard_notes"), default=str).lower()
    assert "amara" not in flat and "joy" not in flat


# --------------------------------------------------------------------------- no usable evidence
@FOR_DB
def test_with_no_class_read_in_the_run_nothing_is_stored_and_the_statement_is_plain(db):
    result = save(db)
    assert result["status"] == "insufficient_evidence"
    assert result["statement"] == "There is not enough class evidence to adapt this lesson."
    assert plan_count(db) == 0
    assert len(log_rows(db, "lesson_save_plan")) == 1


@FOR_DB
def test_a_suppressed_class_stores_nothing_and_the_statement_shows_no_count(db):
    class_read(db, {"status": "suppressed", "school_id": SCHOOL, "stage": "Form 3", "term": 2,
                    "template_version": VERSION, "finding_id": FINDING_ID, "clause_ids": ["GM-1"]})
    result = save(db)
    assert result["status"] == "insufficient_evidence"
    assert result["statement"] == (
        "There is not enough class evidence to adapt this lesson. "
        f"Groups smaller than 10 are never shown. [GM-1, template {VERSION}, {FINDING_ID}]"
    )
    assert plan_count(db) == 0


@FOR_DB
def test_a_class_with_no_gap_stores_nothing_and_says_there_is_no_gap(db):
    bins = [{"bin": "below_expected_range", "shown": True, "count": 0},
            {"bin": "within_expected_range", "shown": True, "count": 20},
            {"bin": "above_expected_range", "shown": True, "count": 2}]
    class_read(db, class_result(areas=[{**class_result()["areas"][0], "bins": bins}]))
    result = save(db)
    assert result["status"] == "no_gap"
    assert result["statement"].startswith("No class gap against the expected range is shown")
    assert plan_count(db) == 0


@FOR_DB
def test_a_class_read_in_another_run_or_for_another_school_is_not_evidence(db):
    class_read(db, run="RUN-OTHER")
    assert save(db)["status"] == "insufficient_evidence"
    class_read(db, class_result(school_id="OTHER"))
    assert save(db)["status"] == "insufficient_evidence"
    class_read(db, tool="get_criterion")  # a tool that is not a class tool
    assert save(db)["status"] == "insufficient_evidence"
    assert plan_count(db) == 0


@FOR_DB
def test_a_finding_the_model_makes_up_is_dropped_and_nothing_is_stored_when_none_is_left(db):
    class_read(db)
    result = save(db, output(edit(finding_ids=["CF-deadbeef"])))
    assert result["status"] == "no_valid_edits"
    assert result["dropped"][0]["lc"] == ["LC-2"] and result["dropped"][0]["repairable"] is False
    assert plan_count(db) == 0


@FOR_DB
def test_overlapping_edits_are_both_dropped_and_nothing_is_stored(db):
    class_read(db)
    a = edit(id="e1", anchor_quote="Read the short passage aloud",
             replacement="Read the passage aloud")
    b = edit(id="e2", anchor_quote="passage aloud to the class.",
             replacement="text aloud to the class.")
    result = save(db, output(a, b))
    assert result["status"] == "no_valid_edits"
    assert [d["id"] for d in result["dropped"]] == ["e1", "e2"]
    assert plan_count(db) == 0


@FOR_DB
def test_output_that_is_not_the_contract_is_one_invalid_result_and_nothing_is_stored(db):
    class_read(db)
    for bad in ("not json", output(edit(type="resequence")), json.dumps({"revised": LESSON})):
        result = save(db, bad)
        assert result["status"] == "invalid_output" and result["repairable"] is True
        assert LESSON not in json.dumps(result)
    assert plan_count(db) == 0


@FOR_DB
def test_a_rulebook_with_no_lesson_rules_refuses_the_lesson_tools(db):
    body = content()
    del body["lessons"]
    install_template(db, "t-old", body)
    activate(db, "t-old")
    class_read(db)
    result = save(db)
    assert result["status"] == "refused" and "lessons" in result["reason"]
    assert plan_count(db) == 0


# --------------------------------------------------------------------------- approving
@FOR_DB
def test_the_author_approves_and_the_final_text_has_only_the_accepted_edits(db):
    class_read(db)
    plan = save(db, output(edit(), second_edit()))["plan_id"]
    result = store.approve_lesson_plan(ctx(db), plan_id=plan, accepted_edit_ids=["e2"])
    assert result["status"] == "approved"
    got = stored(db, plan)
    assert got["status"] == "approved"
    assert (got["approver_id"], got["approver_name"]) == ("u-1", "Ms Tutor")
    assert got["accepted_edit_ids"] == ["e2"] and got["approved_at"] is not None
    only_second = LESSON.replace(A_PLENARY, A_PLENARY + " Ask each pair for one reason.")
    assert got["final_text"] == only_second
    assert A_ASK in got["final_text"]  # the rejected edit is not in the final text
    assert got["original_text"] == LESSON  # the original is never changed


@FOR_DB
def test_accepting_every_edit_gives_the_text_with_all_of_them(db):
    class_read(db)
    plan = save(db, output(edit(), second_edit()))["plan_id"]
    store.approve_lesson_plan(ctx(db), plan_id=plan, accepted_edit_ids=["e1", "e2"])
    assert stored(db, plan)["final_text"] == stored(db, plan)["revised_text"]


@FOR_DB
def test_the_approver_in_the_diary_is_the_signed_in_user(db):
    class_read(db)
    plan = save(db)["plan_id"]
    store.approve_lesson_plan(ctx(db), plan_id=plan, accepted_edit_ids=["e1"])
    (row,) = log_rows(db, "lesson_approve_plan")
    assert (row["approver_id"], row["approver_name"], row["outcome"]) == ("u-1", "Ms Tutor", "ok")


@FOR_DB
@pytest.mark.parametrize("who", [None, OTHER], ids=["nobody", "another user"])
def test_only_the_signed_in_author_can_approve(db, who):
    class_read(db)
    plan = save(db)["plan_id"]
    result = store.approve_lesson_plan(ctx(db, who), plan_id=plan, accepted_edit_ids=["e1"])
    assert result["status"] == "refused"
    assert stored(db, plan)["status"] == "proposed" and stored(db, plan)["approver_id"] is None


@FOR_DB
@pytest.mark.parametrize(
    "accepted",
    [[], ["e9"], ["e1", "e1"], "e1", None, [1], ["e1", "e9"]],
    ids=["none", "unknown", "twice", "a string", "null", "a number", "one unknown"],
)
def test_approval_needs_at_least_one_accepted_edit_and_each_must_be_a_kept_one(db, accepted):
    class_read(db)
    plan = save(db)["plan_id"]
    result = store.approve_lesson_plan(ctx(db), plan_id=plan, accepted_edit_ids=accepted)
    assert result["status"] == "refused"
    assert stored(db, plan)["status"] == "proposed"


@FOR_DB
def test_a_plan_that_is_not_proposed_cannot_be_approved_again_or_after_rejection(db):
    class_read(db)
    first = save(db)["plan_id"]
    store.approve_lesson_plan(ctx(db), plan_id=first, accepted_edit_ids=["e1"])
    again = store.approve_lesson_plan(ctx(db), plan_id=first, accepted_edit_ids=["e1"])
    assert again["status"] == "refused"
    second = save(db)["plan_id"]
    assert store.reject_lesson_plan(ctx(db), plan_id=second)["status"] == "rejected"
    late = store.approve_lesson_plan(ctx(db), plan_id=second, accepted_edit_ids=["e1"])
    assert late["status"] == "refused" and stored(db, second)["status"] == "rejected"


@FOR_DB
def test_an_unknown_plan_is_refused(db):
    result = store.approve_lesson_plan(ctx(db), plan_id="LP-999999", accepted_edit_ids=["e1"])
    assert result["status"] == "refused"


@FOR_DB
def test_a_plan_of_another_school_cannot_be_approved_or_read(db):
    class_read(db)
    plan = save(db)["plan_id"]
    elsewhere = ctx(db, school="OTHER")
    result = store.approve_lesson_plan(elsewhere, plan_id=plan, accepted_edit_ids=["e1"])
    assert result["status"] == "refused"
    assert store.get_lesson_plan(elsewhere, plan_id=plan)["status"] == "refused"


@FOR_DB
def test_no_argument_can_name_the_approver(db):
    class_read(db)
    plan = save(db)["plan_id"]
    with pytest.raises(TypeError):
        store.approve_lesson_plan(ctx(db, OTHER), plan_id=plan, accepted_edit_ids=["e1"],
                                  approver_id="u-1")
    with pytest.raises(TypeError):
        store.save_lesson_plan(ctx(db), stage="Form 3", area="literacy", term=2,
                               objective=OBJECTIVE, notes=LESSON, model_output=output(edit()),
                               model="m", prompt_sha256="b" * 64, author_id="u-9")


@FOR_DB
def test_a_model_output_that_tries_to_name_an_approver_or_set_a_status_is_not_valid(db):
    class_read(db)
    sneaky = json.dumps({"edits": [edit()], "approver_id": "u-1", "status": "approved"})
    assert save(db, sneaky)["status"] == "invalid_output"
    assert plan_count(db) == 0


# --------------------------------------------------------------------------- rejecting and reading
@FOR_DB
def test_only_the_author_can_reject_and_a_rejection_closes_the_plan(db):
    class_read(db)
    plan = save(db)["plan_id"]
    assert store.reject_lesson_plan(ctx(db, OTHER), plan_id=plan)["status"] == "refused"
    assert store.reject_lesson_plan(ctx(db, None), plan_id=plan)["status"] == "refused"
    assert store.reject_lesson_plan(ctx(db), plan_id=plan)["status"] == "rejected"
    assert stored(db, plan)["status"] == "rejected" and stored(db, plan)["approver_id"] is None
    assert store.reject_lesson_plan(ctx(db), plan_id=plan)["status"] == "refused"


@FOR_DB
def test_the_author_can_read_their_plan_and_nobody_else_can(db):
    class_read(db)
    plan = save(db)["plan_id"]
    mine = store.get_lesson_plan(ctx(db), plan_id=plan)
    assert mine["status"] == "ok" and mine["plan_status"] == "proposed"
    assert mine["original_text"] == LESSON and mine["objective"] == OBJECTIVE
    assert mine["edits"][0]["evidence"][0].startswith("Evidence (literacy")
    assert store.get_lesson_plan(ctx(db, OTHER), plan_id=plan)["status"] == "refused"
    assert store.get_lesson_plan(ctx(db, None), plan_id=plan)["status"] == "refused"


# --------------------------------------------------------------------------- the diary
@FOR_DB
def test_every_function_writes_one_diary_row_including_a_refusal(db):
    class_read(db)
    plan = save(db)["plan_id"]
    store.get_lesson_plan(ctx(db), plan_id=plan)
    store.approve_lesson_plan(ctx(db, OTHER), plan_id=plan, accepted_edit_ids=["e1"])  # refused
    store.approve_lesson_plan(ctx(db), plan_id=plan, accepted_edit_ids=["e1"])
    store.guard_lesson_notes(ctx(db), stage="Form 3", area="literacy", term=2,
                             objective=OBJECTIVE, notes=LESSON)
    second = save(db)["plan_id"]
    store.reject_lesson_plan(ctx(db), plan_id=second)
    counts = {t: len(log_rows(db, t)) for t in (
        "lesson_save_plan", "lesson_get_plan", "lesson_approve_plan",
        "lesson_guard_notes", "lesson_reject_plan")}
    assert counts == {
        "lesson_save_plan": 2, "lesson_get_plan": 1, "lesson_approve_plan": 2,
        "lesson_guard_notes": 1, "lesson_reject_plan": 1,
    }
    outcomes = [r["outcome"] for r in log_rows(db, "lesson_approve_plan")]
    assert outcomes == ["refused", "ok"]


@FOR_DB
def test_the_notes_never_appear_in_the_diary_only_their_hash_and_length(db):
    class_read(db)
    save(db)
    (row,) = log_rows(db, "lesson_save_plan")
    assert row["inputs"]["notes"] == {
        "redacted": True,
        "sha256": hashlib.sha256(LESSON.encode()).hexdigest(),
        "length": len(LESSON),
    }
    assert row["inputs"]["objective"]["length"] == len(OBJECTIVE)
    assert row["inputs"]["model_output"]["redacted"] is True
    assert A_STARTER not in json.dumps(row["inputs"])
