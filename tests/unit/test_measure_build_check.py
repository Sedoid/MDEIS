"""The deterministic checks on a generated function (T-071): the static check, the tests in the
sandbox, the sensitivity check, and the rule that a generated measure returns exact numbers only.

The sandbox runs in this process (tests/unit/inprocess_runner.py: the real static check, restricted
namespace and exact-text conversion, no socket). The Baobab fixture and template are read from
disk. The expected answers are in tests/unit/build_kit.py, worked by hand from the document's own
cases. Nothing here calls a model."""

import copy

import pytest
from build_kit import (
    HARDCODED_SOURCE,
    MOVED_SOURCE,
    RULES,
    SANDBOX,
    case,
    inputs,
    moved_proposal,
    moved_tests,
    rule_evidence,
    rule_moved,
    rule_window,
    settled_for_moved,
)
from inprocess_runner import Calls

from mdeis.measure_build import engine
from mdeis.sandbox.client import RunnerRefusal


def check(proposal=None, settled=None, runner=None, content=None, section="6.4"):
    runner = Calls() if runner is None else runner
    got = engine.check_function(
        inputs(content), RULES, SANDBOX, section, settled or settled_for_moved(content),
        moved_proposal() if proposal is None else proposal, runner,
    )
    return got, runner


def codes(got, check_name=None):
    return [p["code"] for p in got["problems"] if check_name in (None, p["check"])]


def test_a_correct_function_passes_every_check():
    got, runner = check()
    assert got["status"] == "ok" and got["passed"] is True, got["problems"]
    assert got["problems"] == []
    assert got["static"]["passed"] is True
    assert all(r["passed"] for r in got["test_results"]) and len(got["test_results"]) == 6
    assert got["sensitivity"]["passed"] is True
    assert got["keys"] == ["MP-1", "TR-1.min_scores", "TR-1.window_terms"]
    assert got["clause_ids"] == ["MP-1", "TR-1"]
    assert got["output_schema"] == {
        "kind": "number",
        "fields": ["change", "enough_evidence", "moved_down", "moved_up", "scores_used"],
    }
    assert len(runner) > 0


def test_the_patch_holds_the_clauses_the_function_reads_and_nothing_else():
    got, _ = check()
    patch = got["patch"]
    assert patch["base_template_version"] == "baobab-v1"
    assert [m["id"] for m in patch["measure_params"]] == ["MP-1"]
    assert patch["measure_params"][0]["confirmed_by"] is None


def test_a_clause_the_function_does_not_read_is_left_out_of_the_patch():
    settled = engine.settle_rules(
        inputs(), RULES, "6.4",
        [rule_moved(), rule_window(), rule_evidence(),
         rule_moved(name="steady_at_most", value=8, quote="Steady band | 8 points")],
        iter(range(1, 9)).__next__,
    )  # fmt: skip
    assert [m["id"] for m in settled["mp_rules"]] == ["MP-1", "MP-2"]
    got, _ = check(settled=settled)
    assert got["passed"] is True, got["problems"]
    assert [m["id"] for m in got["patch"]["measure_params"]] == ["MP-1"]
    assert "MP-2" not in got["clause_ids"]


def test_a_hardcoded_threshold_is_rejected_by_the_static_check_before_anything_runs():
    got, runner = check(moved_proposal(source=HARDCODED_SOURCE))
    assert got["passed"] is False
    found = codes(got, "static")
    assert "hardcoded_value" in found and "duplicates_template_value" in found
    duplicate = next(p for p in got["problems"] if p["code"] == "duplicates_template_value")
    assert "MP-1" in duplicate["detail"], "the clause that holds the value is named"
    assert len(runner) == 0, "the source was never run"


def test_a_failing_test_is_named_and_stops_the_function():
    wrong = moved_tests()
    wrong[1]["expected"]["change"] = "11"  # B-06 is 60 then 70: the change is 10
    got, _ = check(moved_proposal(tests=wrong))
    assert got["passed"] is False
    failed = [p for p in got["problems"] if p["code"] == "test_failed"]
    assert [p["test"] for p in failed] == ["case_b06_boundary"]
    assert "change" in failed[0]["detail"]
    assert got["test_results"][1] == {"name": "case_b06_boundary", "passed": False}


@pytest.mark.parametrize(
    "replacement,why",
    [
        ('"moved_up": 1 if change >= threshold else 0', '"moved_up": change >= threshold'),
        ('"moved_up": 1 if change >= threshold else 0', '"moved_up": "moved up"'),
    ],
)
def test_a_generated_measure_returns_exact_numbers_only_no_yes_no_and_no_word(replacement, why):
    source = MOVED_SOURCE.replace(replacement, why)
    got, _ = check(moved_proposal(source=source))
    assert got["passed"] is False
    assert "output_not_numbers" in codes(got, "output")


def test_an_output_key_must_match_the_pattern_in_the_build_rules():
    source = MOVED_SOURCE.replace('"change": change', '"The Change": change')
    got, _ = check(moved_proposal(source=source))
    assert "output_not_numbers" in codes(got, "output")


def test_the_build_kind_must_be_one_the_rules_allow():
    got, runner = check(moved_proposal(output_kind="label"))
    assert got["passed"] is False and "output_kind_not_allowed" in codes(got, "proposal")
    assert len(runner) == 0


def test_a_description_or_name_with_a_banned_word_is_rejected():
    for field, text in (
        ("description", "Shows which learners are weak at the moved rule."),
        ("measure_name", "gifted_flag"),
        ("description", "Sorts learners into a top group."),  # a banned phrase
    ):
        got, _ = check(moved_proposal(**{field: text}))
        assert "banned_word" in codes(got, "wording"), (field, text)


def test_a_clause_the_function_declares_but_the_build_never_settled_is_rejected():
    got, runner = check(moved_proposal(reads=["MP-1", "TR-1", "SC-1"]))
    assert got["passed"] is False and "clause_not_settled" in codes(got, "proposal")
    assert len(runner) == 0


def test_a_clause_declared_and_never_read_is_the_static_checks_param_unused():
    source = MOVED_SOURCE.replace('params["MP-1"]', '"10"')  # no MP-1 any more (a text number too)
    got, _ = check(moved_proposal(source=source))
    found = codes(got, "static")
    assert "param_unused" in found and "hardcoded_value" in found


def test_a_clause_read_but_not_declared_is_the_static_checks_param_key_not_declared():
    got, _ = check(moved_proposal(reads=["TR-1"]))
    assert "param_key_not_declared" in codes(got, "static")


def test_a_key_of_a_declared_clause_that_the_build_never_settled_is_rejected():
    source = MOVED_SOURCE.replace("TR-1.window_terms", "TR-1.min_terms")
    got, runner = check(moved_proposal(source=source))
    assert "key_not_settled" in codes(got, "static")
    assert len(runner) == 0


def test_a_worked_example_whose_quote_is_not_in_the_document_counts_as_the_models_own():
    tests = moved_tests()
    for t in tests:
        if t["source"] == "document_example":
            t["quote"] = "a quote the methodology never made, with 52 and 11 in it"
    got, _ = check(moved_proposal(tests=tests))
    assert got["passed"] is False and "no_independent_test" in codes(got, "tests")
    assert any("not in the document" in n for n in got["notes"])
    assert {t["source"] for t in got["tests"]} == {"build"}, "demoted, not kept as a document test"


def test_a_worked_example_whose_numbers_are_not_in_its_quote_is_demoted_too():
    tests = moved_tests()
    tests[0]["expected"] = {"change": "12", "moved_up": "1"}  # 64 minus 52 is 12
    tests[0]["records"] = [{"score": "52"}, {"score": "58"}, {"score": "64"}]  # 64 not quoted
    got, _ = check(moved_proposal(tests=tests))
    assert got["passed"] is True, "the other worked examples still stand"
    assert {t["name"]: t["source"] for t in got["tests"]}["worked_example_6_4"] == "build"
    assert any("64" in n for n in got["notes"])


def test_one_independent_test_is_enough_and_the_rest_may_be_the_models_own():
    tests = [t for t in moved_tests() if t["name"] in ("worked_example_6_4", "own_fall_of_twelve",
                                                       "case_b08_window", "case_b06_boundary")]
    got, _ = check(moved_proposal(tests=tests))
    assert got["passed"] is True, got["problems"]


def test_only_the_models_own_tests_are_never_enough():
    tests = [t for t in moved_tests() if t["source"] == "build"]
    got, _ = check(moved_proposal(tests=tests))
    assert got["passed"] is False and "no_independent_test" in codes(got, "tests")


def test_a_value_that_changes_no_answer_stops_the_function():
    # the minimum is read and never used: only tests with three scores or more are given
    source = MOVED_SOURCE.replace("len(used) < minimum", "len(used) < 1")
    keep = ("worked_example_6_4", "case_b03_not_moved", "case_b08_window")
    tests = [t for t in moved_tests() if t["name"] in keep]
    # a boundary case that has three scores, so that it does not depend on the minimum
    tests.append(case("own_boundary_three", None, (60, 65, 70), {"change": "10", "moved_up": "1"},
                      source="build"))  # fmt: skip
    got, _ = check(moved_proposal(source=source, tests=tests))
    assert got["passed"] is False
    assert all(r["passed"] for r in got["test_results"]), "the tests themselves all pass"
    insensitive = [p for p in got["problems"] if p["code"] == "parameter_not_sensitive"]
    assert [p["key"] for p in insensitive] == ["TR-1.min_scores"]


def test_a_function_that_ignores_the_window_fails_the_case_that_uses_four_scores():
    source = MOVED_SOURCE.replace('window = int(params["TR-1.window_terms"])', "window = 1")
    got, _ = check(moved_proposal(source=source))
    assert got["passed"] is False
    assert "case_b08_window" in [p["test"] for p in got["problems"] if p["code"] == "test_failed"]


@pytest.mark.parametrize(
    "bad,code",
    [
        (lambda t: t.update(records=[{"score": 52.5}]), "test_invalid"),       # a float
        (lambda t: t.update(records=[{"score": True}]), "test_invalid"),
        (lambda t: t.update(records=[]), "test_invalid"),
        (lambda t: t.update(records=[{"mark": "52"}]), "test_invalid"),        # not a record field
        (lambda t: t.update(expected={}), "test_invalid"),
        (lambda t: t.update(expected={"moved_up": 1.0}), "test_invalid"),
        (lambda t: t.update(source="teacher"), "test_invalid"),   # not the model's to claim
        (lambda t: t.update(source="break_it"), "test_invalid"),
        (lambda t: t.update(name=""), "test_invalid"),
    ],
)
def test_a_test_that_is_not_well_formed_is_named_and_nothing_runs(bad, code):
    tests = moved_tests()
    bad(tests[5])
    got, runner = check(moved_proposal(tests=tests))
    assert code in codes(got, "tests") and got["passed"] is False
    assert len(runner) == 0


def test_two_tests_with_one_name_are_refused_and_no_test_at_all_is_refused():
    tests = moved_tests()
    tests[1]["name"] = tests[0]["name"]
    assert "test_invalid" in codes(check(moved_proposal(tests=tests))[0], "tests")
    assert "test_invalid" in codes(check(moved_proposal(tests=[]))[0], "tests")


@pytest.mark.parametrize("missing", ["source", "reads", "tests", "description", "measure_name"])
def test_a_proposal_missing_a_part_is_refused_by_name(missing):
    proposal = moved_proposal()
    del proposal[missing]
    got, runner = check(proposal)
    assert got["passed"] is False and codes(got) == ["proposal_malformed"]
    assert missing in got["problems"][0]["detail"] and len(runner) == 0


def test_a_sandbox_that_cannot_be_reached_is_an_error_not_a_failed_function():
    def down(*args, **kwargs):
        raise RunnerRefusal("runner_unavailable", "nothing is listening")

    got, _ = check(runner=down)
    assert got["status"] == "error" and got["kind"] == "runner_unavailable"
    assert got["passed"] is False


def test_a_refusal_by_the_sandbox_is_a_problem_to_repair():
    def refuses(source, digest, records, params, *, output_kind, **_):
        raise RunnerRefusal("runtime", "the function raised an error")

    got, _ = check(runner=refuses)
    assert got["status"] == "ok" and got["passed"] is False
    assert "test_run_refused" in codes(got, "tests")


def test_a_patch_the_template_would_refuse_is_caught_before_any_code_runs():
    settled = settled_for_moved()
    settled["mp_rules"][0]["unit"] = "furlongs"
    got, runner = check(settled=settled)
    assert "patch_invalid" in codes(got, "patch") and len(runner) == 0


def test_checking_is_deterministic_and_changes_nothing_it_was_given():
    given, proposal, settled = inputs(), moved_proposal(), settled_for_moved()
    before = copy.deepcopy((given.content, proposal, settled))
    one = engine.check_function(given, RULES, SANDBOX, "6.4", settled, proposal, Calls())
    two = engine.check_function(given, RULES, SANDBOX, "6.4", settled, proposal, Calls())
    assert one == two and (given.content, proposal, settled) == before


def test_the_checks_are_made_in_the_order_the_brief_gives():
    # static first (nothing runs), then the tests, then the sensitivity check
    got, _ = check(moved_proposal(source=HARDCODED_SOURCE))
    assert got["static"]["passed"] is False and got["sensitivity"] is None
    assert got["test_results"] == []
    only_models = [case("t", None, (52, 58, 63), {"change": "11"}, source="build")]
    got, _ = check(moved_proposal(tests=only_models))
    assert got["static"]["passed"] is True and got["test_results"] and got["sensitivity"] is None


def test_a_settlement_that_was_changed_on_the_way_back_is_not_taken_on_trust():
    for change, fits in (
        (lambda m: m.update(text="Moved when the change is 10 points or more, as I say."), "words"),
        (lambda m: m.update(confirmed_by="Ms Teacher"), "confirmed"),
        (lambda m: m.update(source_document="SAV-METH-1.0"), "document"),
        (lambda m: m.update(method_ref="3.2"), "section"),
        (lambda m: m.update(id="TR-1"), "measure_params clause number"),
    ):
        settled = settled_for_moved()
        change(settled["mp_rules"][0])
        got, runner = check(settled=settled)
        assert codes(got) == ["settled_invalid"], fits
        assert fits in got["problems"][0]["detail"], fits
        assert len(runner) == 0
    got, _ = check(settled={"reads": []})
    assert codes(got) == ["settled_invalid"]
