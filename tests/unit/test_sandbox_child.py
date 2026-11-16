"""The child of the measure runner: one request in, one answer out (T-052, slice 2).

The first group calls `child.answer` in this process (no limits, nothing started), with sources that
pass the static check. The second group starts a REAL child process for each call, the way the
supervisor does, and holds it to the time and memory limits. No Docker is needed.

What these tests hold the code to:
  * a valid function gives its result as exact text, never a float;
  * a changed hash, a source the static check refuses, a float in or out, and every other bad
    request are refused with a typed error, and the function is not run;
  * an error never carries record data: only the name of the exception type;
  * what the function can reach at run time is only what the rules allow, even if the static check
    were bypassed;
  * a loop that never ends meets the time limit, and a function that asks for too much memory meets
    the memory limit;
  * no state is left for the next call, and the same input gives the same output.
"""

import hashlib
import time
from pathlib import Path

import pytest

from mdeis.sandbox import child, config, protocol, supervisor

ROOT = Path(__file__).resolve().parents[2]
FILE = ROOT / "templates" / "sandbox-v1.yaml"
RULES = config.load_rules(FILE)

GOOD = '''\
from fractions import Fraction

OUTPUT_KIND = "number"


def measure(records, params):
    total = Fraction(0)
    for record in records:
        total += Fraction(record["score"])
    return {"value": Fraction(total, len(records)), "count": len(records)}
'''

DECIMAL = '''\
from decimal import Decimal

OUTPUT_KIND = "number"


def measure(records, params):
    return {"weight": Decimal(params["CL-1"]), "ok": True, "note": "text", "none": None}
'''

MATH = '''\
import math

OUTPUT_KIND = "number"


def measure(records, params):
    return {"value": math.isqrt(len(records))}
'''

KEEPS_STATE = '''\
OUTPUT_KIND = "number"
SEEN = []


def measure(records, params):
    SEEN.append(1)
    return {"calls_so_far": len(SEEN)}
'''

SET_ORDER = '''\
OUTPUT_KIND = "number"


def measure(records, params):
    return {"order": list({"a", "b", "c", "d", "e", "f", "g"})}
'''

LOOPS_FOR_EVER = '''\
OUTPUT_KIND = "number"


def measure(records, params):
    for step in range(int(params["n"])):
        pass
    return {"done": "yes"}
'''

TAKES_TOO_MUCH = '''\
OUTPUT_KIND = "number"


def measure(records, params):
    big = "a" * int(params["n"])
    return {"size": len(big)}
'''

RECORDS = [{"score": "31/2"}, {"score": "15"}]


def request(source=GOOD, records=RECORDS, params=None, kind="number", rules=None, limits=None,
            sha=None):
    return protocol.request(
        source=source,
        code_sha256=sha or hashlib.sha256(source.encode("utf-8")).hexdigest(),
        output_kind=kind,
        records=records,
        params=params or {},
        rules=rules or RULES.to_dict(),
        limits=limits or dict(RULES.limits),
    )


def ask(**kw):
    return child.answer(protocol.encode(request(**kw)))


def limits(**over):
    return {**RULES.limits, **over}


# ------------------------------------------------------------------------------ a good call
def test_a_valid_function_gives_its_result_as_exact_text():
    reply = ask()
    # (31/2 + 15) = 61/2, over two records = 61/4
    assert reply == {"status": "ok", "output_kind": "number",
                     "output": {"value": "61/4", "count": "2"}}


def test_a_whole_result_is_written_without_a_denominator():
    assert ask(records=[{"score": "3"}, {"score": "7"}])["output"]["value"] == "5"


def test_a_decimal_keeps_its_places_and_text_booleans_and_nothing_pass_through():
    reply = ask(source=DECIMAL, params={"CL-1": "0.60"})
    assert reply["output"] == {"weight": "0.60", "ok": True, "note": "text", "none": None}


def test_an_allowed_module_member_can_be_used():
    assert ask(source=MATH, records=[{}, {}, {}, {}])["output"] == {"value": "2"}


def test_the_answer_is_one_line_of_json_with_no_float_in_it():
    line = protocol.encode(ask())
    assert line.count(b"\n") == 1
    assert protocol.parse(line)["status"] == "ok"


def test_the_same_request_gives_the_same_answer_every_time():
    assert ask() == ask() == ask()


# --------------------------------------------------------------------------- a bad request
def test_a_changed_source_with_the_old_hash_is_refused_and_not_run():
    original = hashlib.sha256(GOOD.encode("utf-8")).hexdigest()
    reply = ask(source=GOOD + "# one more line\n", sha=original)
    assert reply["status"] == "error" and reply["kind"] == "bad_request"
    assert "hash" in reply["detail"]


def test_a_hash_in_capitals_or_the_wrong_length_is_refused_as_a_bad_request():
    for bad in (hashlib.sha256(GOOD.encode()).hexdigest().upper(), "ab" * 10):
        reply = child.answer(protocol.encode({**request(), "code_sha256": bad}))
        assert reply["kind"] == "bad_request"


def test_a_source_the_static_check_refuses_is_refused_with_the_findings_named():
    source = src("return {}", extra_import="import os\n\n")
    reply = ask(source=source)
    assert reply["status"] == "error" and reply["kind"] == "static"
    assert "import_not_allowed" in reply["detail"]


def test_the_static_check_is_run_again_for_a_literal_and_a_float_name_too():
    for body, code in (
        ('return {"v": 5}', "hardcoded_value"),
        ('return {"v": float(1)}', "float_not_allowed"),
        ('return {"v": open}', "name_not_allowed"),
    ):
        source = f'OUTPUT_KIND = "number"\n\n\ndef measure(records, params):\n    {body}\n'
        reply = ask(source=source)
        assert reply["kind"] == "static" and code in reply["detail"], (body, reply)


def test_a_declared_kind_that_differs_from_the_requested_one_is_a_static_refusal():
    reply = ask(kind="series")
    assert reply["kind"] == "static" and "output_kind_mismatch" in reply["detail"]


def test_a_float_in_the_input_is_refused_and_the_function_is_not_run():
    line = protocol.encode(request()).replace(b'"31/2"', b"15.5")
    reply = child.answer(line)
    assert reply["status"] == "error" and reply["kind"] == "bad_request"
    assert "text" in reply["detail"]


@pytest.mark.parametrize(
    "line",
    [b"", b"not json", b"[]", b'{"protocol": 1}', b'{"a": 1, "a": 2}', b'{"records": NaN}'],
)
def test_input_that_is_not_a_request_is_refused_as_a_bad_request(line):
    assert child.answer(line)["kind"] == "bad_request"


def test_rules_that_are_not_valid_are_a_bad_request():
    broken = RULES.to_dict()
    del broken["allowed_builtins"]
    assert ask(rules=broken)["kind"] == "bad_request"


def test_params_must_be_text():
    line = protocol.encode({**request(), "params": {"CL-1": 3}})
    assert child.answer(line)["kind"] == "bad_request"


# ---------------------------------------------------------------------------- a bad answer
def src(body, extra_import=""):
    return (
        f'{extra_import}OUTPUT_KIND = "number"\n\n\ndef measure(records, params):\n'
        f"    {body}\n"
    )


def with_division():
    allowed = RULES.to_dict()
    allowed["allowed_node_types"].append("Div")
    return allowed


def test_a_float_in_the_output_is_refused_and_the_value_is_not_in_the_answer():
    # Division is allowed here only so a float can be made at all: the rules file refuses it.
    reply = ask(source=src('return {"v": 1 / 1}'), rules=with_division())
    assert reply["status"] == "error" and reply["kind"] == "bad_output"
    assert "float" in reply["detail"] and "1.0" not in reply["detail"]


def test_the_default_rules_stop_a_float_before_it_can_be_made():
    reply = ask(source=src('return {"v": 1 / 1}'))
    assert reply["kind"] == "static"


@pytest.mark.parametrize(
    "body",
    ['return {"v": set()}', 'return {1: "a"}', 'return ["a"]', 'return "a"', 'return 1'],
)
def test_an_output_that_is_not_an_object_of_exact_values_is_refused(body):
    assert ask(source=src(body))["kind"] == "bad_output"


def test_an_output_over_the_size_limit_is_refused():
    body = 'return {"t": "a" * int(params["n"])}'
    reply = ask(source=src(body), params={"n": "300000"})
    assert reply["kind"] == "bad_output" and "large" in reply["detail"]
    small = ask(source=src(body), params={"n": "100"})
    assert small["status"] == "ok"


def test_the_output_limit_is_the_one_in_the_request():
    body = 'return {"t": "a" * int(params["n"])}'
    tight = ask(source=src(body), params={"n": "2000"}, limits=limits(output_kb=1))
    assert tight["kind"] == "bad_output"


def test_an_error_inside_the_function_names_the_exception_type_and_never_the_data():
    reply = ask(source=src('return {"v": records[0]["secret_name_xyz"]}'))
    assert reply == {"status": "error", "kind": "runtime", "detail": "KeyError"}
    reply = ask(source=src('return {"v": [][1]}'))  # the rules allow only the numbers 0 and 1
    assert reply == {"status": "error", "kind": "runtime", "detail": "IndexError"}


def test_endless_recursion_is_a_runtime_error_not_a_crash():
    source = (
        'OUTPUT_KIND = "number"\n\n\ndef again(records):\n    return again(records)\n\n\n'
        'def measure(records, params):\n    return {"v": again(records)}\n'
    )
    assert ask(source=source) == {"status": "error", "kind": "runtime",
                                  "detail": "RecursionError"}


def test_the_function_is_never_given_a_chance_to_catch_what_stops_it():
    # `try` is not in the allowed syntax, so the static check refuses it before it can run.
    source = src("try:\n        return {}\n    except Exception:\n        return {}")
    assert ask(source=source)["kind"] == "static"


# --------------------------------------------------------- the run-time guard on the builtins
def namespace():
    return child.restricted_namespace(RULES)


def test_the_builtins_are_the_allowed_ones_and_a_guarded_import_and_nothing_else():
    names = set(namespace()["__builtins__"])
    assert names == set(RULES.builtins) | {"__import__"}


@pytest.mark.parametrize(
    "code",
    [
        "x = open",
        "x = eval",
        "x = exec",
        "x = compile",
        "x = getattr",
        "x = setattr",
        "x = vars",
        "x = globals",
        "x = type",
        "x = input",
        "x = print",
        "x = float",
        "x = round",
        "x = object",
        "x = memoryview",
        "x = id",
    ],
)
def test_a_builtin_that_is_not_on_the_list_does_not_exist_for_the_function(code):
    with pytest.raises(NameError):
        exec(code, namespace())


@pytest.mark.parametrize(
    "code",
    [
        "import os",
        "import sys",
        "import socket",
        "import subprocess",
        "import importlib",
        "import builtins",
        "import json",
        "import os.path",
        "from os import path",
        "from . import x",
        "from .math import gcd",
        "from math import sqrt",
        "from math import *",
        "from fractions import Decimal",
        'x = __import__("os")',
        'x = __import__("math", None, None, ("sqrt",), 0)',
        'x = __import__("fractions", None, None, (), 1)',
    ],
)
def test_an_import_that_got_past_the_static_check_is_still_refused_at_run_time(code):
    with pytest.raises(ImportError):
        exec(code, namespace())


def test_an_allowed_import_works_and_gives_only_the_listed_members():
    space = namespace()
    exec("from math import gcd\nr = gcd(12, 18)", space)
    assert space["r"] == 6
    exec("import fractions\nq = fractions.Fraction(1, 2)", space)
    assert str(space["q"]) == "1/2"
    with pytest.raises(AttributeError):
        exec("import math\nx = math.sqrt", space)
    with pytest.raises(AttributeError):
        exec("import fractions\nx = fractions.sys", space)


def test_the_module_a_function_gets_is_not_the_real_module():
    space = namespace()
    exec("import math\nm = math", space)
    import math

    assert space["m"] is not math


# --------------------------------------------------------- a real process: time and memory
def run_for_real(source, params=None, records=RECORDS, **over):
    line = protocol.encode(request(source=source, params=params, records=records,
                                   limits=limits(**over)))
    return supervisor.execute(line, limits(**over))


def test_a_real_child_runs_a_good_function():
    assert run_for_real(GOOD)["output"] == {"value": "61/4", "count": "2"}


def test_a_function_that_loops_for_ever_meets_the_wall_clock_limit():
    started = time.monotonic()
    reply = run_for_real(LOOPS_FOR_EVER, {"n": "1000000000000"}, wall_s=1, cpu_s=30)
    assert reply["status"] == "error" and reply["kind"] == "timeout"
    assert time.monotonic() - started < 10


def test_a_function_that_burns_processor_time_meets_the_cpu_limit():
    started = time.monotonic()
    reply = run_for_real(LOOPS_FOR_EVER, {"n": "1000000000000"}, wall_s=30, cpu_s=1)
    assert reply["status"] == "error" and reply["kind"] == "timeout"
    assert time.monotonic() - started < 20


def test_a_function_that_asks_for_too_much_memory_meets_the_memory_limit():
    reply = run_for_real(TAKES_TOO_MUCH, {"n": "4000000000"}, memory_mb=256)
    assert reply == {"status": "error", "kind": "memory",
                     "detail": "the function used more memory than the limit"}


def test_a_function_within_the_limits_is_not_touched_by_them():
    reply = run_for_real(TAKES_TOO_MUCH, {"n": "1000"})
    assert reply["output"] == {"size": "1000"}


def test_two_calls_do_not_share_state():
    first = run_for_real(KEEPS_STATE)
    second = run_for_real(KEEPS_STATE)
    assert first["output"] == {"calls_so_far": "1"}
    assert second["output"] == {"calls_so_far": "1"}


def test_the_same_function_gives_the_same_order_from_a_set_in_every_fresh_process():
    orders = [run_for_real(SET_ORDER)["output"]["order"] for _ in range(3)]
    assert orders[0] == orders[1] == orders[2]
    assert sorted(orders[0]) == list("abcdefg")


def test_the_child_writes_nothing_but_the_one_answer_to_its_output():
    # execute() reads one line and checks it parses: anything else on stdout would be bad_output.
    reply = run_for_real(GOOD)
    assert reply["status"] == "ok"
