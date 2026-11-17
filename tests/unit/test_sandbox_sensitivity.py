"""The sensitivity check (T-070): does each value a function is given change at least one answer?

The functions below are tiny and the expected answers are worked by hand. They run in the real child
(tests/unit/inprocess_runner.py: the static check, the restricted namespace and the exact-text
conversion are the real ones), with no socket. One function reads its threshold from the values and
is sensitive to it. One has a threshold of 1 written into the code, which the static check allows
(0 and 1 are structure), and the sensitivity check is what catches it.
"""

import hashlib
import json

import pytest
from inprocess_runner import Calls

from mdeis.sandbox import sensitivity
from mdeis.sandbox.client import RunnerRefusal
from mdeis.sandbox.sensitivity import NO_OTHER_VALUE, candidates, check_sensitivity


def scores(*values):
    return [{"score": str(v)} for v in values]


# Moved when the last score minus the first is at least the threshold. Worked by hand below.
THRESHOLD_FROM_VALUES = '''\
from fractions import Fraction

OUTPUT_KIND = "number"


def measure(records, params):
    change = Fraction(records[len(records) - 1]["score"]) - Fraction(records[0]["score"])
    return {"moved": change >= Fraction(params["MP-1"])}
'''

# Steady when the spread is at most 1. The 1 is written in the code; the value is read and unused.
HARDCODED_ONE = '''\
from fractions import Fraction

OUTPUT_KIND = "number"


def measure(records, params):
    limit = Fraction(params["MP-2"])
    values = [Fraction(r["score"]) for r in records]
    return {"steady": max(values) - min(values) <= 1}
'''

# The same rule with the value really read.
LIMIT_FROM_VALUES = HARDCODED_ONE.replace("<= 1}", "<= limit}")

# Reads two values and uses one.
USES_ONE_OF_TWO = '''\
from fractions import Fraction

OUTPUT_KIND = "number"


def measure(records, params):
    window = int(params["TR-1.window_terms"])
    change = Fraction(records[len(records) - 1]["score"]) - Fraction(records[0]["score"])
    return {"moved": change >= Fraction(params["MP-1"])}
'''

ORDER_POSITION = '''\
OUTPUT_KIND = "number"


def measure(records, params):
    levels = params["MP-3"].split("\\n")
    return {"position": levels.index(records[0]["level"])}
'''

# Three cases from the Baobab fixture: scores 60, 70 (change 10, the boundary), 52, 58, 63 (change
# 11) and 70, 66, 62 (change -8).
MOVED_TESTS = [
    {"name": "boundary", "records": scores(60, 70), "expected": {"moved": True}},
    {"name": "up_by_eleven", "records": scores(52, 58, 63), "expected": {"moved": True}},
    {"name": "down_by_eight", "records": scores(70, 66, 62), "expected": {"moved": False}},
]


def check(source, tests, params, runner=None, **more):
    runner = Calls() if runner is None else runner
    report = check_sensitivity(
        source, tests, params, output_kind="number", runner=runner, **more
    )
    return report, runner


# ----------------------------------- the other values
@pytest.mark.parametrize(
    "value, expected",
    [
        ("10", ("11", "9")),
        ("1", ("2", "0")),
        ("0", ("1",)),  # minus one is below zero: not valid, left out
        ("3/5", ("8/5",)),  # 3/5 - 1 is below zero
        ("5/2", ("7/2", "3/2")),
        ("2", ("3", "1")),
    ],
)
def test_a_number_is_tried_one_above_and_one_below_when_below_is_valid(value, expected):
    assert candidates(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("A\nB\nC", ("C\nB\nA", "B\nC\nA")),  # reversed, then turned by one place
        ("A\nB", ("B\nA",)),  # both give the same order: tried once
        ("Starting", ()),  # one name has no other order
    ],
)
def test_names_are_tried_in_reverse_and_turned_by_one_place(value, expected):
    assert candidates(value) == expected


def test_the_probe_step_is_a_named_constant_that_is_not_a_school_value():
    from fractions import Fraction

    assert sensitivity.PROBE_STEP == Fraction(1)


# ----------------------------------- a sensitive value
def test_a_threshold_read_from_the_values_is_sensitive_to_it():
    # Threshold 10. Changed to 11: the boundary case (change 10) is no longer moved. The change of
    # 11 is still moved and the change of -8 is still not moved. So only the boundary case answers
    # differently, and the first value tried is enough.
    report, runner = check(THRESHOLD_FROM_VALUES, MOVED_TESTS, {"MP-1": "10"})
    assert report.passed and report.baseline_failed == () and report.insensitive == ()
    (one,) = report.params
    assert (one.key, one.sensitive, one.tried) == ("MP-1", True, ("11",))
    assert one.changed_tests == ("boundary",)
    assert (one.refused_runs, one.note) == (0, "")
    assert len(runner) == 6, "3 tests to begin with, then 3 with the value changed, then it stops"


def test_a_test_of_its_own_values_is_changed_too_so_a_test_that_names_its_value_cannot_hide_it():
    own = {"name": "own_value", "records": scores(60, 70), "params": {"MP-1": "7"},
           "expected": {"moved": True}}  # fmt: skip
    report, _ = check(THRESHOLD_FROM_VALUES, [own, *MOVED_TESTS], {"MP-1": "10"})
    assert report.passed
    # at 11 the boundary and the test that named 7 both stop being moved
    assert report.params[0].changed_tests == ("own_value", "boundary")


def test_the_second_value_is_tried_when_the_first_changes_nothing():
    # Limit 1, steady when the spread is at most the limit. Spreads: 0, 1 and 3.
    tests = [
        {"name": "same", "records": scores(60, 60), "expected": {"steady": True}},
        {"name": "one", "records": scores(60, 61), "expected": {"steady": True}},
        {"name": "three", "records": scores(60, 63), "expected": {"steady": False}},
    ]
    report, _ = check(LIMIT_FROM_VALUES, tests, {"MP-2": "1"})
    # limit 2 changes none of the three answers (0, 1 <= 2 are steady, 3 is not). Limit 0 changes
    # the spread-1 case from steady to not steady.
    assert report.passed
    one = report.params[0]
    assert one.tried == ("2", "0") and one.changed_tests == ("one",)


def test_names_are_sensitive_when_the_order_moves_an_answer():
    # Practising is in the middle: reversed it is still at 1, turned by one place it is at 0.
    tests = [{"name": "middle", "records": [{"level": "Practising"}],
              "params": {}, "expected": {"position": "1"}}]  # fmt: skip
    names = "Starting\nPractising\nConfident"
    report, _ = check(ORDER_POSITION, tests, {"MP-3": names})
    one = report.params[0]
    assert one.sensitive and one.tried == ("Confident\nPractising\nStarting",
                                           "Practising\nConfident\nStarting")  # fmt: skip
    assert one.changed_tests == ("middle",)


# ----------------------------------- a value that is not
def test_a_threshold_written_into_the_code_is_found_even_though_the_static_check_allows_it():
    from mdeis.sandbox import static_check
    from mdeis.sandbox.config import load_rules

    # The static check passes: 1 is structure, the key is declared and read.
    assert static_check.check_source(
        HARDCODED_ONE, output_kind="number", rules=load_rules(), declared_clause_ids=["MP-2"]
    ).passed
    tests = [
        {"name": "same", "records": scores(60, 60), "expected": {"steady": True}},
        {"name": "one", "records": scores(60, 61), "expected": {"steady": True}},
        {"name": "three", "records": scores(60, 63), "expected": {"steady": False}},
    ]
    report, _ = check(HARDCODED_ONE, tests, {"MP-2": "1"})
    assert report.baseline_failed == ()
    assert report.insensitive == ("MP-2",)
    assert not report.passed
    one = report.params[0]
    assert (one.sensitive, one.tried, one.changed_tests) == (False, ("2", "0"), ())


def test_one_value_used_and_one_read_and_ignored_are_told_apart():
    tests = [dict(t) for t in MOVED_TESTS]
    report, _ = check(USES_ONE_OF_TWO, tests, {"MP-1": "10", "TR-1.window_terms": "3"})
    by_key = {p.key: p.sensitive for p in report.params}
    assert by_key == {"MP-1": True, "TR-1.window_terms": False}
    assert report.insensitive == ("TR-1.window_terms",)
    assert [p.key for p in report.params] == sorted(by_key), "the keys are in a fixed order"


def test_a_value_with_no_other_value_to_try_is_reported_and_does_not_pass():
    tests = [{"name": "t", "records": [{"level": "Starting"}], "expected": {"position": "0"}}]
    report, runner = check(ORDER_POSITION, tests, {"MP-3": "Starting"})
    one = report.params[0]
    assert (one.sensitive, one.tried, one.note) == (False, (), NO_OTHER_VALUE)
    assert not report.passed and report.insensitive == ("MP-3",)
    assert len(runner) == 1, "only the test's own run: there is nothing to change"


# ----------------------------------- the tests themselves
def test_a_test_that_misses_its_expected_answer_first_is_listed_and_the_report_does_not_pass():
    wrong = {"name": "wrong", "records": scores(60, 70), "expected": {"moved": False}}
    report, _ = check(THRESHOLD_FROM_VALUES, [wrong, *MOVED_TESTS], {"MP-1": "10"})
    assert report.baseline_failed == ("wrong",)
    assert not report.passed
    assert report.params[0].sensitive, "the other tests are still used"
    assert "wrong" not in report.params[0].changed_tests


def test_a_test_with_no_expected_answer_is_run_and_compared_to_itself_only():
    bare = {"name": "bare", "records": scores(60, 70)}
    report, _ = check(THRESHOLD_FROM_VALUES, [bare], {"MP-1": "10"})
    assert report.baseline_failed == () and report.params[0].changed_tests == ("bare",)


def test_a_run_the_runner_refuses_is_not_a_change_and_is_counted():
    seen = []

    def runner(source, sha, records, params, *, output_kind, **_):
        seen.append(dict(params))
        if params["K"] == "0":
            raise RunnerRefusal("runtime", "ZeroDivisionError")
        return {"output_kind": output_kind, "output": {"v": "same"}}

    tests = [{"name": "a", "records": [], "expected": {"v": "same"}}]
    report = check_sensitivity("x", tests, {"K": "1"}, output_kind="number", runner=runner)
    one = report.params[0]
    assert (one.sensitive, one.tried, one.refused_runs) == (False, ("2", "0"), 1)
    assert [s["K"] for s in seen] == ["1", "2", "0"]


def test_a_baseline_run_the_runner_refuses_counts_as_a_failed_test():
    def runner(*_a, **_k):
        raise RunnerRefusal("timeout", "too slow")

    tests = [{"name": "slow", "records": [], "expected": {"v": "x"}}]
    report = check_sensitivity("x", tests, {"K": "1"}, output_kind="number", runner=runner)
    assert report.baseline_failed == ("slow",) and not report.passed


def test_the_runner_is_given_the_hash_of_the_source_and_the_output_kind():
    got = {}

    def runner(source, sha, records, params, *, output_kind, **_):
        got.update(sha=sha, kind=output_kind, params=params)
        return {"output_kind": output_kind, "output": {}}

    check_sensitivity("abc", [{"name": "a", "records": []}], {"K": "1"},
                      output_kind="series", runner=runner)  # fmt: skip
    assert got["sha"] == hashlib.sha256(b"abc").hexdigest() and got["kind"] == "series"


# ----------------------------------- the report
def test_the_same_input_gives_the_same_report_and_the_inputs_are_not_changed():
    tests = [dict(t) for t in MOVED_TESTS]
    params = {"MP-1": "10"}
    before = (json.dumps(tests), dict(params))
    first, _ = check(THRESHOLD_FROM_VALUES, tests, params)
    second, _ = check(THRESHOLD_FROM_VALUES, tests, params)
    assert first == second
    assert (json.dumps(tests), params) == before


def test_the_report_is_plain_json_and_names_no_record():
    report, _ = check(THRESHOLD_FROM_VALUES, MOVED_TESTS, {"MP-1": "10"})
    data = json.loads(json.dumps(report.to_dict()))
    assert data["passed"] is True and data["insensitive"] == [] and data["baseline_failed"] == []
    assert data["params"][0] == {
        "key": "MP-1", "sensitive": True, "tried": ["11"], "changed_tests": ["boundary"],
        "refused_runs": 0, "note": "",
    }  # fmt: skip
    assert "score" not in json.dumps(data)


def test_the_module_holds_no_school_number_and_no_word_about_a_learner():
    import ast
    from pathlib import Path

    path = Path(sensitivity.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    numbers = {
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
        and not isinstance(n.value, bool)
    }  # fmt: skip
    assert numbers <= {0, 1}, numbers
    text = path.read_text(encoding="utf-8").lower()
    for word in ("weak", "remedial", "gifted", "rank", "pathway"):
        assert word not in text, word
