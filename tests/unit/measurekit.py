"""Set-up shared by the tests of the run-time measure tools (T-072). It is not a test file.

It builds a throwaway schema with the Baobab College rulebook in five variants, and walks a small
hand-made measure to `approved` through the real registry functions (the same walk a build and a
teacher demo make), so the tools are tested on rows the registry itself wrote.

The hand-made measure is "the change over the window, and whether it reaches the moved threshold".
It reads three values through the params channel: `MP-1` (the moved threshold, a measure parameter
with a confirmed teacher), `TR-1.window_terms` and `TR-1.min_scores` (read from the hand-written
trend rule, so there is one home for each). It returns numbers only, as a build does (a yes-or-no is
1 or 0). Its expected answers are worked out by hand from the Baobab document
(data/synthetic/methodology/methodology-baobab.md, sections 5 and 6.4):

    English 52, 58, 63        window of 3, change 63 - 52 = 11, 11 >= 10, so the threshold is met
    English 60, 70            2 scores meets the minimum of 2, change 10, 10 >= 10, met (case B-06)
    Mathematics 40, 62, 66, 64   only the last 3 count: change 64 - 62 = 2, not met
    Science 64                1 score is below the minimum of 2: enough_scores is 0 (case B-07)
"""

import contextlib
import copy
import hashlib
import json
import os
import uuid
from pathlib import Path

import psycopg
import yaml
from dbtools import make_document, run_migrate
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from mdeis import registry, rulebook
from mdeis.tools.context import Approver, ToolContext

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "templates"
BAOBAB = TEMPLATES / "baobab-v1.yaml"

TEACHER = Approver("u-teacher", "Ms Teacher")

SOURCE = '''from fractions import Fraction

OUTPUT_KIND = "number"


def measure(records, params):
    window = int(params["TR-1.window_terms"])
    minimum = int(params["TR-1.min_scores"])
    threshold = Fraction(params["MP-1"])
    used = records[-window:]
    if len(used) < minimum:
        return {"enough_scores": 0, "scores_used": len(used)}
    change = Fraction(used[-1]["score"]) - Fraction(used[0]["score"])
    reached = 1 if abs(change) >= threshold else 0
    return {
        "change": change,
        "enough_scores": 1,
        "scores_used": len(used),
        "threshold_met": reached,
    }
'''
# The second version differs in one operator: the threshold counts only when it is passed.
SOURCE_V2 = SOURCE.replace(">= threshold", "> threshold")
OUTPUT_SCHEMA = {
    "kind": "number",
    "fields": ["change", "enough_scores", "scores_used", "threshold_met"],
}
CLAUSES = ["MP-1", "TR-1"]
DESCRIPTION = "The change across the window of one subject, and whether it reaches the threshold."
NAME = "Change over the window"

# Template variants: name -> how MP-1 is written (None: no measure parameter at all).
VARIANTS = {
    "baobab-v1": None,
    "baobab-mp": {},
    "baobab-mp-open": {"confirmed_by": None},
    "baobab-mp-12": {"value": 12},
    "baobab-mp-rational": {"value_type": "rational", "value": "10"},
}
ACTIVE = "baobab-mp"


def mp_rule(value=10, confirmed_by="Ms Teacher", value_type="integer", unit="points"):
    return {
        "id": "MP-1",
        "name": "moved_at_or_above",
        "value_type": value_type,
        "value": value,
        "unit": unit,
        "source_document": "BAO-METH-1.0",
        "method_ref": "6.4",
        "confirmed_by": confirmed_by,
        "text": f"The pattern is moved up when the change is {value} points or more.",
    }


def write_variant(directory: Path, name: str, over) -> Path:
    """The Baobab file with MP-1 added as `over` says, saved as `<name>.yaml`."""
    content = copy.deepcopy(yaml.safe_load(BAOBAB.read_text(encoding="utf-8")))
    if over is not None:
        content["measure_params"] = [mp_rule(**over)]
    path = directory / f"{name}.yaml"
    path.write_text(yaml.safe_dump(content, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def build_schema(url: str, directory: Path) -> tuple[str, str]:
    """A schema with every migration and the five Baobab variants loaded; `baobab-mp` is active.
    Returns (schema name, its connection url)."""
    name = "measures_" + os.urandom(4).hex()
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{name}"')
    scoped = make_conninfo(url, options=f"-c search_path={name},public")
    built = run_migrate(scoped)
    assert built.returncode == 0, built.stderr
    order = [n for n in VARIANTS if n != ACTIVE] + [ACTIVE]
    for variant in order:
        path = BAOBAB
        if variant != "baobab-v1":
            path = write_variant(directory, variant, VARIANTS[variant])
        with psycopg.connect(scoped) as conn:
            rulebook.load_file(conn, path, url=scoped, by="test")
    with psycopg.connect(scoped) as conn:
        conn.execute(
            "INSERT INTO registry_reviewers (user_id, role, name, granted_by) "
            "VALUES ('u-teacher', 'teacher', 'Ms Teacher', 'admin')"
        )
        conn.commit()
    return name, scoped


def drop_schema(url: str, name: str) -> None:
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'DROP SCHEMA "{name}" CASCADE')


@contextlib.contextmanager
def active(url: str, name: str):
    """Make `name` the active rulebook for a moment, then put `baobab-mp` back."""
    with psycopg.connect(url) as c:
        rulebook.activate_version(c, name, url=url, by="test")
    try:
        yield
    finally:
        with psycopg.connect(url) as c:
            rulebook.activate_version(c, ACTIVE, url=url, by="test")


# --------------------------------------------------------------------------- the registry walk
def context(url: str, conn=None, who=None, commit=True) -> ToolContext:
    return ToolContext(
        conn=conn if conn is not None else psycopg.connect(url),
        run_id=f"RUN-{uuid.uuid4()}",
        approver=who,
        database_url=url,
        commit=commit,
    )


def methodology_document(conn) -> str:
    return make_document(
        conn, kind="methodology", status="done", sha256=hashlib.sha256(os.urandom(8)).hexdigest()
    )


def case(name, source, quote=None):
    test = {"name": name, "source": source, "records": [], "expected": {"value": "11"}}
    if quote:
        test["quote"] = quote
    return test


TESTS = [
    case("build case", "build"),
    case(
        "worked example 52, 58, 63",
        "document_example",
        "The scores, oldest first, were 52, 58 and 63. The change was 63 minus 52, which is 11",
    ),
]


def register(
    url: str,
    *,
    source: str = SOURCE,
    stop_after: str = "approved",
    measure_id: str | None = None,
    name: str = NAME,
    method_ref: str = "6.4",
    template_version: str = ACTIVE,
    clause_ids=CLAUSES,
    output_kind: str = "number",
    output_schema=OUTPUT_SCHEMA,
    description: str = DESCRIPTION,
    input_scope: str = "learner_skill",
    conn=None,
    commit: bool = True,
) -> dict:
    """Create a version and walk it as far as `stop_after`: proposed, testing, awaiting_demo,
    approved, or rejected (the teacher turns it down). Every step is the real registry function:
    the system moves it to the demo, and the registered teacher's decision approves it. Returns
    {measure_id, version}.

    `conn` and `commit=False` keep every row inside one test's transaction."""
    own = conn is None
    conn = psycopg.connect(url) if own else conn
    try:
        system = context(url, conn, None, commit)
        made = registry.create_version(
            system,
            measure_name=name,
            method_ref=method_ref,
            description=description,
            input_scope=input_scope,
            source_code=source,
            output_kind=output_kind,
            output_schema=output_schema,
            methodology_document_id=methodology_document(conn),
            section_sha256=hashlib.sha256(method_ref.encode()).hexdigest(),
            clause_ids=list(clause_ids),
            template_version=template_version,
            tests=TESTS,
            measure_id=measure_id,
        )
        assert made["status"] == "ok", made
        which = {"measure_id": made["measure_id"], "version": made["version"]}
        if stop_after == "proposed":
            return which
        names = [t["name"] for t in TESTS]
        done = [{"name": n, "passed": True} for n in names]
        assert registry.advance_status(system, **which, to="testing")["status"] == "ok"
        assert registry.record_tests(system, **which, results=done)["status"] == "ok"
        if stop_after == "testing":
            return which
        assert registry.advance_status(system, **which, to="awaiting_review")["status"] == "ok"
        review = registry.record_review(system, **which, result="pass", reasons=["agrees"])
        assert review["status"] == "ok", review
        assert registry.advance_status(system, **which, to="awaiting_demo")["status"] == "ok"
        if stop_after == "awaiting_demo":
            return which
        teacher = context(url, conn, TEACHER, commit)
        decision = "rejected" if stop_after == "rejected" else "confirmed"
        note = "the case does not fit" if decision == "rejected" else None
        decided = registry.record_teacher_decision(teacher, **which, decision=decision, note=note)
        assert decided["status"] == "ok", decided
        return which
    finally:
        if own:
            conn.close()


# --------------------------------------------------------------------------- the learners
def add_learner(url: str, subjects: dict, school: str = "BAO", year: str = "2027/2028") -> str:
    """A learner with out-of-100 term scores. `subjects` maps a subject to its scores, oldest
    first; None is a term with no score. Returns the learner ID."""
    with psycopg.connect(url) as conn:
        learner = conn.execute(
            "INSERT INTO learners (school_code, school_ref, display_name) "
            "VALUES (%s, %s, %s) RETURNING learner_id",
            (school, f"{school}/{uuid.uuid4().hex[:8]}", "Pupil Name"),
        ).fetchone()[0]
        for subject, scores in subjects.items():
            for term, raw in enumerate(scores, start=1):
                conn.execute(
                    "INSERT INTO assessments (learner_id, subject, score, academic_year, stage, "
                    "term, coefficient, denominator, raw_score) "
                    "VALUES (%s, %s, %s, %s, 'Form 1', %s, 1, 100, %s)",
                    (learner, subject, raw, year, term, None if raw is None else str(raw)),
                )
        conn.commit()
    return learner


def record_ids(url: str, learner: str, subject: str) -> list:
    """The record IDs of a subject, in term order."""
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "SELECT assessment_id FROM assessments WHERE learner_id = %s AND subject = %s "
            "ORDER BY term",
            (learner, subject),
        ).fetchall()
    return [r[0] for r in rows]


def add_patch(url: str, which: dict, clauses: list) -> str:
    """A proposed template patch stored for a measure version: the measure parameter clauses the
    build proposed for it, as the build's own store writes them."""
    text = json.dumps({"measure_params": clauses}, sort_keys=True)
    with psycopg.connect(url) as conn:
        document = methodology_document(conn)
        patch_id = conn.execute(
            "INSERT INTO template_patches (measure_id, version, source_document_id, "
            "base_template_version, section_hashes, patch_text, patch_sha256, clauses) "
            "VALUES (%s, %s, %s, 'baobab-v1', %s, %s, %s, %s) RETURNING patch_id",
            (
                which["measure_id"],
                which["version"],
                document,
                Jsonb({"6.4": hashlib.sha256(b"6.4").hexdigest()}),
                text,
                hashlib.sha256(text.encode("utf-8")).hexdigest(),
                Jsonb(clauses),
            ),
        ).fetchone()[0]
        conn.commit()
    return patch_id
