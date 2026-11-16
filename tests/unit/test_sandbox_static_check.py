"""The static check of a generated function (T-052 slice 1). Standard library only, no database.

Each rule has a case that is refused and, where it matters, a case that passes. The rules are read
from templates/sandbox-v1.yaml. These tests say what a finding is called; they do not copy the lists
from the file, so a rule moving in the file does not make a test lie.
"""

import ast
import json
import textwrap
from pathlib import Path

import pytest

from mdeis.sandbox import static_check
from mdeis.sandbox.config import load_rules

ROOT = Path(__file__).resolve().parents[2]
RULES = load_rules()

GOOD = '''
from fractions import Fraction
import math

OUTPUT_KIND = "number"


def measure(records, params):
    scores = [Fraction(r["score"]) for r in records]
    if len(scores) == 0:
        return {"value": str(Fraction(0))}
    total = sum(scores)
    return {"value": str(Fraction(total, len(scores))), "root": math.isqrt(len(scores))}
'''


def wrap(body, imports="", kind="number"):
    """A whole function around `body`, with the declared kind and the fixed entry shape."""
    inner = textwrap.indent(textwrap.dedent(body).strip("\n"), "    ")
    return f'{imports}\nOUTPUT_KIND = "{kind}"\n\n\ndef measure(records, params):\n{inner}\n'


def check(source, kind="number", values=None):
    return static_check.check_source(source, output_kind=kind, rules=RULES, template_values=values)


def codes(report):
    return [f.code for f in report.findings]


# --------------------------------------------------------------------------- what passes
def test_an_allowed_function_using_fraction_and_math_isqrt_passes():
    report = check(GOOD)
    assert report.passed, report.findings
    assert report.findings == ()


def test_a_function_can_use_zero_and_one_and_a_template_value_equal_to_one_does_not_change_that():
    # 0 and 1 are structure. They are never reported as a copy of a template value.
    report = check(GOOD, values={"WT-1": ["1"], "SC-9": ["0"]})
    assert report.passed, report.findings


def test_a_module_can_be_used_through_its_allowed_members_and_division_goes_through_fraction():
    source = wrap(
        'return {"value": str(Fraction(len(records), 1)), "n": math.gcd(len(records), 1)}',
        imports="import math\nfrom fractions import Fraction",
    )
    assert check(source).passed


# --------------------------------------------------------------------------- numbers (E-33)
@pytest.mark.parametrize(
    "body",
    [
        'return {"value": str(len(records) * 12)}',  # a whole number that acts as a rule
        'x = 100\nreturn {"value": str(x)}',
        'return {"value": str(records[2])}',  # even an index is refused: only the listed ones pass
    ],
)
def test_a_whole_number_literal_outside_the_allowed_set_is_a_hardcoded_value(body):
    assert "hardcoded_value" in codes(check(wrap(body)))


@pytest.mark.parametrize("text", ["3/4", "10", "0.6", "12.5", "1e3"])
def test_a_string_that_reads_as_a_number_is_a_hardcoded_value(text):
    source = wrap(
        f'return {{"value": str(Fraction("{text}"))}}', imports="from fractions import Fraction"
    )
    assert "hardcoded_value" in codes(check(source))


@pytest.mark.parametrize("text", ["1e999999999", "nan", "inf", "-Infinity", "1_000"])
def test_a_text_that_reads_as_a_number_is_refused_without_being_expanded(text):
    # An exponent must not be expanded (1e999999999 would stall the check), so it is reported only.
    source = wrap(
        f'return {{"value": str(Fraction("{text}"))}}', imports="from fractions import Fraction"
    )
    report = check(source, values={"SC-3": ["10"]})
    assert "hardcoded_value" in codes(report)
    assert "duplicates_template_value" not in codes(report)


def test_even_a_numeric_string_equal_to_an_allowed_literal_is_refused():
    source = wrap('return {"value": str(Fraction("1"))}', imports="from fractions import Fraction")
    assert "hardcoded_value" in codes(check(source))


def test_a_clause_id_or_an_ordinary_string_is_not_a_number():
    source = wrap(
        'return {"value": str(Fraction(params["SC-3"])), "note": "a statement"}',
        imports="from fractions import Fraction",
    )
    assert check(source).passed


def test_a_literal_equal_to_a_template_value_is_reported_with_the_clause_id_and_fails():
    source = wrap('return {"value": str(len(records) * 10)}')
    report = check(source, values={"SC-3": ["10"], "TR-2": ["10", "3"]})
    found = [f for f in report.findings if f.code == "duplicates_template_value"]
    assert len(found) == 1
    assert found[0].clause_ids == ("SC-3", "TR-2")
    assert not report.passed


def test_a_numeric_string_equal_to_a_template_value_in_another_form_is_a_duplicate():
    # The template holds 0.6; the source writes 3/5. They are the same exact number.
    source = wrap(
        'return {"value": str(Fraction("3/5"))}', imports="from fractions import Fraction"
    )
    report = check(source, values={"TR-4": ["0.6"]})
    dup = [f for f in report.findings if f.code == "duplicates_template_value"]
    assert [f.clause_ids for f in dup] == [("TR-4",)]


def test_a_literal_that_is_not_in_the_template_is_still_a_hardcoded_value_without_a_duplicate():
    report = check(wrap('return {"value": str(len(records) * 7)}'), values={"SC-3": ["10"]})
    assert "hardcoded_value" in codes(report)
    assert "duplicates_template_value" not in codes(report)


def test_a_float_literal_and_float_are_refused():
    assert "float_literal" in codes(check(wrap('return {"value": 0.5}')))
    assert "float_not_allowed" in codes(check(wrap('return {"value": str(float(len(records)))}')))
    assert "float_literal" in codes(check(wrap('return {"value": 1j}')))


def test_a_float_literal_equal_to_a_template_value_is_also_a_duplicate():
    report = check(wrap('return {"value": 0.6}'), values={"TR-4": ["0.6"]})
    assert "duplicates_template_value" in codes(report)


def test_true_division_and_power_are_not_allowed_syntax():
    divided = wrap('return {"value": str(len(records) / len(records))}')
    powered = wrap('return {"value": str(len(records) ** len(records))}')
    assert "node_not_allowed" in codes(check(divided))
    assert "node_not_allowed" in codes(check(powered))


# --------------------------------------------------------------------------- imports
@pytest.mark.parametrize(
    "imports",
    [
        "import os", "import socket", "import subprocess", "import sys", "import os.path",
        "import json",
    ],
)
def test_an_import_of_a_module_that_is_not_allowed_is_refused(imports):
    assert "import_not_allowed" in codes(check(wrap("return {}", imports=imports)))


def test_from_imports_and_star_and_relative_imports_are_refused():
    assert "import_not_allowed" in codes(check(wrap("return {}", imports="from os import path")))
    assert "import_not_allowed" in codes(check(wrap("return {}", imports="from math import *")))
    assert "import_not_allowed" in codes(check(wrap("return {}", imports="from . import x")))


def test_a_star_import_is_refused_as_a_form_and_the_other_bad_imports_beside_it_still_show():
    star = check(wrap("return {}", imports="from math import *"))
    assert codes(star) == ["import_not_allowed"]
    assert "star import" in star.findings[0].detail
    with_module = check(wrap("return {}", imports="from math import *\nimport os"))
    assert codes(with_module) == ["import_not_allowed", "import_not_allowed"]
    assert any("star import" in f.detail for f in with_module.findings)
    assert any("os" in f.detail for f in with_module.findings)
    with_member = check(wrap("return {}", imports="from math import *\nfrom math import sqrt"))
    assert sorted(codes(with_member)) == ["import_member_not_allowed", "import_not_allowed"]


def test_an_import_with_as_is_refused_so_a_name_says_what_it_is():
    assert "alias_not_allowed" in codes(check(wrap("return {}", imports="import math as m")))
    assert "alias_not_allowed" in codes(
        check(wrap("return {}", imports="from fractions import Fraction as F"))
    )


def test_a_member_that_is_not_listed_is_refused_however_it_is_reached():
    assert "import_member_not_allowed" in codes(
        check(wrap("return {}", imports="from math import sqrt"))
    )
    through_module = wrap('return {"v": str(math.sqrt(len(records)))}', imports="import math")
    assert "module_member_not_allowed" in codes(check(through_module))
    state = wrap("return {}\ndecimal.getcontext()", imports="import decimal")
    assert "module_member_not_allowed" in codes(check(state))


def test_a_module_name_cannot_be_passed_around_bare():
    assert "module_used_bare" in codes(check(wrap("m = math\nreturn {}", imports="import math")))


# --------------------------------------------------------------------------- names and attributes
@pytest.mark.parametrize(
    "body",
    [
        'return open("x")',
        'return eval("records")',
        'exec("x")\nreturn {}',
        'return getattr(records, "x")',
        'setattr(records, "x", records)\nreturn {}',
        'return compile("x", "f", "exec")',
        "return globals()",
        "return locals()",
        "return vars(records)",
        "return type(records)",
        "return input()",
        "breakpoint()\nreturn {}",
        "return round(len(records))",
    ],
)
def test_a_builtin_that_is_not_on_the_list_is_refused(body):
    assert "name_not_allowed" in codes(check(wrap(body)))


def test_dunder_names_and_attributes_are_refused():
    assert "dunder_name" in codes(check(wrap('return __import__("os")')))
    assert "dunder_name" in codes(check(wrap("return __builtins__")))
    assert "private_attribute" in codes(check(wrap("return records.__class__")))
    assert "private_attribute" in codes(check(wrap("return params.__dict__")))
    assert "private_attribute" in codes(check(wrap("return records._x")))


def test_a_format_string_cannot_reach_an_attribute():
    assert "attribute_not_allowed" in codes(check(wrap('return "{0.__class__}".format(records)')))
    assert "attribute_not_allowed" in codes(check(wrap('return "{a}".format_map(params)')))
    assert "node_not_allowed" in codes(check(wrap('return f"{records}"')))


@pytest.mark.parametrize("attribute", ["gi_frame", "f_globals", "mro", "tb_frame", "from_float"])
def test_interpreter_internals_are_refused_by_name(attribute):
    assert "attribute_not_allowed" in codes(check(wrap(f"return records.{attribute}")))


@pytest.mark.parametrize(
    "body, node",
    [
        ("while records:\n    pass\nreturn {}", "While"),
        ("f = lambda x: x\nreturn {}", "Lambda"),
        ("try:\n    pass\nexcept Exception:\n    pass\nreturn {}", "Try"),
        ('raise ValueError("x")', "Raise"),
        ("yield 1", "Yield"),
        ("global x\nreturn {}", "Global"),
        ("assert records\nreturn {}", "Assert"),
        ("del records\nreturn {}", "Delete"),
        ("return [*records]", "Starred"),
    ],
)
def test_syntax_that_is_not_on_the_allowed_list_is_refused(body, node):
    report = check(wrap(body))
    assert any(f.code == "node_not_allowed" and node in f.detail for f in report.findings), report


def test_class_async_and_decorators_are_refused():
    cls = wrap("return {}") + "\nclass X:\n    pass\n"
    assert "node_not_allowed" in codes(check(cls))
    asynchronous = wrap("return {}") + "\nasync def other():\n    pass\n"
    assert "node_not_allowed" in codes(check(asynchronous))
    decorated = wrap("return {}").replace("def measure", "@staticmethod\ndef measure")
    assert "decorator_not_allowed" in codes(check(decorated))


# ------------------------------------------------------------------------- the declared output kind
def test_a_function_with_no_declared_output_kind_is_rejected():
    source = "def measure(records, params):\n    return {}\n"
    assert codes(check(source)) == ["output_kind_missing"]


def test_a_declared_kind_that_is_not_the_registrys_kind_is_rejected():
    assert "output_kind_mismatch" in codes(check(wrap("return {}", kind="table"), kind="number"))


def test_a_registry_kind_that_is_not_one_of_the_kinds_is_rejected():
    assert "output_kind_invalid" in codes(check(wrap("return {}", kind="chart"), kind="chart"))
    assert "output_kind_invalid" in codes(check(wrap("return {}"), kind=None))


def test_two_declarations_are_refused():
    source = wrap("return {}") + 'OUTPUT_KIND = "number"\n'
    assert "output_kind_duplicate" in codes(check(source))


@pytest.mark.parametrize("kind", ["number", "label", "series", "table"])
def test_each_of_the_four_kinds_can_be_declared(kind):
    assert check(wrap("return {}", kind=kind), kind=kind).passed


def test_the_entry_function_must_exist_with_the_fixed_parameters():
    no_entry = 'OUTPUT_KIND = "number"\n\n\ndef other(records, params):\n    return {}\n'
    assert "entry_missing" in codes(check(no_entry))
    wrong = wrap("return {}").replace("(records, params)", "(rows)")
    assert "entry_signature" in codes(check(wrong))
    extra = wrap("return {}").replace("(records, params)", "(records, params, more=0)")
    assert "entry_signature" in codes(check(extra))


# --------------------------------------------------------------------------- the report
def test_a_source_that_does_not_parse_gives_one_finding_and_nothing_else():
    report = check("def measure(records, params:\n    return {}\n")
    assert codes(report) == ["syntax_error"]
    assert report.findings[0].line >= 1


def test_a_source_over_the_size_limit_is_refused_unread():
    report = check("x = 1\n" * (RULES.source_max_chars + 1))
    assert codes(report) == ["source_too_large"]


def test_the_same_source_gives_the_same_findings_in_the_same_order():
    source = wrap(
        'x = 12\ny = 0.5\nimport os\nreturn open("f")',
    )
    first, second = check(source), check(source)
    assert first == second
    assert [(f.line, f.code) for f in first.findings] == sorted(
        (f.line, f.code) for f in first.findings
    )
    assert len(first.findings) >= 4


def test_a_report_is_plain_data_that_can_be_stored_as_json():
    report = check(wrap("return len(records) * 12"), values={"SC-3": ["12"]})
    data = json.loads(json.dumps(report.to_dict()))
    assert data["passed"] is False
    assert data["rules_version"] == RULES.version
    assert {"code", "line", "detail", "clause_ids"} <= set(data["findings"][0])
    assert json.loads(json.dumps(check(GOOD).to_dict()))["passed"] is True


def test_the_report_names_no_label_and_makes_no_suggestion():
    report = check(wrap("return len(records) * 12"))
    for finding in report.findings:
        assert finding.detail == finding.detail.strip()
        for word in ("weak", "remedial", "gifted", "should", "recommend"):
            assert word not in finding.detail.lower()


# --------------------------------------------------------------------------- fixed versus generated
# A generated function can only compute. These are the powers of the fixed tools: the database, the
# network, files, the clock, processes, and the tools themselves. None can be imported or called.
POWERS = {
    "database": ["psycopg", "sqlite3", "psycopg_pool"],
    "network": ["socket", "urllib", "http", "httpx", "requests", "ssl"],
    "files": ["os", "pathlib", "io", "shutil", "tempfile", "open"],
    "clock and chance": ["time", "datetime", "random", "secrets"],
    "processes": ["subprocess", "sys", "multiprocessing", "threading", "ctypes"],
    "tools": [
        "ingest_assessments", "resolve_issue", "get_criterion", "compute_trend", "stage_gap_counts",
        "class_baseline",
        "draft_summary", "check_citations", "make_chart", "release_report", "compute_measure",
        "list_measures",
    ],
}
EVERY_POWER = sorted(name for names in POWERS.values() for name in names)


@pytest.mark.parametrize("name", EVERY_POWER)
def test_no_fixed_power_can_be_imported_or_called(name):
    as_import = wrap("return {}", imports=f"import {name}")
    from_import = wrap("return {}", imports=f"from {name} import anything")
    as_call = wrap(f"return {name}()")
    as_attribute = wrap(f"return {name}.connect()")
    for source in (as_import, from_import, as_call, as_attribute):
        report = check(source)
        assert not report.passed, source
        assert report.findings, source


# ------------------------------------------------------------------------- what the module imports
ALLOWED_IMPORTS = {"__future__", "ast", "collections", "dataclasses", "fractions", "typing"}


@pytest.mark.parametrize("file", ["static_check.py", "rules.py"])
def test_the_check_imports_only_the_standard_library_and_its_own_rules(file):
    tree = ast.parse((ROOT / "src" / "mdeis" / "sandbox" / file).read_text(encoding="utf-8"))
    seen = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            seen |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level == 1:
                assert node.module == "rules", "only the sibling rules file may be imported"
            else:
                assert node.level == 0
                seen.add((node.module or "").split(".")[0])
    assert seen <= ALLOWED_IMPORTS, seen - ALLOWED_IMPORTS
