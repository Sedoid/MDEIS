"""How class_baseline is wired: the server's listing and signature, and the read-only situation.

No database. The server is listed with the class view on and off (the listing needs no connection),
`server.py` is read as text for the tool's signature, and `situation(..., sync=False)` runs against
a small fake connection that records every statement it is given.
"""

import ast
import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")

from mdeis import server  # noqa: E402
from mdeis.core import effective, store  # noqa: E402
from mdeis.tools import _shared  # noqa: E402
from mdeis.tools.context import Approver, ToolContext  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
NOT_ARGUMENTS = {"school", "school_id", "school_code", "approver", "user", "user_id",
                 "template_version", "version", "approver_id", "approver_name"}  # fmt: skip


def listed(monkeypatch, on):
    if on:
        monkeypatch.setenv("CLASS_VIEW_ENABLED", "true")
    else:
        monkeypatch.delenv("CLASS_VIEW_ENABLED", raising=False)
    reloaded = importlib.reload(server)
    return {t.name: t for t in asyncio.run(reloaded.mcp.list_tools())}


@pytest.fixture(autouse=True)
def put_the_server_back(monkeypatch):
    yield
    monkeypatch.delenv("CLASS_VIEW_ENABLED", raising=False)
    importlib.reload(server)


# ------------------------------------------------------------------ the listing
def test_with_the_view_off_the_server_lists_neither_class_tool(monkeypatch):
    tools = listed(monkeypatch, on=False)
    assert "class_baseline" not in tools and "stage_gap_counts" not in tools
    # the eight tools, list_measures and compute_measure (T-072), export_report (T-076)
    assert len(tools) == 11


def test_with_the_view_on_the_server_lists_class_baseline_with_only_stage_and_term(monkeypatch):
    tools = listed(monkeypatch, on=True)
    assert "class_baseline" in tools and "stage_gap_counts" in tools
    assert set(tools["class_baseline"].inputSchema["properties"]) == {"stage", "term"}
    assert set(tools["class_baseline"].inputSchema["required"]) == {"stage", "term"}
    # The three lesson tools (guard_lesson_notes, save_lesson_plan, get_lesson_plan) share the
    # class view's switch, so the count with the view on is 11 + 2 + 3. The names are pinned in
    # test_lesson_graph_boundaries.py.
    assert len(tools) == 16


def test_the_description_says_what_it_is_and_what_it_will_not_do(monkeypatch):
    text = listed(monkeypatch, on=True)["class_baseline"].description.lower()
    assert "no names" in text and "locked" in text and "hidden" in text
    for word in ("rank", "label", "pathway"):
        assert word not in text.replace("no names", "")


# ------------------------------------------------------------------ the signature, as text
def server_function(name):
    tree = ast.parse((ROOT / "src" / "mdeis" / "server.py").read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def test_class_baseline_takes_no_school_approver_user_or_rulebook_argument():
    function = server_function("class_baseline")
    names = [a.arg for a in function.args.args]
    assert names == ["request", "stage", "term"]
    assert not NOT_ARGUMENTS & set(names)


def test_class_baseline_passes_only_stage_and_term_to_the_tool():
    function = server_function("class_baseline")
    calls = [n for n in ast.walk(function) if isinstance(n, ast.Call)]
    run = next(c for c in calls if getattr(c.func, "id", "") == "_run")
    assert {k.arg for k in run.keywords} == {"stage", "term"}


def test_class_baseline_is_registered_only_inside_the_class_view_switch():
    tree = ast.parse((ROOT / "src" / "mdeis" / "server.py").read_text(encoding="utf-8"))
    guards = [n for n in tree.body if isinstance(n, ast.If)]
    assert any(
        "enabled" in ast.unparse(g.test)
        and any(isinstance(n, ast.FunctionDef) and n.name == "class_baseline" for n in g.body)
        for g in guards
    )
    top_level = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    assert "class_baseline" not in top_level


def test_the_tool_function_takes_the_context_first_and_no_school_argument():
    import inspect

    import mdeis.tools as toolbox

    names = list(inspect.signature(toolbox.class_baseline).parameters)
    assert names == ["ctx", "stage", "term"]


# ------------------------------------------------------------------ situation without writing
class FakeConnection:
    """Answers `SELECT issue_id ... WHERE fingerprint = %s` from a dict; notes each statement."""

    def __init__(self, known):
        self.known, self.statements = known, []

    def execute(self, sql, params=()):
        self.statements.append(sql)
        key = params[0] if params else None
        row = (self.known[key],) if key in self.known else None
        return SimpleNamespace(fetchone=lambda: row)


def issue(fingerprint, kind="missing_term", severity="blocking", subject="Mathematics"):
    return {"fingerprint": fingerprint, "kind": kind, "severity": severity, "subject": subject,
            "year": "2025/2026", "card_id": "c", "term": 2}  # fmt: skip


def test_attach_issue_ids_reads_the_saved_number_and_writes_nothing():
    conn = FakeConnection({"f-saved": "I-000007"})
    issues = [issue("f-saved"), issue("f-new")]
    store.attach_issue_ids(conn, issues)
    assert [i["issue_id"] for i in issues] == ["I-000007", None]
    assert all(s.lstrip().upper().startswith("SELECT") for s in conn.statements)


def test_a_blocking_problem_with_no_saved_number_still_blocks_and_a_saved_answer_still_clears():
    rules = SimpleNamespace(subjects={"Mathematics": 1, "English": 1})
    issues = [issue("f-saved"), issue("f-new", subject="English")]
    store.attach_issue_ids(FakeConnection({"f-saved": "I-000007"}), issues)
    answers = {"I-000007": {"resolution": "confirm_missing"}}
    assert effective.blocked_subjects(issues, answers, rules) == {"English"}
    assert effective.blocked_subjects(issues, {}, rules) == {"Mathematics", "English"}


@pytest.fixture
def stubbed(monkeypatch):
    """`situation` with the reading of cards, rules and answers replaced by small stand-ins."""
    from mdeis.core.rules import rules_from_content
    from mdeis.core.template import load_template

    rules = rules_from_content(
        load_template(ROOT / "templates" / "v1.1.yaml").model_dump(mode="json"), "v1.1", "0" * 64
    )
    calls = []
    monkeypatch.setattr(_shared.store, "learner_exists", lambda conn, lid: True)
    monkeypatch.setattr(_shared.store, "load_answers", lambda conn, lid: {})
    monkeypatch.setattr(_shared, "load_rules", lambda conn, version=None: rules)
    monkeypatch.setattr(_shared, "load_cards", lambda conn, lid: [])
    monkeypatch.setattr(_shared, "detect", lambda lid, cards, r: [issue("f-new")])
    monkeypatch.setattr(_shared, "effective_records", lambda *a, **k: [])

    def sync(conn, lid, issues, run):
        calls.append("sync")
        for i in issues:
            i["issue_id"] = "I-000001"  # what the real one does: it numbers every problem

    monkeypatch.setattr(_shared.store, "sync_issues", sync)
    return calls


def context(conn):
    return ToolContext(conn=conn, run_id="RUN-T", approver=Approver("u", "Ada"), commit=False)


def test_situation_without_sync_saves_nothing_and_with_sync_still_saves(stubbed):
    conn = FakeConnection({})
    found = _shared.situation(context(conn), "L-0001", None, sync=False)
    assert stubbed == [], "no issue rows are saved when sync is off"
    assert found.issues[0]["issue_id"] is None
    assert found.blocked == {"Mathematics"}
    _shared.situation(context(conn), "L-0001", None)
    assert stubbed == ["sync"], "the default is unchanged: every other caller still saves"
