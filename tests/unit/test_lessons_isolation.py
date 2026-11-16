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


# T-062: the human-only routes need to call approve and reject, so exactly one new module may import
# the store besides the tool server. This test PINS that set. It was added, not loosened: the test
# above looks only inside `src/mdeis/lessons/`, so by itself it would not stop any other module.
IMPORTS_THE_STORE = re.compile(
    r"lessons\.store|from \.lessons import store|from mdeis\.lessons import store"
)
MAY_IMPORT_THE_STORE = {"mdeis/server.py", "mdeis/human_routes.py"}


def test_exactly_the_tool_server_and_the_human_routes_import_the_store_outside_it():
    found = {
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if p != SRC / "mdeis" / "lessons" / "store.py"
        and p.parent != SRC / "mdeis" / "lessons"  # the package's own modules are checked above
        and "lessons" in p.read_text("utf-8")
        and IMPORTS_THE_STORE.search(p.read_text("utf-8"))
    }
    assert found == MAY_IMPORT_THE_STORE
