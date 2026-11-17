"""The sensitivity check of a generated function: does each value it is given change an answer?

A function that reads `params["MP-1"]` but also has the same number written inside it (a 0 or a 1 is
allowed by the static check, so a threshold of 0 or 1 can be hidden that way) gives the same answers
whatever the template holds. This check finds it. For each value the function is given, it runs the
function's tests again with that one value changed, and requires that at least one test's answer
changes. A value that changes no answer is either unused or written into the code, and the version
is stopped for a person. Nothing here judges a result: it only compares answers.

    report = check_sensitivity(source, tests, params, output_kind="number")
    report.passed            # every value moved an answer, and every test ran first
    report.insensitive       # the keys no test is sensitive to

The fixed rule for the changed value (the same input always gives the same candidates, in this
order, and only the first that changes an answer is needed):

  * a whole or exact number: the value plus one step, then the value minus one step. A candidate
    below zero is not valid (a template value is never negative) and is left out. The step is
    `PROBE_STEP`, one, a probe size and not a school value;
  * a list of names (one text, the names joined by a line break): the order reversed, then the order
    turned by one place. A list of one name has no other order, so it is reported as
    `no_other_value` and not as passed;
  * the changed value replaces that key in every test, also where a test names its own value.

A run the runner refuses is not a change. It is counted in `refused_runs`, so a person can see
that the check could not tell. A test that did not give its expected answer before any change is
listed in `baseline_failed`, and then the report does not pass.

`runner` has the signature of `client.run` (source, code_sha256, records, params, output_kind=...),
so a test can hand in a runner that works in the same process. The default is `client.run`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from fractions import Fraction
from functools import partial

from ..core.measure_params import NAMES_JOINER, exact_text
from . import client

PROBE_STEP = Fraction(1)  # how far a number is moved to see whether an answer follows it

NO_OTHER_VALUE = "no_other_value"


@dataclass(frozen=True)
class ParamSensitivity:
    key: str
    sensitive: bool
    tried: tuple[str, ...]  # the values tried, in order, as exact text
    changed_tests: tuple[str, ...]  # tests whose answer changed (first value that changed any)
    refused_runs: int  # runs the runner refused while trying this key
    note: str  # "" or NO_OTHER_VALUE

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "sensitive": self.sensitive,
            "tried": list(self.tried),
            "changed_tests": list(self.changed_tests),
            "refused_runs": self.refused_runs,
            "note": self.note,
        }


@dataclass(frozen=True)
class SensitivityReport:
    params: tuple[ParamSensitivity, ...]
    baseline_failed: tuple[str, ...]  # tests that missed their expected answer to begin with

    @property
    def insensitive(self) -> tuple[str, ...]:
        return tuple(p.key for p in self.params if not p.sensitive)

    @property
    def passed(self) -> bool:
        return not self.insensitive and not self.baseline_failed

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "insensitive": list(self.insensitive),
            "baseline_failed": list(self.baseline_failed),
            "params": [p.to_dict() for p in self.params],
        }


def candidates(text: str) -> tuple[str, ...]:
    """The other values to try for one value, in the fixed order (see the module note)."""
    try:
        number = Fraction(text)
    except (ValueError, ZeroDivisionError):
        return _other_orders(text)
    tried = [number + PROBE_STEP, number - PROBE_STEP]
    return tuple(exact_text(n) for n in tried if n >= 0)


def _other_orders(text: str) -> tuple[str, ...]:
    names = text.split(NAMES_JOINER)
    turned = names[1:] + names[:1]
    options = [NAMES_JOINER.join(names[::-1]), NAMES_JOINER.join(turned)]
    out: list[str] = []
    for option in options:
        if option != text and option not in out:
            out.append(option)
    return tuple(out)


def check_sensitivity(
    source: str,
    tests: list[dict],
    params: Mapping[str, str],
    *,
    output_kind: str,
    runner: Callable | None = None,
    rules=None,
    socket_path: str | None = None,
    limits: dict | None = None,
) -> SensitivityReport:
    """Run the tests with each of `params` changed in turn. `params` maps each key the function
    reads to its exact text (as `params_for` builds it). Each test is
    `{"name", "records", "expected", "params"?}`; a test's own `params` override the shared ones."""
    if runner is None:
        runner = partial(client.run, rules=rules, socket_path=socket_path, limits=limits)
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()

    def answer(test: dict, values: dict) -> dict | None:
        try:
            reply = runner(source, digest, test["records"], values, output_kind=output_kind)
        except client.RunnerRefusal:
            return None
        return reply["output"]

    def values_of(test: dict, key: str | None = None, value: str | None = None) -> dict:
        values = {**params, **(test.get("params") or {})}
        if key is not None and value is not None:
            values[key] = value
        return values

    baseline: dict[str, dict] = {}
    failed: list[str] = []
    for test in tests:
        got = answer(test, values_of(test))
        wanted = test.get("expected")
        if got is None or ("expected" in test and got != wanted):
            failed.append(test["name"])
        else:
            baseline[test["name"]] = got

    results = []
    for key in sorted(params):
        results.append(_probe(key, params[key], tests, baseline, answer, values_of))
    return SensitivityReport(params=tuple(results), baseline_failed=tuple(failed))


def _probe(key, value, tests, baseline, answer, values_of) -> ParamSensitivity:
    options = candidates(value)
    if not options:
        return ParamSensitivity(key, False, (), (), 0, NO_OTHER_VALUE)
    tried: list[str] = []
    refused = 0
    for option in options:
        tried.append(option)
        changed = []
        for test in tests:
            if test["name"] not in baseline:
                continue
            got = answer(test, values_of(test, key, option))
            if got is None:
                refused += 1
            elif got != baseline[test["name"]]:
                changed.append(test["name"])
        if changed:
            return ParamSensitivity(key, True, tuple(tried), tuple(changed), refused, "")
    return ParamSensitivity(key, False, tuple(tried), (), refused, "")
