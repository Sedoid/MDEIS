"""The params channel (T-070): the values a generated measure reads, built from the ACTIVE template.

`params_for` is pure and returns exact text for each key. `with_template.run` is the call path: it
builds the values, refuses before anything is sent, and otherwise calls the runner as `client.run`
does. A function can read only keys its registry version declares. No database, no socket: the
runner is the real child, called in this process (tests/unit/inprocess_runner.py).

Expected values are worked by hand from the content typed below.
"""

import builtins
import copy
import hashlib
import json
import pathlib

import pytest
from inprocess_runner import Calls

from mdeis.core.measure_params import NAMES_JOINER, REFUSALS, ParamsRefusal, params_for
from mdeis.sandbox import protocol, static_check, with_template
from mdeis.sandbox.config import load_rules

RULES = load_rules()
SEP = RULES.key_separator


def param(rule_id, value_type, value, unit, confirmed_by="Ada Teacher"):
    return {
        "id": rule_id, "name": "n_" + rule_id[3:], "value_type": value_type, "value": value,
        "unit": unit, "source_document": "BAO-METH-1.0", "method_ref": "6.4",
        "confirmed_by": confirmed_by, "text": "t",
    }  # fmt: skip


CONTENT = {
    "scales": [
        {"id": "SC-2", "grade_bands": [{"grade": "A", "min": 80}], "text": "bands"},
    ],
    "trend": [
        {"id": "TR-1", "min_scores": 2, "min_terms": 2, "window_terms": 3, "method_ref": "5",
         "text": "evidence"},
        {"id": "TR-2", "move_threshold": 10.0, "text": "move"},
        {"id": "TR-3", "missing_term": "ask_approver", "text": "missing"},
        {"id": "TR-4", "gap_direction_threshold": 2.5, "text": "gap"},
    ],
    "measure_params": [
        param("MP-1", "integer", 10, "points"),
        param("MP-2", "integer", 2, "checks", confirmed_by=None),
        param("MP-3", "names", ["Starting", "Practising", "Confident"], "none"),
        param("MP-4", "rational", "0.6", "percent"),
        param("MP-5", "integer", 0, "points"),
    ],
}  # fmt: skip
ALL_DECLARED = ["MP-1", "MP-2", "MP-3", "MP-4", "MP-5", "TR-1", "TR-2", "TR-3", "TR-4", "SC-2"]


def build(keys, declared=ALL_DECLARED, content=CONTENT, **more):
    return params_for(content, keys, declared, separator=SEP, **more)


def refusal(keys, declared=ALL_DECLARED, content=CONTENT, **more):
    with pytest.raises(ParamsRefusal) as info:
        build(keys, declared, content, **more)
    assert info.value.kind in REFUSALS
    return info.value


# ------------------------------------------------------------------------------ what is built
def test_a_parameter_and_two_fields_are_built_as_exact_text():
    got = build(["MP-1", "TR-1.window_terms", "TR-1.min_scores"])
    assert got == {"MP-1": "10", "TR-1.min_scores": "2", "TR-1.window_terms": "3"}
    assert list(got) == sorted(got), "the keys are in a fixed order"


def test_a_float_field_is_made_exact_and_a_rational_is_in_lowest_terms():
    # 10.0 is the whole number 10. 2.5 is 5/2. "0.6" is 3/5. 0 is "0".
    got = build(["TR-2.move_threshold", "TR-4.gap_direction_threshold", "MP-4", "MP-5"])
    assert got == {
        "TR-2.move_threshold": "10",
        "TR-4.gap_direction_threshold": "5/2",
        "MP-4": "3/5",
        "MP-5": "0",
    }


def test_a_list_of_names_is_one_text_in_the_templates_order():
    got = build(["MP-3"])["MP-3"]
    assert got == "Starting\nPractising\nConfident"
    assert got.split(NAMES_JOINER) == ["Starting", "Practising", "Confident"]


def test_every_built_value_is_text_and_the_protocol_accepts_the_whole_object():
    got = build(["MP-1", "MP-3", "TR-2.move_threshold", "TR-4.gap_direction_threshold"])
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in got.items())
    line = protocol.encode(
        protocol.request(
            source="x", code_sha256="a" * 64, output_kind="number", records=[], params=got,
            rules={}, limits=dict(RULES.limits),
        )
    )
    assert protocol.check_request(protocol.parse(line))["params"] == got


def test_a_repeated_key_is_built_once_and_nothing_extra_is_added():
    got = build(["MP-1", "MP-1"])
    assert got == {"MP-1": "10"}


def test_the_same_input_gives_the_same_output_and_the_content_is_not_changed():
    before = copy.deepcopy(CONTENT)
    first = build(["TR-1.window_terms", "MP-1", "MP-3"])
    assert build(["MP-3", "MP-1", "TR-1.window_terms"]) == first
    again = build(["MP-1", "MP-3", "TR-1.window_terms"])
    assert json.dumps(first) == json.dumps(again)
    assert CONTENT == before


def test_the_separator_comes_from_the_caller_and_is_not_written_in_the_module():
    got = params_for(CONTENT, ["TR-1:window_terms"], ["TR-1"], separator=":")
    assert got == {"TR-1:window_terms": "3"}
    with pytest.raises(ParamsRefusal) as info:
        params_for(CONTENT, ["TR-1.window_terms"], ["TR-1"], separator=":")
    assert info.value.kind == "key_malformed"


def test_it_reads_no_file(monkeypatch):
    def no(*_a, **_k):
        raise AssertionError("a file was opened")

    monkeypatch.setattr(builtins, "open", no)
    monkeypatch.setattr(pathlib.Path, "read_text", no)
    monkeypatch.setattr(pathlib.Path, "open", no)
    assert build(["MP-1"]) == {"MP-1": "10"}


# ------------------------------------------------------------------------------ what is refused
def test_a_key_whose_clause_the_version_does_not_declare_is_refused():
    r = refusal(["TR-2.move_threshold"], declared=["TR-1", "MP-1"])
    assert r.kind == "key_not_declared" and "TR-2" in r.detail
    assert refusal(["MP-1"], declared=[]).kind == "key_not_declared"


def test_a_clause_the_active_template_does_not_have_is_missing():
    assert refusal(["MP-9"], declared=["MP-9"]).kind == "clause_missing"
    assert refusal(["TR-9.window_terms"], declared=["TR-9"]).kind == "clause_missing"
    # the rule is there, the field is not (TR-1 does not set move_threshold)
    assert refusal(["TR-1.move_threshold"]).kind == "clause_missing"
    assert refusal(["TR-1.not_a_field"]).kind == "clause_missing"


def test_a_template_with_no_section_is_a_missing_clause_not_a_crash():
    plain = {k: v for k, v in CONTENT.items() if k != "measure_params"}
    assert refusal(["MP-1"], content=plain).kind == "clause_missing"


def test_a_value_no_teacher_has_confirmed_is_refused_as_a_placeholder():
    r = refusal(["MP-2"])
    assert r.kind == "placeholder_value" and "MP-2" in r.detail
    assert build(["MP-1"]) == {"MP-1": "10"}, "a confirmed one beside it is fine"


def test_a_changed_type_or_unit_is_refused_and_an_unchanged_one_is_not():
    recorded = {"MP-1": ("integer", "points")}
    assert build(["MP-1"], recorded=recorded) == {"MP-1": "10"}
    assert refusal(["MP-1"], recorded={"MP-1": ("integer", "scores")}).kind == "clause_changed"
    assert refusal(["MP-1"], recorded={"MP-1": ("rational", "points")}).kind == "clause_changed"
    assert build(["MP-1"], recorded={"MP-4": ("rational", "percent")}) == {"MP-1": "10"}


@pytest.mark.parametrize(
    "key", ["SC-2.grade_bands", "TR-3.missing_term", "TR-1.text", "TR-1.method_ref"]
)
def test_a_field_that_is_not_a_single_number_is_not_readable(key):
    assert refusal([key]).kind == "value_not_readable"


def test_a_boolean_or_a_broken_value_is_not_readable():
    broken = copy.deepcopy(CONTENT)
    broken["trend"][0]["window_terms"] = True
    assert refusal(["TR-1.window_terms"], content=broken).kind == "value_not_readable"
    broken["measure_params"][0]["value"] = "10"  # an integer rule that holds text
    assert refusal(["MP-1"], content=broken).kind == "value_not_readable"
    broken["measure_params"][2]["value"] = ["A", 1]
    assert refusal(["MP-3"], content=broken).kind == "value_not_readable"


@pytest.mark.parametrize("key", ["TR-1", "TR-1.", "nonsense", "", "MP-1.value", "mp-1", "MP-0"])
def test_a_key_of_the_wrong_shape_is_malformed(key):
    assert refusal([key], declared=[key, "TR-1", "MP-1"]).kind == "key_malformed"


def test_the_first_refusal_is_the_same_every_time():
    keys = ["TR-9.x", "MP-9", "MP-2"]
    kinds = {refusal(keys, declared=ALL_DECLARED + ["TR-9", "MP-9"]).kind for _ in range(3)}
    assert kinds == {"placeholder_value"}, "keys are taken in sorted order: MP-2 comes first"


def test_a_refusal_names_the_key_and_never_a_record():
    for key in ("MP-2", "MP-9"):
        text = str(refusal([key], declared=ALL_DECLARED + ["MP-9"]))
        assert key in text


# ------------------------------------------------------------------------------ the call path
SOURCE = '''\
from fractions import Fraction

OUTPUT_KIND = "number"


def measure(records, params):
    scores = [Fraction(r["score"]) for r in records]
    change = scores[len(scores) - 1] - scores[0]
    moved = change >= Fraction(params["MP-1"])
    window = int(params["TR-1.window_terms"])
    return {"change": change, "moved": moved, "window": window}
'''
SHA = hashlib.sha256(SOURCE.encode("utf-8")).hexdigest()
DECLARED = ["MP-1", "TR-1"]


def call(records, content=CONTENT, declared=DECLARED, source=SOURCE, runner=None, **more):
    runner = Calls() if runner is None else runner
    sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
    result = with_template.run(
        source, sha, records, content, declared, output_kind="number", rules=RULES,
        runner=runner, **more,
    )  # fmt: skip
    return result, runner


def test_the_keys_the_source_reads_are_the_keys_that_are_built():
    assert static_check.params_read(SOURCE, RULES) == ("MP-1", "TR-1.window_terms")
    result, runner = call([{"score": "60"}, {"score": "70"}])
    assert runner[0]["params"] == {"MP-1": "10", "TR-1.window_terms": "3"}
    assert result["params"] == runner[0]["params"]


def test_the_source_passes_the_static_check_with_its_declared_clauses():
    report = static_check.check_source(
        SOURCE, output_kind="number", rules=RULES, declared_clause_ids=DECLARED
    )
    assert report.passed, report.findings


def test_the_boundary_case_runs_with_the_value_from_the_template():
    # Baobab B-06: scores 60 then 70, change 10, moved at "10 or more". Worked by hand.
    result, _ = call([{"score": "60"}, {"score": "70"}])
    assert result["output_kind"] == "number"
    assert result["output"] == {"change": "10", "moved": True, "window": "3"}


def test_a_changed_value_in_the_template_changes_the_answer_and_the_code_does_not():
    other = copy.deepcopy(CONTENT)
    other["measure_params"][0]["value"] = 11
    result, _ = call([{"score": "60"}, {"score": "70"}], content=other)
    assert result["output"]["moved"] is False and result["params"]["MP-1"] == "11"


def test_a_function_that_reads_an_undeclared_key_is_refused_and_the_runner_is_not_called():
    source = SOURCE.replace('params["TR-1.window_terms"]', 'params["TR-2.move_threshold"]')
    runner = Calls()
    with pytest.raises(ParamsRefusal) as info:
        call([{"score": "60"}, {"score": "70"}], source=source, runner=runner)
    assert info.value.kind == "key_not_declared"
    assert len(runner) == 0


def test_an_unconfirmed_value_stops_the_run_before_anything_is_sent():
    source = SOURCE.replace('params["MP-1"]', 'params["MP-2"]')
    runner = Calls()
    with pytest.raises(ParamsRefusal) as info:
        call([{"score": "60"}, {"score": "70"}], source=source, declared=["MP-2", "TR-1"],
             runner=runner)  # fmt: skip
    assert info.value.kind == "placeholder_value"
    assert len(runner) == 0


def test_a_function_cannot_reach_a_key_it_was_not_given_at_run_time():
    # Even with the wrapper out of the way, the runner only holds the keys it was sent.
    from mdeis.sandbox.client import RunnerRefusal

    with pytest.raises(RunnerRefusal) as info:
        Calls()(SOURCE, SHA, [{"score": "60"}, {"score": "70"}], {"MP-1": "10"},
                output_kind="number")  # fmt: skip
    assert info.value.kind == "runtime", "the missing key is a KeyError inside the function"


def test_the_call_does_not_change_the_runner_protocol_or_the_client():
    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "mdeis" / "sandbox"
    client = (root / "client.py").read_text(encoding="utf-8")
    assert "measure_params" not in client and "params_for" not in client
    assert "isinstance(v, str)" in (root / "protocol.py").read_text(encoding="utf-8"), (
        "the protocol still refuses a params value that is not text"
    )
