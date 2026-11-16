"""The wire format of the measure runner (T-052, slice 2) and what the rules object gained.

No process is started and no database is used. What these tests hold the code to:
  * a number travels as a string; a JSON float, NaN or a repeated key is refused on the way in;
  * what comes back is built from exact values only (whole numbers and fractions as text), and a
    float, a set or an object with a key that is not text is refused;
  * the requested limits are clamped by the container's own ceilings, so a bad request or a bad file
    can never raise a limit;
  * the rules travel inside the request, so `Rules` turns back into the dict it was read from;
  * the runner side files use the standard library and nothing else of ours, and write no number
    of their own (only unit sizes).
"""

import ast
import copy
import sys
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest
import yaml

from mdeis.sandbox import config, protocol, supervisor
from mdeis.sandbox.rules import LIMIT_KEYS, RUNNER_KEYS, Rules, SandboxRulesError

ROOT = Path(__file__).resolve().parents[2]
FILE = ROOT / "templates" / "sandbox-v1.yaml"
SANDBOX = ROOT / "src" / "mdeis" / "sandbox"
RUNNER_FILES = ["protocol.py", "child.py", "supervisor.py", "client.py"]
MARK = "# PROPOSED: owner to confirm"


def data():
    return yaml.safe_load(FILE.read_text(encoding="utf-8"))


# ------------------------------------------------------------------------------------ parsing
def test_a_plain_object_with_whole_numbers_and_text_is_read():
    got = protocol.parse(b'{"a": "31/2", "b": [1, 2, {"c": true}], "d": null}')
    assert got == {"a": "31/2", "b": [1, 2, {"c": True}], "d": None}


@pytest.mark.parametrize(
    "line",
    [
        b'{"score": 15.5}',
        b'{"score": [1, 2.0]}',
        b'{"score": 1e3}',
        b'{"score": NaN}',
        b'{"score": Infinity}',
        b'{"score": -Infinity}',
        b'{"a": 1, "a": 2}',
        b"not json",
        b"",
        b'{"a": ',
        b"\xff\xfe",
    ],
)
def test_a_float_a_nan_a_repeated_key_or_bad_json_is_refused(line):
    with pytest.raises(protocol.ProtocolError):
        protocol.parse(line)


def test_the_message_for_a_float_says_to_send_exact_numbers_as_text():
    with pytest.raises(protocol.ProtocolError, match="text"):
        protocol.parse(b'{"score": 15.5}')


def test_encoding_is_one_line_sorted_and_the_same_every_time():
    one = protocol.encode({"b": "2", "a": ["x", "y"]})
    assert one == protocol.encode({"a": ["x", "y"], "b": "2"})
    assert one.endswith(b"\n") and one.count(b"\n") == 1
    assert one.index(b'"a"') < one.index(b'"b"')


def test_encoding_refuses_a_float():
    with pytest.raises(protocol.ProtocolError):
        protocol.encode({"a": 1.5})


def test_the_text_of_an_error_line_has_a_kind_and_a_short_detail():
    assert protocol.error("timeout", "too slow") == {
        "status": "error", "kind": "timeout", "detail": "too slow"}
    with pytest.raises(ValueError):
        protocol.error("not_a_kind", "x")


def test_the_kinds_are_the_ones_the_design_names():
    assert set(protocol.KINDS) == {
        "static", "timeout", "memory", "crash", "bad_output", "runtime", "bad_request",
        "runner_unavailable",
    }


# -------------------------------------------------------------------------- the way back out
@pytest.mark.parametrize(
    "value, shown",
    [
        (5, "5"),
        (-7, "-7"),
        (Fraction(31, 2), "31/2"),
        (Fraction(62, 4), "31/2"),
        (Fraction(10, 2), "5"),
        (Fraction(-3, 4), "-3/4"),
        (Decimal("0.60"), "0.60"),
        (Decimal("1E+2"), "100"),
        (Decimal("-0.5"), "-0.5"),
        ("text", "text"),
        (True, True),
        (None, None),
    ],
)
def test_an_exact_value_is_written_as_text(value, shown):
    assert protocol.to_wire(value) == shown


def test_a_structure_is_written_by_walking_it():
    got = protocol.to_wire({"value": Fraction(61, 4), "points": [(1, Fraction(1, 2))], "n": 2})
    assert got == {"value": "61/4", "points": [["1", "1/2"]], "n": "2"}


@pytest.mark.parametrize(
    "value",
    [
        1.5,
        float("nan"),
        {1, 2},
        b"bytes",
        {1: "a"},
        {"a": 1.5},
        [[1.5]],
        Decimal("NaN"),
        Decimal("Infinity"),
        1j,
        object(),
    ],
)
def test_a_float_a_set_bytes_a_bad_key_or_a_non_finite_decimal_is_refused(value):
    with pytest.raises(protocol.BadOutput):
        protocol.to_wire(value)


def test_the_message_for_a_refused_value_names_the_type_and_never_the_value():
    with pytest.raises(protocol.BadOutput) as info:
        protocol.to_wire({"secret_learner_value": 15.5})
    assert "float" in str(info.value)
    assert "15.5" not in str(info.value) and "secret_learner_value" not in str(info.value)


def test_a_structure_that_is_too_deep_is_refused_not_a_crash():
    deep = []
    for _ in range(5000):
        deep = [deep]
    with pytest.raises(protocol.BadOutput):
        protocol.to_wire(deep)


# ------------------------------------------------------------------------ the request shape
def good_request(**over):
    rules = config.load_rules(FILE)
    base = protocol.request(
        source="x = 1\n",
        code_sha256="0" * 64,
        output_kind="number",
        records=[{"score": "15"}],
        params={"CL-1": "3/5"},
        rules=rules.to_dict(),
        limits=dict(rules.limits),
    )
    base.update(over)
    return base


def test_a_good_request_is_accepted_as_it_is():
    request = good_request()
    assert protocol.check_request(request) is request
    assert request["protocol"] == protocol.PROTOCOL_VERSION


@pytest.mark.parametrize(
    "change",
    [
        {"protocol": 99},
        {"source": 5},
        {"source": ""},
        {"code_sha256": "ABC"},
        {"code_sha256": "g" * 64},
        {"output_kind": ""},
        {"records": {}},
        {"records": [1]},
        {"params": []},
        {"params": {"CL-1": 3}},
        {"params": {"CL-1": 0.5}},
        {"rules": []},
        {"limits": []},
        {"limits": {"wall_s": 5}},
        {"limits": {"wall_s": 0, "cpu_s": 2, "memory_mb": 256, "input_kb": 1, "output_kb": 1}},
        {"limits": {"wall_s": "5", "cpu_s": 2, "memory_mb": 256, "input_kb": 1, "output_kb": 1}},
        {"limits": {"wall_s": True, "cpu_s": 2, "memory_mb": 256, "input_kb": 1, "output_kb": 1}},
    ],
)
def test_a_request_that_is_the_wrong_shape_is_refused(change):
    with pytest.raises(protocol.ProtocolError):
        protocol.check_request(good_request(**change))


@pytest.mark.parametrize("key", ["source", "code_sha256", "output_kind", "records", "params",
                                 "rules", "limits", "protocol"])
def test_a_request_missing_a_part_is_refused_by_name(key):
    request = good_request()
    del request[key]
    with pytest.raises(protocol.ProtocolError, match=key):
        protocol.check_request(request)


def test_a_request_that_is_not_an_object_is_refused():
    for bad in ([], "x", 1, None):
        with pytest.raises(protocol.ProtocolError):
            protocol.check_request(bad)


# ------------------------------------------------------------------------------- the limits
CEILINGS = supervisor.Ceilings(wall_s=5, cpu_s=2, memory_mb=256, input_kb=1024, output_kb=256,
                               parallel=4)


def test_a_limit_above_the_ceiling_is_cut_to_the_ceiling():
    asked = {"wall_s": 9999, "cpu_s": 9999, "memory_mb": 9999, "input_kb": 9999, "output_kb": 9999}
    assert supervisor.clamp(asked, CEILINGS) == {
        "wall_s": 5, "cpu_s": 2, "memory_mb": 256, "input_kb": 1024, "output_kb": 256}


def test_a_limit_below_the_ceiling_is_kept():
    asked = {"wall_s": 1, "cpu_s": 1, "memory_mb": 64, "input_kb": 8, "output_kb": 4}
    assert supervisor.clamp(asked, CEILINGS) == asked


def test_every_limit_the_rules_file_names_has_a_ceiling_and_the_other_way_round():
    assert set(LIMIT_KEYS) == {"wall_s", "cpu_s", "memory_mb", "input_kb", "output_kb"}
    assert set(CEILINGS.limits()) == set(LIMIT_KEYS)


def good_environment():
    env = {protocol.CEILING_PREFIX + key.upper(): "7" for key in LIMIT_KEYS}
    env[protocol.PARALLEL_ENV] = "3"
    return env


def test_ceilings_are_read_from_the_environment_of_the_container():
    got = supervisor.Ceilings.from_environment(good_environment())
    assert got.limits() == {key: 7 for key in LIMIT_KEYS}
    assert got.parallel == 3


@pytest.mark.parametrize("missing", [protocol.CEILING_PREFIX + k.upper() for k in LIMIT_KEYS]
                         + [protocol.PARALLEL_ENV])
def test_a_missing_ceiling_stops_the_runner_and_names_the_variable(missing):
    env = good_environment()
    del env[missing]
    with pytest.raises(supervisor.RunnerConfigError, match=missing):
        supervisor.Ceilings.from_environment(env)


@pytest.mark.parametrize("bad", ["", "0", "-3", "abc", "1.5", " "])
def test_a_ceiling_that_is_not_a_whole_number_above_zero_is_refused(bad):
    env = good_environment()
    name = protocol.CEILING_PREFIX + LIMIT_KEYS[0].upper()
    env[name] = bad
    with pytest.raises(supervisor.RunnerConfigError, match=name):
        supervisor.Ceilings.from_environment(env)


# ------------------------------------------------------- the rules object gained a runner block
def test_the_rules_file_has_a_runner_block_and_every_value_is_marked_as_a_proposal():
    block = data()["runner"]
    assert set(block) == set(RUNNER_KEYS)
    text = FILE.read_text(encoding="utf-8")
    inside = text.split("\nrunner:", 1)[1]
    for line in inside.splitlines()[1:]:
        if line.startswith("  ") and ":" in line and not line.strip().startswith("#"):
            assert MARK in line, line


def test_the_rules_hold_the_runner_values():
    rules = config.load_rules(FILE)
    assert set(rules.runner) == set(RUNNER_KEYS)
    assert all(isinstance(n, int) and n > 0 for n in rules.runner.values())


@pytest.mark.parametrize("key", RUNNER_KEYS)
def test_a_missing_or_nonpositive_runner_value_is_refused(key):
    for bad in (None, 0, -1, "5", True):
        broken = data()
        broken["runner"][key] = bad
        with pytest.raises(SandboxRulesError, match=key):
            Rules.from_dict(broken)
    broken = data()
    del broken["runner"][key]
    with pytest.raises(SandboxRulesError, match=key):
        Rules.from_dict(broken)
    broken = data()
    del broken["runner"]
    with pytest.raises(SandboxRulesError, match="runner"):
        Rules.from_dict(broken)


def test_the_rules_turn_back_into_the_dict_they_came_from_and_read_again_the_same():
    rules = config.load_rules(FILE)
    sent = rules.to_dict()
    assert Rules.from_dict(copy.deepcopy(sent)) == rules
    again = Rules.from_dict(sent).to_dict()
    assert again == sent


def test_the_sent_rules_are_plain_json_and_in_a_fixed_order():
    sent = config.load_rules(FILE).to_dict()
    assert protocol.encode(sent) == protocol.encode(config.load_rules(FILE).to_dict())
    for key in ("allowed_builtins", "allowed_node_types", "forbidden_attributes"):
        assert sent[key] == sorted(sent[key])


# --------------------------------------------------------------------- what the files may hold
def imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.add(("." * node.level) + (node.module or ""))
    return found


@pytest.mark.parametrize("file", ["protocol.py", "child.py", "supervisor.py"])
def test_the_runner_side_files_use_the_standard_library_and_the_sandbox_package_only(file):
    for name in imports(SANDBOX / file):
        if name.startswith("."):
            assert name in {".protocol", ".rules", ".static_check", ".child"}, (file, name)
        else:
            assert name.split(".")[0] in sys.stdlib_module_names, (file, name)


def test_the_client_uses_the_standard_library_the_sandbox_package_and_nothing_else():
    for name in imports(SANDBOX / "client.py"):
        if name.startswith("."):
            assert name in {".protocol", ".rules", ".config"}, name
        else:
            assert name.split(".")[0] in sys.stdlib_module_names, name


def numbers(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, (int, float))
        and not isinstance(n.value, bool)
    }


@pytest.mark.parametrize("file", RUNNER_FILES)
def test_no_limit_or_threshold_is_written_in_the_runner_files(file):
    allowed = {0, 1, 1024} if file == "protocol.py" else {0, 1}
    assert numbers(SANDBOX / file) <= allowed, (file, sorted(numbers(SANDBOX / file) - allowed))


def test_the_runner_files_hold_none_of_the_rule_lists_as_text():
    rules = data()
    names = set(rules["allowed_builtins"]) | set(rules["float_names"])
    names |= set(rules["forbidden_attributes"]) | set(rules["allowed_node_types"])
    for module, members in rules["allowed_modules"].items():
        names |= {module, *members}
    for file in RUNNER_FILES:
        tree = ast.parse((SANDBOX / file).read_text(encoding="utf-8"))
        docs = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    docs.add(id(first.value))
        for node in ast.walk(tree):
            is_text = isinstance(node, ast.Constant) and isinstance(node.value, str)
            if is_text and id(node) not in docs:
                assert node.value not in names, f"{file} holds the rule value {node.value!r}"
