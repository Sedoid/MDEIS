"""The lesson core is pure: no model client, no network, no database in the checking modules.

`store.py` is the one module that may import the database driver, and it imports no model client
either. An import scan reads the source with `ast`; a runtime test makes any new socket fail and
runs the whole checking path.
"""

import ast
import socket
from pathlib import Path

import pytest
from lessonkit import A_PLENARY, add_after, edit, finding, lesson, roster, rules

from mdeis.lessons.apply import apply_edits
from mdeis.lessons.checks import check_edits
from mdeis.lessons.edits import parse_edits
from mdeis.lessons.evidence import evidence_sentence
from mdeis.lessons.guard import guard_notes

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "mdeis" / "lessons"
MODEL_OR_NETWORK = {
    "httpx", "requests", "urllib", "urllib3", "http", "socket", "ssl", "aiohttp", "websockets",
    "openai", "anthropic", "langchain", "langchain_core", "langgraph", "litellm", "mcp", "fastapi",
    "subprocess", "asyncio",
}
DATABASE = {"psycopg", "psycopg2", "sqlalchemy", "pgvector"}
NOT_THE_DATABASE_LAYER = (
    "db", "tools", "server", "registry", "core.store", "core.records", "mdeis.db", "mdeis.tools",
    "mdeis.server", "mdeis.registry", "mdeis.core.store", "mdeis.core.records",
)


def modules():
    return sorted(PACKAGE.glob("*.py"))


def imported(path):
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = ("." * node.level) + (node.module or "")
            names.add(base)
            names |= {f"{base}.{a.name}" for a in node.names}
    return names


def test_the_package_has_the_modules_the_slices_name():
    names = {p.stem for p in modules()}
    assert {"parse", "apply", "edits", "checks", "guard", "evidence", "findings", "rules",
            "limits", "store"} <= names


@pytest.mark.parametrize("path", modules(), ids=lambda p: p.name)
def test_no_module_imports_a_model_client_or_a_network_library(path):
    top = {n.lstrip(".").split(".")[0] for n in imported(path)}
    assert not top & MODEL_OR_NETWORK, (path.name, top & MODEL_OR_NETWORK)


@pytest.mark.parametrize("path", [p for p in modules() if p.stem != "store"], ids=lambda p: p.name)
def test_only_the_store_may_touch_the_database_layer(path):
    names = imported(path)
    top = {n.lstrip(".").split(".")[0] for n in names}
    assert not top & DATABASE, (path.name, top & DATABASE)
    for name in names:
        assert not name.lstrip(".").startswith(NOT_THE_DATABASE_LAYER), (path.name, name)


@pytest.mark.parametrize("path", [p for p in modules() if p.stem != "store"], ids=lambda p: p.name)
def test_a_pure_module_reads_no_file_no_environment_and_runs_no_program(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"open", "exec", "eval", "compile", "__import__"}, path.name
        if isinstance(node, ast.Attribute):
            assert node.attr not in {"environ", "getenv", "read_text", "write_text"}, path.name


def test_the_store_imports_no_model_client():
    top = {n.lstrip(".").split(".")[0] for n in imported(PACKAGE / "store.py")}
    assert not top & MODEL_OR_NETWORK


def test_the_whole_checking_path_runs_with_every_new_socket_refused(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a pure lesson function opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)

    parsed = lesson()
    assert guard_notes(parsed.text, roster()).ok
    assert parse_edits('{"edits": []}').error is None
    assert evidence_sentence(finding(), rules())
    checked = check_edits(
        parsed,
        [edit(), add_after(A_PLENARY, " Why?", id="e2", type="add_check")],
        [finding()],
        rules(),
        ("Form 3", "literacy", 2),
        roster(),
    )
    assert apply_edits(parsed, checked.kept).text == checked.revised.text
