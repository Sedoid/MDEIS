"""Stand-ins for the lesson graph tests (T-061): a class result, and a tool server that runs the
REAL pure checks of the lesson core (T-060) with no database.

`LessonTools` answers the three tools the graph may call. It is not a copy of the checks: it calls
`check_edits`, `parse_edits`, `guard_notes`, `evidence_sentence` and the diff builder of the core,
with the inline rules of `lessonkit.py`. Only the parts that need a database (the roster query, the
diary, the row insert) are replaced: the roster is a fixed one, the diary is the list `calls`, the
row is a counter. A test file that runs the same graph against the real store is
`test_lesson_graph_store.py` (integration).

The model is `orchestrator_fakes.FakeModel`. Nothing here touches a network.
"""

import copy

from lessonkit import BINS, FINDING_ID, VERSION, roster, rules

from mdeis.lessons.checks import PlanError, Selection, check_edits
from mdeis.lessons.edits import parse_edits
from mdeis.lessons.evidence import evidence_sentence
from mdeis.lessons.findings import FindingError, findings_from_class_result
from mdeis.lessons.guard import guard_notes
from mdeis.lessons.parse import LessonError, parse_lesson

SCHOOL = "TST"
OTHER_AREA_ID = "CF-5e5e5e5e"
NUMERACY_BINS = [
    {"bin": "below_expected_range", "shown": True, "count": 9},
    {"bin": "within_expected_range", "shown": True, "count": 11},
    {"bin": "above_expected_range", "shown": True, "count": 2},
    {"bin": "not_enough_evidence", "shown": True, "count": 0},
    {"bin": "blocked", "shown": True, "count": 0},
]


def class_result(**over) -> dict:
    """What `class_baseline` returns for Form 3, term 2: two areas (hand-worked: 14 + 6 + 2 = 22 and
    9 + 11 + 2 = 22)."""
    base = {
        "status": "ok", "school_id": SCHOOL, "stage": "Form 3", "term": 2,
        "academic_year": "2025/2026", "template_version": VERSION, "group_size": 22,
        "finding_id": "CF-00000001", "clause_ids": ["GM-1"],
        "areas": [
            {"area": "literacy", "status": "ok", "bins": copy.deepcopy(BINS),
             "finding_id": FINDING_ID,
             "clause_ids": ["SK-1", "ST-3", "TR-1", "CA-5", "GM-1", "GM-2", "GM-3"]},
            {"area": "numeracy", "status": "ok", "bins": copy.deepcopy(NUMERACY_BINS),
             "finding_id": OTHER_AREA_ID,
             "clause_ids": ["SK-2", "ST-6", "TR-1", "CA-5", "GM-1", "GM-2", "GM-3"]},
        ],
    }
    base.update(over)
    return base


def with_literacy(**over) -> dict:
    """The class result with changes to the literacy area only."""
    result = class_result()
    result["areas"][0].update(over)
    return result


def with_gap_bin(count, shown=True) -> dict:
    bins = copy.deepcopy(BINS)
    bins[0] = {"bin": "below_expected_range", "shown": shown, "count": count if shown else None}
    return with_literacy(bins=bins)


class LessonTools:
    """The MCP server as the lesson graph sees it. `calls` is every (tool, arguments) in order."""

    def __init__(self, result=None, refusal=None, names=None):
        self.result = class_result() if result is None else result
        self.refusal = refusal  # a plain reason: class_baseline refuses with it
        self.rules = rules()
        self.roster = roster() if names is None else names
        self.calls = []
        self.plans = []  # plan ids "stored"
        self.class_read = False

    # -- the server's side of the client
    async def list_tools(self):
        return []

    async def call(self, name, arguments):
        self.calls.append((name, copy.deepcopy(arguments)))
        handler = getattr(self, f"tool_{name}", None)
        if handler is None:
            return {"status": "error", "detail": "no such tool"}
        return handler(**arguments)

    def names_called(self):
        return [name for name, _ in self.calls]

    def arguments_of(self, name):
        return [a for n, a in self.calls if n == name]

    # -- class_baseline
    def tool_class_baseline(self, stage, term):
        if self.refusal:
            return {"status": "refused", "reason": self.refusal}
        self.class_read = True
        return copy.deepcopy(self.result)

    # -- guard_lesson_notes (the real guard, the roster is fixed)
    def tool_guard_lesson_notes(self, stage, area, term, objective, notes):
        problem = self.rules.selection_problem((stage, area, term))
        if problem:
            return {"status": "refused", "reason": problem}
        for text, limit in ((notes, self.rules.max_notes_chars), (objective, None)):
            result = guard_notes(text, self.roster, max_chars=limit)
            if not result.ok:
                return {"status": "refused", "reason": result.message}
        return {"status": "ok", "length": len(notes), "template_version": VERSION}

    # -- save_lesson_plan (the real checks; no row is written)
    def tool_save_lesson_plan(
        self, stage, area, term, objective, notes, model_output, model, prompt_sha256
    ):
        problem = self.rules.selection_problem((stage, area, term))
        if problem:
            return {"status": "refused", "reason": problem}
        for text, limit in ((notes, self.rules.max_notes_chars), (objective, None)):
            result = guard_notes(text, self.roster, max_chars=limit)
            if not result.ok:
                return {"status": "refused", "reason": result.message}
        try:
            lesson = parse_lesson(notes, objective)
        except LessonError as error:
            return {"status": "refused", "reason": str(error)}
        contract = parse_edits(model_output)
        if contract.error:
            return {"status": "invalid_output", "reason": contract.error, "repairable": True}
        try:
            findings = findings_from_class_result(self.result) if self.class_read else []
        except FindingError:
            findings = []
        try:
            checked = check_edits(
                lesson, contract.edits, findings, self.rules, Selection(stage, area, term),
                self.roster,
            )
        except PlanError as error:
            return {"status": "refused", "reason": str(error)}
        dropped = [
            {"id": v.edit_id, "lc": list(v.lc_ids), "reason": "; ".join(v.reasons),
             "repairable": v.repairable}
            for v in checked.dropped
        ]
        if not checked.usable_evidence:
            return {"status": checked.statement_kind, "statement": checked.statement,
                    "dropped": dropped}
        if not checked.kept:
            return {"status": "no_valid_edits", "dropped": dropped}
        by_id = {f.finding_id: f for f in findings}
        plan_id = f"LP-{len(self.plans) + 1:06d}"
        self.plans.append(plan_id)
        edits = []
        for e in checked.kept:
            edits.append({
                "id": e.id, "type": e.type, "anchor_quote": e.anchor_quote,
                "replacement": e.replacement, "suggestion": e.suggestion,
                "finding_ids": list(e.finding_ids),
                "evidence": [evidence_sentence(by_id[i], self.rules) for i in e.finding_ids],
            })
        return {
            "status": "stored", "plan_id": plan_id, "plan_status": "proposed", "stage": stage,
            "area": area, "term": term, "template_version": VERSION, "objective": objective,
            "original_sha256": lesson.text_sha256, "revised_sha256": checked.revised.text_sha256,
            "revised_text": checked.revised.text, "edits": edits, "dropped": dropped,
            "notices": [{"clause_id": n.clause_id, "where": n.where} for n in checked.notices],
            "diff": {
                "lines": [
                    {"op": d.op, "old": list(d.old), "new": list(d.new)}
                    for d in checked.revised.diff
                ],
                "changes": [],
            },
        }

    # -- get_lesson_plan (only to prove the read path)
    def tool_get_lesson_plan(self, plan_id):
        return {"status": "ok", "plan_id": plan_id, "plan_status": "proposed"}
