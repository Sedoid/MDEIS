"""The opt-in setting `REVIEWERS_ARE_APPROVERS` (T-077), the parts that need no database.

An owner decision of 2026-11-17: the signed-in approver of a school must be able to use the whole
measure demo and clear a hold without a separate registration step. It is a server setting, off
unless the environment switches it on. These tests hold the code to:

* the setting is read from the environment each time it is asked, and only the words the class-view
  switch accepts turn it on (a missing or odd value is off);
* nothing in the model path, the gateway, the portal code or the MCP tools reads it or imports what
  it writes;
* the one writer (`mdeis.registry.approvers`) is not a public registry function, and nothing outside
  the registry store reaches it;
* the refusal for an ended record keeps the words the routes type on, and says "approver";
* the page says who the name will be recorded as.

The behaviour against Postgres is in tests/unit/test_approver_setting_db.py (integration).
"""

import ast
import inspect
import re
from pathlib import Path

import pytest

from mdeis import approver_setting as setting
from mdeis import measure_routes, registry
from mdeis.registry import approvers, store

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
FLAG = "REVIEWERS_ARE_APPROVERS"


def python_files(*parts):
    root = SRC.joinpath(*parts)
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


# ------------------------------------------------------------------------------ reading the setting
def test_the_setting_has_the_name_the_owner_gave():
    assert setting.FLAG == FLAG


@pytest.mark.parametrize("value", ["true", "TRUE", " True ", "1", "yes", "on", "On"])
def test_these_words_turn_it_on(value):
    assert setting.enabled({FLAG: value}) is True


@pytest.mark.parametrize("value", ["", "false", "False", "0", "no", "off", "maybe", "tru", " "])
def test_anything_else_is_off(value):
    assert setting.enabled({FLAG: value}) is False


def test_unset_is_off():
    assert setting.enabled({}) is False


def test_it_is_read_from_the_environment_each_time_it_is_asked(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    assert setting.enabled() is False
    monkeypatch.setenv(FLAG, "true")
    assert setting.enabled() is True
    monkeypatch.setenv(FLAG, "false")
    assert setting.enabled() is False


def test_the_words_that_turn_it_on_are_the_ones_the_class_view_switch_takes():
    from mdeis.tools import class_view

    assert tuple(setting.ON) == tuple(class_view.ON)


# ------------------------------------------------------------------------------ the ended record
class FakeConn:
    """A connection whose one query gives the row it was made with."""

    def __init__(self, row):
        self.row = row

    def execute(self, sql, params=None):
        assert "registry_reviewers" in sql and "SELECT" in sql and "INSERT" not in sql
        return self

    def fetchone(self):
        return self.row


@pytest.mark.parametrize("row,expected", [
    ((None,), False),  # no record at all
    ((True,), False),  # a live record
    ((False,), True),  # only ended records
    ({"bool_or": False}, True),
    ({"bool_or": None}, False),
])
def test_a_record_counts_as_ended_only_when_every_record_of_that_role_was_ended(row, expected):
    assert setting.ended(FakeConn(row), "u-1", "teacher") is expected


@pytest.mark.parametrize("role", ["teacher", "developer"])
def test_the_ended_record_refusal_says_approver_keeps_the_route_words_and_names_the_setting(role):
    text = setting.ended_text(role)
    assert "approver" in text and f"not recorded as a {role}" in text and FLAG in text
    typed, status = measure_routes.type_refusal(text)
    assert (typed, status) == (f"not_a_{role}", 403)


def test_the_plain_refusal_with_the_setting_off_is_the_old_words_in_both_places():
    api_text = (SRC / "mdeis" / "measure_demo" / "api.py").read_text("utf-8")
    store_text = (SRC / "mdeis" / "registry" / "store.py").read_text("utf-8")
    # (the demo now builds the same words for the teacher and, T-077, for the developer)
    assert 'f"the signed-in person is not recorded as a {role} in the registry"' in api_text
    assert '_person(ctx, t.TEACHER, "not_a_teacher")' in api_text
    assert '_person(ctx, t.DEVELOPER, "not_a_developer")' in api_text
    assert 'f"the signed-in person is not recorded as a {role} in the registry"' in store_text


def test_a_name_is_the_one_the_token_gave_and_a_blank_one_is_none():
    from mdeis.tools.context import Approver

    assert setting.name_of(Approver("u", "  Owner One ")) == "Owner One"
    assert setting.name_of(Approver("u", "   ")) is None
    assert setting.name_of(None) is None


def test_a_school_is_the_one_the_context_holds_and_a_blank_or_missing_one_is_none():
    class Ctx:
        school = None

    ctx = Ctx()
    assert setting.school_of(ctx) is None
    ctx.school = "  "
    assert setting.school_of(ctx) is None
    ctx.school = 7
    assert setting.school_of(ctx) is None
    ctx.school = "BAO"
    assert setting.school_of(ctx) == "BAO"


# ------------------------------------------------------------------------------ where it is read
def test_only_the_registry_and_the_demo_read_the_setting():
    allowed = {
        SRC / "mdeis" / "approver_setting.py",
        SRC / "mdeis" / "registry" / "store.py",
        SRC / "mdeis" / "registry" / "approvers.py",
        SRC / "mdeis" / "measure_demo" / "api.py",
    }
    for path in python_files():
        text = path.read_text("utf-8")
        if path in allowed:
            continue
        assert FLAG not in text and "approver_setting" not in text, path


@pytest.mark.parametrize("place", ["orchestrator", "gateway", "portal"])
def test_the_model_path_the_gateway_and_the_portal_code_never_name_the_setting(place):
    for path in python_files(place):
        text = path.read_text("utf-8")
        assert FLAG not in text and "approver_setting" not in text, path
        assert "registry.approvers" not in text and "add_row" not in text, path


def test_the_mcp_tools_and_the_server_never_name_the_setting_or_its_writer():
    for path in [*python_files("mdeis", "tools"), SRC / "mdeis" / "server.py"]:
        text = path.read_text("utf-8")
        assert FLAG not in text and "approver_setting" not in text, path
        assert "approvers" not in text, path


def test_the_writer_is_not_a_public_registry_function():
    assert "approvers" not in registry.__all__
    assert not hasattr(registry, "add_row") and not hasattr(registry, "log")


def test_only_the_registry_store_imports_the_writer():
    for path in python_files():
        if path.name == "store.py" and path.parent.name == "registry":
            continue
        if path.name == "approvers.py":
            continue
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names = {a.name for a in node.names}
                assert not (
                    "approvers" in names or (node.module or "").endswith("registry.approvers")
                ), path
            elif isinstance(node, ast.Import):
                assert not any(a.name.endswith("registry.approvers") for a in node.names), path


def test_the_demo_imports_from_the_registry_only_what_it_did_plus_clear_hold():
    reads = set()
    for path in sorted((SRC / "mdeis" / "measure_demo").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module and "registry" in node.module:
                reads |= {a.name for a in node.names}
    # T-077: `clear_hold` joined the list when the approver got a screen to clear a hold
    assert reads <= {"record_tests", "record_teacher_decision", "transitions", "clear_hold"}, reads


def test_the_demo_holds_no_insert_of_its_own_for_the_registry():
    for path in sorted((SRC / "mdeis" / "measure_demo").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not re.match(r"\s*(INSERT|UPDATE|DELETE)\b", node.value), (path, node.value)


# ------------------------------------------------------------------------------ the writer
def test_the_writer_takes_the_person_from_its_caller_and_has_no_argument_for_a_school_or_a_note():
    assert list(inspect.signature(approvers.add_row).parameters) == ["ctx", "who", "role", "name"]
    assert list(inspect.signature(approvers.log).parameters) == ["ctx", "who", "role", "name"]


def test_the_writer_is_called_only_inside_the_registry_functions_with_the_person_they_resolved():
    tree = ast.parse(inspect.getsource(store))
    callers = {}
    for fn in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
        for call in ast.walk(fn):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "approvers"
            ):
                callers.setdefault(fn.name, set()).add(call.func.attr)
    assert set(callers) == {"record_tests", "clear_hold", "record_teacher_decision"}
    for names in callers.values():
        assert names == {"add_row", "log"}


def test_the_resolver_has_no_way_to_be_given_a_person():
    parameters = list(inspect.signature(store._registered).parameters)
    assert parameters == ["ctx", "role"]


def test_the_writer_refuses_a_role_that_is_not_a_registry_role():
    from mdeis.tools.context import Approver, Refusal

    class Ctx:
        school = "BAO"
        conn = None

    with pytest.raises(Refusal):
        approvers.add_row(Ctx(), Approver("u", "N"), "admin", "N")


def test_the_writer_refuses_a_session_with_no_school_before_it_touches_the_database():
    from mdeis.tools.context import Approver, Refusal

    class Ctx:
        school = None
        conn = None  # a touch of the database would be an AttributeError, not a Refusal

    with pytest.raises(Refusal, match="no school"):
        approvers.add_row(Ctx(), Approver("u", "N"), "teacher", "N")


def test_the_granted_by_value_says_where_the_row_came_from():
    assert setting.GRANTED_BY == "auto:REVIEWERS_ARE_APPROVERS"
    assert setting.AUTO_NOTE == "auto-registered by REVIEWERS_ARE_APPROVERS"


# ------------------------------------------------------------------------------ the page
SCRIPT = (SRC / "portal" / "static" / "methods.js").read_text("utf-8")


def test_the_page_says_the_name_is_recorded_as_the_signed_in_approver_who_is_the_teacher():
    assert "api.me.name" in SCRIPT
    assert "recorded as the signed-in approver" in SCRIPT
    assert "will be recorded" in SCRIPT


def test_the_page_still_sends_no_person_and_no_school():
    for word in ("approver_id", "approver_name", "user_id", "school"):
        assert not re.search(rf"[\"']{word}[\"']\s*:", SCRIPT), word
