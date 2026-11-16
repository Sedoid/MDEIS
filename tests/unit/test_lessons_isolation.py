"""Who may reach the plans table (T-060, slice 3). Static: reads the source, needs no database."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
TABLE = re.compile(r"\blesson_plans\b")


def python_files(*parts):
    return sorted((SRC.joinpath(*parts)).rglob("*.py"))


def test_only_the_store_and_the_migration_name_the_plans_table_in_the_source():
    holders = {
        str(p.relative_to(SRC))
        for p in SRC.rglob("*")
        if p.suffix in {".py", ".sql"} and p.is_file() and TABLE.search(p.read_text("utf-8"))
    }
    assert holders == {"mdeis/lessons/store.py", "mdeis/db/migrations/009_lesson_plans.sql"}


def test_the_portal_gateway_and_orchestrator_do_not_import_the_lesson_package():
    for place in ("portal", "gateway", "orchestrator"):
        for path in python_files(place):
            text = path.read_text("utf-8")
            assert "mdeis.lessons" not in text and "from mdeis import lessons" not in text, path
            assert not TABLE.search(text), path


def test_the_server_offers_no_tool_that_approves_a_lesson_plan_to_a_model():
    server = (SRC / "mdeis" / "server.py").read_text("utf-8")
    assert "approve_lesson" not in server and "reject_lesson" not in server


def test_the_only_modules_that_import_the_store_are_the_store_itself_and_tests():
    for path in python_files("mdeis"):
        if path.name == "store.py" and path.parent.name == "lessons":
            continue
        text = path.read_text("utf-8")
        if path.parent.name == "lessons":
            assert "lessons.store" not in text and "from .store" not in text, path
