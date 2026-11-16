"""The sandbox rules file (templates/sandbox-v1.yaml) and the code that reads it. No database."""

import ast
import copy
import re
from pathlib import Path

import pytest
import yaml

from mdeis.sandbox import config
from mdeis.sandbox.rules import SHAPE, Rules, SandboxRulesError

ROOT = Path(__file__).resolve().parents[2]
FILE = ROOT / "templates" / "sandbox-v1.yaml"
SANDBOX = ROOT / "src" / "mdeis" / "sandbox"
MARK = "# PROPOSED: owner to confirm"


def data():
    return yaml.safe_load(FILE.read_text(encoding="utf-8"))


def test_the_file_loads_and_holds_every_part_the_check_needs():
    rules = config.load_rules(FILE)
    assert isinstance(rules, Rules)
    assert rules.version == "sandbox-v1"
    assert rules.output_kinds and rules.builtins and rules.node_types and rules.modules
    assert all(isinstance(n, int) and n > 0 for n in rules.limits.values())
    assert set(rules.limits) == {"wall_s", "cpu_s", "memory_mb", "input_kb", "output_kb"}


@pytest.mark.parametrize("key", sorted(SHAPE))
def test_a_missing_part_is_refused_by_name(key):
    broken = data()
    del broken[key]
    with pytest.raises(SandboxRulesError, match=key):
        Rules.from_dict(broken)


@pytest.mark.parametrize("key", sorted(SHAPE))
def test_an_empty_or_wrong_part_is_refused(key):
    for wrong in (None, [], {}, "", 0, True):
        broken = data()
        broken[key] = wrong
        # allowed_literals may hold a zero but never be empty; an empty value is refused for all.
        with pytest.raises(SandboxRulesError):
            Rules.from_dict(broken)


@pytest.mark.parametrize("limit", ["wall_s", "cpu_s", "memory_mb", "input_kb", "output_kb"])
def test_a_missing_or_nonpositive_limit_is_refused(limit):
    for bad in (None, 0, -1, "5", True):
        broken = data()
        broken["limits"][limit] = bad
        with pytest.raises(SandboxRulesError, match=limit):
            Rules.from_dict(broken)
    del_case = copy.deepcopy(data())
    del del_case["limits"][limit]
    with pytest.raises(SandboxRulesError, match=limit):
        Rules.from_dict(del_case)


def test_a_node_type_that_python_does_not_have_is_refused():
    broken = data()
    broken["allowed_node_types"].append("NotARealNode")
    with pytest.raises(SandboxRulesError, match="NotARealNode"):
        Rules.from_dict(broken)


def test_a_member_list_that_is_empty_or_not_text_is_refused():
    broken = data()
    broken["allowed_modules"]["math"] = []
    with pytest.raises(SandboxRulesError):
        Rules.from_dict(broken)
    broken = data()
    broken["allowed_modules"]["math"] = [1]
    with pytest.raises(SandboxRulesError):
        Rules.from_dict(broken)


def test_a_file_that_is_missing_or_not_yaml_is_refused_with_a_plain_message(tmp_path):
    with pytest.raises(SandboxRulesError, match="cannot read"):
        config.load_rules(tmp_path / "nope.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("a: [unclosed", encoding="utf-8")
    with pytest.raises(SandboxRulesError, match="not valid YAML"):
        config.load_rules(bad)
    scalar = tmp_path / "scalar.yaml"
    scalar.write_text("just text", encoding="utf-8")
    with pytest.raises(SandboxRulesError):
        config.load_rules(scalar)


def test_the_environment_can_name_the_file(monkeypatch, tmp_path):
    other = tmp_path / "other.yaml"
    text = FILE.read_text(encoding="utf-8").replace("sandbox-v1", "sandbox-test")
    other.write_text(text, encoding="utf-8")
    monkeypatch.setenv(config.SANDBOX_ENV, str(other))
    assert config.load_rules().version == "sandbox-test"


def test_without_an_environment_name_the_repo_file_is_found(monkeypatch):
    monkeypatch.delenv(config.SANDBOX_ENV, raising=False)
    assert config.rules_path().resolve() == FILE.resolve()


def test_every_value_in_the_file_is_marked_as_a_proposal_except_the_version_name():
    unmarked = []
    for number, line in enumerate(FILE.read_text(encoding="utf-8").splitlines(), 1):
        text = line.strip()
        if not text or text.startswith("#") or text.startswith("- "):
            continue
        has_value = re.match(r"^[A-Za-z_]+:\s*\S", text)
        if has_value and "sandbox_version" not in text and MARK not in text:
            unmarked.append((number, text))
    assert not unmarked, unmarked


# --------------------------------------------------------------------------- no rule in the Python
def leaves(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from leaves(value)
    elif isinstance(node, list):
        for value in node:
            yield from leaves(value)
    else:
        yield node


def constants(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and id(n) not in docstrings
    ]


@pytest.mark.parametrize("file", ["rules.py", "static_check.py", "config.py"])
def test_no_list_name_or_limit_from_the_file_is_written_in_the_python(file):
    values = {v for v in leaves(data()) if isinstance(v, str)}
    for value in constants(SANDBOX / file):
        if isinstance(value, str):
            assert value not in values, f"{file} holds the rule value {value!r}"


@pytest.mark.parametrize("file", ["rules.py", "static_check.py", "config.py"])
def test_the_only_numbers_in_the_python_are_zero_and_one(file):
    numbers = [
        v for v in constants(SANDBOX / file)
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    assert set(numbers) <= {0, 1}, f"{file} holds {sorted(set(numbers) - {0, 1})}"


def test_the_runner_side_files_do_not_read_yaml_or_the_disk():
    for file in ("rules.py", "static_check.py"):
        source = (SANDBOX / file).read_text(encoding="utf-8")
        for word in ("import yaml", "open(", "pathlib", "os.environ", "socket", "psycopg"):
            assert word not in source, (file, word)
