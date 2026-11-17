"""The two static checks that read the values channel (T-070). Standard library only, no database.

`param_key_not_declared`: a value is read by a key that is not plain text, whose clause the version
does not declare, or the values parameter is used in any other way than a subscript read.
`param_unused`: a clause the version declares is never read.
They are made only when the declared clause IDs are given. The name of the parameter and the
separator inside a key are read from templates/sandbox-v1.yaml, so this file also changes them.
"""

import textwrap
from pathlib import Path

import pytest
import yaml

from mdeis.sandbox import static_check
from mdeis.sandbox.config import load_rules
from mdeis.sandbox.rules import Rules

ROOT = Path(__file__).resolve().parents[2]
FILE = ROOT / "templates" / "sandbox-v1.yaml"
RULES = load_rules()
FRACTION = "from fractions import Fraction"


def wrap(body, imports=FRACTION, kind="number", parameters="records, params"):
    inner = textwrap.indent(textwrap.dedent(body).strip("\n"), "    ")
    return f'{imports}\nOUTPUT_KIND = "{kind}"\n\n\ndef measure({parameters}):\n{inner}\n'


def check(source, declared, rules=RULES, values=None):
    return static_check.check_source(
        source,
        output_kind="number",
        rules=rules,
        template_values=values,
        declared_clause_ids=declared,
    )


def codes(report):
    return [f.code for f in report.findings]


READS_ONE = wrap('return {"v": str(Fraction(params["MP-1"]))}')


# ----------------------------------- what passes
def test_reading_a_declared_parameter_by_its_clause_id_passes():
    report = check(READS_ONE, ["MP-1"])
    assert report.passed, report.findings


def test_reading_a_field_of_a_declared_clause_passes_and_one_clause_may_have_several_keys():
    source = wrap(
        'a = int(params["TR-1.window_terms"])\n'
        'b = int(params["TR-1.min_scores"])\n'
        'return {"v": str(Fraction(params["MP-1"]) + a + b)}'
    )
    assert check(source, ["MP-1", "TR-1"]).passed
    assert check(source, ["TR-1", "MP-1"]).passed, "the order of the declared list does not matter"


def test_the_same_key_read_twice_is_one_read():
    source = wrap('return {"v": str(Fraction(params["MP-1"]) + Fraction(params["MP-1"]))}')
    assert check(source, ["MP-1"]).passed


def test_without_the_declared_clauses_the_two_checks_are_not_made():
    source = wrap('return {"v": params["ANY-9"], "w": params}')
    plain = static_check.check_source(source, output_kind="number", rules=RULES)
    assert "param_key_not_declared" not in codes(plain)
    only_unused = static_check.check_source(READS_ONE, output_kind="number", rules=RULES)
    assert only_unused.passed


def test_a_key_text_and_a_clause_id_are_not_numbers_for_the_hardcoded_value_rule():
    assert check(READS_ONE, ["MP-1"]).passed
    assert "hardcoded_value" not in codes(check(READS_ONE, ["MP-1"]))


# ----------------------------------- param_key_not_declared
def test_a_key_whose_clause_is_not_declared_is_refused_with_the_clause_id():
    source = wrap('return {"v": str(Fraction(params["MP-2"]))}')
    report = check(source, ["MP-1"])
    bad = [f for f in report.findings if f.code == "param_key_not_declared"]
    assert [f.clause_ids for f in bad] == [("MP-2",)]
    assert "MP-2" in bad[0].detail


def test_a_field_key_of_an_undeclared_clause_is_refused():
    source = wrap('return {"v": str(int(params["TR-2.move_threshold"]))}')
    report = check(source, ["TR-1"])
    assert [f.clause_ids for f in report.findings if f.code == "param_key_not_declared"] == [
        ("TR-2",)
    ]


def test_an_empty_declared_list_refuses_every_key():
    assert "param_key_not_declared" in codes(check(READS_ONE, []))


@pytest.mark.parametrize(
    "key_expression",
    [
        '"MP-" + "1"',  # a computed key
        "name",  # a variable
        "0",  # a number
        '"MP-1"[0:]',  # a slice of text
        '("MP-1", "MP-2")',  # a tuple
        'str("MP-1")',  # a call
        '"MP-1" if True else "MP-2"',  # a choice
    ],
)
def test_a_key_that_is_not_plain_written_text_is_refused(key_expression):
    source = wrap(f'name = "MP-1"\nreturn {{"v": params[{key_expression}]}}')
    report = check(source, ["MP-1"])
    assert "param_key_not_declared" in codes(report)
    assert any("not plain text" in f.detail for f in report.findings)


def test_a_slice_of_the_values_is_refused():
    source = wrap('return {"v": params["MP-1":"MP-2"]}')
    assert "param_key_not_declared" in codes(check(source, ["MP-1"]))


@pytest.mark.parametrize(
    "body",
    [
        "return params",
        'return {"v": params.get("MP-1")}',
        'return {"v": list(params.items())}',
        'return {"v": len(params)}',
        'return {"v": [k for k in params]}',
        "everything = params\nreturn {}",
        'return {"v": dict(params)}',
        'return {"v": "MP-1" in params}',
        'params = records\nreturn {"v": params["MP-1"]}',
    ],
)
def test_the_values_parameter_used_in_any_other_way_than_a_key_read_is_refused(body):
    report = check(wrap(body), ["MP-1"])
    assert "param_key_not_declared" in codes(report), body


def test_writing_to_the_values_is_refused():
    source = wrap('params["MP-1"] = "5"\nreturn {"v": params["MP-1"]}')
    report = check(source, ["MP-1"])
    assert any("written" in f.detail for f in report.findings)
    assert "param_key_not_declared" in codes(report)


def test_a_nested_function_reading_the_values_is_found_too():
    source = wrap('return {"v": [params["MP-9"] for r in records]}')
    assert "param_key_not_declared" in codes(check(source, ["MP-1"]))


# ----------------------------------- param_unused
def test_a_declared_clause_that_is_never_read_is_refused_with_its_clause_id():
    report = check(READS_ONE, ["MP-1", "MP-2"])
    unused = [f for f in report.findings if f.code == "param_unused"]
    assert [f.clause_ids for f in unused] == [("MP-2",)]
    assert "MP-2" in unused[0].detail
    assert "param_key_not_declared" not in codes(report)


def test_a_function_that_reads_nothing_leaves_every_declared_clause_unused():
    source = wrap('return {"v": str(len(records))}')
    report = check(source, ["TR-1", "MP-1"])
    assert [f.clause_ids for f in report.findings if f.code == "param_unused"] == [
        ("MP-1",),
        ("TR-1",),
    ]
    assert all(f.line == 1 for f in report.findings if f.code == "param_unused")


def test_reading_a_field_of_a_clause_counts_as_reading_the_clause():
    source = wrap('return {"v": str(int(params["TR-1.window_terms"]))}')
    assert check(source, ["TR-1"]).passed


def test_an_undeclared_read_and_an_unused_clause_are_both_reported():
    source = wrap('return {"v": str(Fraction(params["MP-2"]))}')
    report = check(source, ["MP-1"])
    assert sorted(set(codes(report))) == ["param_key_not_declared", "param_unused"]


# ----------------------------------- unchanged rules
def test_a_literal_equal_to_a_parameter_value_is_still_a_hardcoded_duplicate():
    source = wrap('return {"v": str(Fraction(params["MP-1"]) + len(records) * 10)}')
    report = check(source, ["MP-1"], values={"MP-1": ["10"]})
    assert "hardcoded_value" in codes(report)
    dup = [f for f in report.findings if f.code == "duplicates_template_value"]
    assert [f.clause_ids for f in dup] == [("MP-1",)]


def test_the_report_is_in_a_fixed_order_and_the_same_every_time():
    source = wrap('return {"v": params["B-1"], "w": params["A-1"]}')
    first = check(source, ["Z-1", "C-1"])
    assert first == check(source, ["C-1", "Z-1"])
    assert [(f.line, f.code) for f in first.findings] == sorted(
        (f.line, f.code) for f in first.findings
    )


# ----------------------------------- the keys read
def test_params_read_gives_the_plain_text_keys_sorted_and_without_repeats():
    source = wrap(
        'x = params["TR-1.window_terms"]\ny = params["MP-1"]\nz = params["MP-1"]\n'
        'return {"v": [x, y, z, params[x]]}'
    )
    assert static_check.params_read(source, RULES) == ("MP-1", "TR-1.window_terms")


def test_params_read_gives_nothing_for_a_source_that_does_not_parse_or_reads_nothing():
    assert static_check.params_read("def measure(records, params:\n", RULES) == ()
    assert static_check.params_read(wrap('return {"v": "x"}'), RULES) == ()


def test_params_read_never_runs_the_source():
    source = wrap('boom = 1 // 0\nreturn {"v": params["MP-1"]}')
    assert static_check.params_read(source, RULES) == ("MP-1",)


# ----------------------------------- from the rules file
def data():
    return yaml.safe_load(FILE.read_text(encoding="utf-8"))


def test_the_rules_file_names_the_values_parameter_and_the_key_separator():
    assert RULES.params_name in RULES.entry_parameters
    assert RULES.key_separator.strip() and len(RULES.key_separator) == 1
    sent = RULES.to_dict()
    assert sent["params_parameter"] == RULES.params_name
    assert sent["params_key_separator"] == RULES.key_separator
    assert Rules.from_dict(sent) == RULES


def test_another_separator_in_the_rules_file_changes_what_is_a_clause():
    changed = data()
    changed["params_key_separator"] = ":"
    rules = Rules.from_dict(changed)
    source = wrap('return {"v": str(int(params["TR-1:window_terms"]))}')
    assert check(source, ["TR-1"], rules=rules).passed
    assert "param_key_not_declared" in codes(
        check(wrap('return {"v": str(int(params["TR-1.window_terms"]))}'), ["TR-1"], rules=rules)
    )


def test_another_parameter_name_in_the_rules_file_is_the_one_that_is_watched():
    changed = data()
    changed["entry_parameters"] = ["records", "values"]
    changed["params_parameter"] = "values"
    rules = Rules.from_dict(changed)
    reads = wrap('return {"v": str(Fraction(values["MP-1"]))}', parameters="records, values")
    assert check(reads, ["MP-1"], rules=rules).passed
    assert static_check.params_read(reads, rules) == ("MP-1",)
    bare = wrap("return values", parameters="records, values")
    assert "param_key_not_declared" in codes(check(bare, ["MP-1"], rules=rules))
