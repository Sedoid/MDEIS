"""The lesson graph against the REAL lesson store (T-061, integration: needs DATABASE_URL).

The model is still a script. The tool client calls the real functions of the lesson store in
process, on one connection in a throwaway schema (the same set-up as `test_lessons_store.py`). Only
`class_baseline` is a stand-in: it writes what the real tool would have written to the diary and
returns the same class result, so the store finds it as "returned in this run".

This proves what the fake server cannot: the real checks, the real roster, the real plan row, and
the real action-log rows (one per tool call, the notes redacted).
"""

import asyncio
import copy
import hashlib
import json

import psycopg
import pytest
from dbtools import run_migrate
from lesson_graph_fakes import class_result
from lessonkit import A_ASK, LESSON, OBJECTIVE, VERSION, content
from orchestrator_fakes import FakeModel, say
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from mdeis.db import diary
from mdeis.lessons import store
from mdeis.tools.context import Approver, ToolContext
from orchestrator import tools
from orchestrator.config import load_settings
from orchestrator.lessons import MAX_REPAIR_ROUNDS, prompt
from orchestrator.lessons import graph as lesson_graph

pytestmark = pytest.mark.integration

AUTHOR = Approver("u-1", "Ms Tutor")
RUN = "RUN-G1"
SETTINGS = load_settings(
    {
        "ORCHESTRATOR_API_KEY": "k", "OPENROUTER_API_KEY": "or-key",
        "OPENROUTER_MODEL": "open/weights",
    }
)
NEW_ASK = "Ask what the passage tells us and why."
EDIT = {
    "id": "e1", "type": "reword", "anchor_quote": A_ASK, "replacement": NEW_ASK,
    "finding_ids": ["CF-a1b2c3d4"], "resource_ids": [],
    "suggestion": "Give learners a question that asks for a reason.",
}
GOOD = json.dumps({"edits": [EDIT]})
DIGIT = json.dumps({"edits": [{**EDIT, "suggestion": "Give 3 questions."}]})
OBJECTIVE_EDIT = json.dumps({"edits": [{
    **EDIT, "anchor_quote": "Learners explain the main idea of a short text.",
    "replacement": "Learners win the lesson.",
}]})


@pytest.fixture
def db(scratch_schema):
    schema, url = scratch_schema
    built = run_migrate(url)
    assert built.returncode == 0, built.stderr
    with psycopg.connect(url) as conn:
        conn.schema, conn.url = schema, url
        conn.execute(
            "INSERT INTO templates (version_name, file_name, sha256, template_id, content, active) "
            "VALUES (%s, %s, repeat('a', 64), 'tid', %s, true)",
            ("t-lessons", "t-lessons.yaml", Jsonb(content())),
        )
        conn.execute(
            "INSERT INTO learners (school_code, school_ref, display_name) VALUES "
            "('TST', 'TST/2025/014', 'Amara Nfor'), ('TST', 'TST/2025/015', 'Joy Mbeki')"
        )
        yield conn
        conn.rollback()


class RealStore:
    """The graph's tool client: the real store functions, in process. The signed-in user and the
    school are the session's, set here once, as the server sets them from the verified token."""

    def __init__(self, conn, result=None):
        self.conn = conn
        self.result = class_result() if result is None else result
        self.calls = []

    async def list_tools(self):
        return []

    async def call(self, name, arguments):
        self.calls.append((name, copy.deepcopy(arguments)))
        if name == "class_baseline":
            diary.write(name, run_id=RUN, inputs=arguments, outputs=self.result, outcome="ok",
                        url=self.conn.url)
            return copy.deepcopy(self.result)
        ctx = ToolContext(conn=self.conn, run_id=RUN, approver=AUTHOR,
                          database_url=self.conn.url, commit=False, school="TST")
        return getattr(store, name)(ctx, **arguments)


def run(db, model, notes=LESSON, objective=OBJECTIVE, result=None):
    client = RealStore(db, result)
    answer = asyncio.run(
        lesson_graph.run(
            stage="Form 3", area="literacy", term=2, objective=objective, notes=notes,
            run_id=RUN, settings=SETTINGS, model=model, servers={tools.MDEIS: client},
            engine="plain",
        )
    )
    return answer, client


def plans(db):
    return db.cursor(row_factory=dict_row).execute("SELECT * FROM lesson_plans").fetchall()


def log(db, tool=None):
    sql = "SELECT tool, outcome, approver_id, inputs, outputs FROM action_log WHERE run_id = %s"
    args = [RUN]
    if tool:
        sql, args = sql + " AND tool = %s", [RUN, tool]
    return db.cursor(row_factory=dict_row).execute(sql + " ORDER BY event_id", args).fetchall()


def test_a_valid_edit_list_makes_one_proposed_plan_for_the_signed_in_author(db):
    answer, _ = run(db, FakeModel(say(GOOD)))
    assert answer["outcome"] == "stored"
    (row,) = plans(db)
    assert row["plan_id"] == answer["plan"]["plan_id"] and row["status"] == "proposed"
    assert (row["author_id"], row["author_name"], row["school_code"]) == ("u-1", "Ms Tutor", "TST")
    assert (row["model"], row["prompt_sha256"], row["run_id"]) == (
        "open/weights", prompt.system_hash(), RUN,
    )
    assert row["revised_text"] == LESSON.replace(A_ASK, NEW_ASK)
    assert answer["plan"]["revised_text"] == row["revised_text"]
    assert [e["id"] for e in row["edits"]["kept"]] == ["e1"]


def test_every_tool_call_is_an_action_log_row_and_the_notes_and_the_output_are_only_hashes(db):
    answer, client = run(db, FakeModel(say(GOOD)))
    lesson_rows = [r for r in log(db) if r["tool"].startswith("lesson_")]
    assert [r["tool"] for r in lesson_rows] == ["lesson_guard_notes", "lesson_save_plan"]
    assert {r["approver_id"] for r in lesson_rows} == {"u-1"}
    save = log(db, "lesson_save_plan")[0]["inputs"]
    assert save["model_output"] == {
        "redacted": True, "sha256": hashlib.sha256(GOOD.encode()).hexdigest(), "length": len(GOOD),
    }
    assert answer["model_output_sha256"] == save["model_output"]["sha256"]
    assert save["notes"]["sha256"] == hashlib.sha256(LESSON.encode()).hexdigest()
    assert save["model"] == "open/weights" and save["prompt_sha256"] == prompt.system_hash()
    assert LESSON not in json.dumps([r["inputs"] for r in lesson_rows])


def test_the_repair_count_is_the_save_rows_of_the_run_minus_one(db):
    answer, _ = run(db, FakeModel(say(DIGIT), say(GOOD)))
    assert answer["outcome"] == "stored" and answer["repair_rounds"] == 1
    assert len(log(db, "lesson_save_plan")) == answer["repair_rounds"] + 1


def test_a_digit_that_is_never_fixed_stops_at_the_bound_and_stores_nothing(db):
    model = FakeModel(*[say(DIGIT) for _ in range(MAX_REPAIR_ROUNDS + 1)])
    answer, _ = run(db, model)
    assert answer["outcome"] == "no_valid_edits" and plans(db) == []
    assert len(model.seen) == 1 + MAX_REPAIR_ROUNDS
    assert len(log(db, "lesson_save_plan")) == 1 + MAX_REPAIR_ROUNDS


def test_an_edit_of_the_objective_is_dropped_by_the_real_checks_and_nothing_is_stored(db):
    answer, _ = run(db, FakeModel(say(OBJECTIVE_EDIT)))
    assert answer["outcome"] == "no_valid_edits" and plans(db) == []
    assert [d["lc"] for d in answer["dropped"]] == [["LC-1"]]


def test_notes_that_name_a_learner_never_reach_the_model_or_the_action_log(db):
    notes = LESSON + "\nAmara found this hard last term.\n"
    model = FakeModel()
    answer, client = run(db, model, notes=notes)
    assert answer["outcome"] == "refused" and model.seen == [] and plans(db) == []
    assert [name for name, _ in client.calls] == ["class_baseline", "guard_lesson_notes"]
    refused = log(db, "lesson_guard_notes")
    assert [r["outcome"] for r in refused] == ["refused"]
    everything = [(r["inputs"], r["outputs"]) for r in log(db)]
    assert "Amara" not in json.dumps(everything)
    assert "Amara" not in json.dumps(answer)


def test_a_suppressed_class_gives_the_rulebooks_own_statement_and_stores_nothing(db):
    suppressed = {
        "status": "suppressed", "school_id": "TST", "stage": "Form 3", "term": 2,
        "academic_year": "2025/2026", "template_version": VERSION, "minimum_group_size": 10,
        "reason": "the group is too small", "clause_ids": ["GM-1"], "finding_id": "CF-00000001",
    }
    model = FakeModel()
    answer, client = run(db, model, result=suppressed)
    assert model.seen == [] and plans(db) == []
    assert answer["outcome"] == "no_evidence"
    assert answer["statement"].startswith(
        "There is not enough class evidence to adapt this lesson. Groups smaller than 10 are never"
    )
