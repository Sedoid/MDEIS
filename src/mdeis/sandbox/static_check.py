"""The static check of a generated function. Standard library only: no model, no network, no
database.

Input: the source text, the output kind the registry holds for the version, the sandbox rules (read
from `templates/sandbox-v1.yaml` by `config.py`) and, for the duplicate check, the values the
school's template holds, by clause ID. Output: a `Report`, with one `Finding` for each rule the
source breaks. The same input always gives the same report, in the same order.

An AST allow-list is not a security boundary on its own. The container is. This check is the first
line: it keeps honest code honest, it is what the tests for E-33 and E-34 hold to, and it makes sure
that a number a school chose is read from the template and never written into code.

What it holds the source to (every list and every limit is in the rules file, none is written here):
  * only the syntax on the allowed list, no decorators, no class, no loop that is not a for loop;
  * imports only from the listed modules, and only the listed members, with no `as`;
  * only the listed builtins, and no name that starts with the double underscore prefix;
  * no float, written or called for, no private or frame attribute, no format string method;
  * no whole number outside the allowed few, and no string that reads as a number. A value that is
    in the template is also reported with its clause ID. The check reports it and does not guess
    which clause the author meant;
  * one entry function with the fixed parameters, and one module constant that declares the output
    kind and equals the kind the registry holds. Without it the function is rejected;
  * when the clause IDs the version declares are given: the school's values are read only by a key
    written as plain text and naming a declared clause (`param_key_not_declared`), the values
    parameter is used in no other way, and every declared clause is read (`param_unused`). When they
    are not given (the runner, which does not know them), these two checks are not made.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fractions import Fraction

from .rules import Rules


@dataclass(frozen=True)
class Finding:
    code: str  # which rule the source broke
    line: int  # the line of the source (the first line when the rule is about the whole source)
    detail: str  # what was found, in plain words
    clause_ids: tuple[str, ...] = ()  # the template clauses a duplicated value belongs to

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "line": self.line,
            "detail": self.detail,
            "clause_ids": list(self.clause_ids),
        }


@dataclass(frozen=True)
class Report:
    rules_version: str
    findings: tuple[Finding, ...]

    @property
    def passed(self) -> bool:
        return not self.findings

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "rules_version": self.rules_version,
            "findings": [f.to_dict() for f in self.findings],
        }


def check_source(
    source: str,
    *,
    output_kind: str | None,
    rules: Rules,
    template_values: Mapping[str, Iterable[str]] | None = None,
    declared_clause_ids: Iterable[str] | None = None,
) -> Report:
    """Check one generated function against the rules. Never runs the source.

    `declared_clause_ids` are the clauses the registry version says it reads (`clause_ids`). Give
    them to make the two value-reading checks; leave them out and those checks are skipped."""
    if len(source) > rules.source_max_chars:
        size = f"the source has {len(source)} characters; the rules allow {rules.source_max_chars}"
        return _report(rules, [Finding("source_too_large", 1, size)])
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as error:
        line = getattr(error, "lineno", None) or 1
        return _report(rules, [Finding("syntax_error", line, "the source does not parse")])
    found: list[Finding] = []
    nodes = _nodes(tree)
    scope = _Scope(rules, *_names(nodes, rules), _template(template_values))
    for node, line in nodes:
        _inspect(node, line, scope, found)
    _entry(tree, rules, found)
    _declared_kind(tree, output_kind, rules, found)
    if declared_clause_ids is not None:
        _values_read(nodes, rules, frozenset(declared_clause_ids), found)
    return _report(rules, found)


def params_read(source: str, rules: Rules) -> tuple[str, ...]:
    """The keys the source reads from the values parameter, as plain text, sorted and without
    repeats. Reads the source and never runs it. A source that does not parse reads none. The build
    keeps this list with the version; the wrapper builds exactly these values for a run."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return ()
    keys = {key for key, _, _ in _reads(_nodes(tree), rules) if key is not None}
    return tuple(sorted(keys))


def _report(rules: Rules, found: list[Finding]) -> Report:
    ordered = sorted(set(found), key=lambda f: (f.line, f.code, f.detail, f.clause_ids))
    return Report(rules_version=rules.version, findings=tuple(ordered))


def _add(found: list[Finding], code: str, line: int, detail: str, clauses=()) -> None:
    found.append(Finding(code, line, detail, tuple(clauses)))


# --- walking the tree --------------------------------------------------------------------------
@dataclass(frozen=True)
class _Scope:
    """What the walk needs to know about the whole source."""

    rules: Rules
    modules: frozenset  # modules imported with `import x`, and allowed
    bound: frozenset  # every name the source binds itself
    bases: frozenset  # ids of the Name nodes that are the left side of an attribute
    template: dict  # template value (an exact number) -> the clause IDs that hold it


def _nodes(tree: ast.AST) -> list[tuple[ast.AST, int]]:
    """Every node with the line it sits on. A node with no line of its own (an operator, a
    context) takes its parent's. Walks with a stack, so a deeply nested source cannot overflow."""
    out: list[tuple[ast.AST, int]] = []
    stack: list[tuple[ast.AST, int]] = [(tree, 1)]
    while stack:
        node, inherited = stack.pop()
        line = getattr(node, "lineno", None) or inherited
        out.append((node, line))
        stack.extend((child, line) for child in ast.iter_child_nodes(node))
    return out


def _names(nodes, rules: Rules):
    """The modules imported by `import x`, every name the source binds itself, and which Name
    nodes are the left side of an attribute (`math` in `math.isqrt`)."""
    modules: set[str] = set()
    bound: set[str] = set()
    bases: set[int] = set()
    for node, _ in nodes:
        if isinstance(node, ast.Import):
            modules.update(a.name for a in node.names if a.name in rules.modules)
        elif isinstance(node, ast.ImportFrom):
            bound.update(a.name for a in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            bases.add(id(node.value))
    return frozenset(modules), frozenset(bound), frozenset(bases)


def _template(values: Mapping[str, Iterable[str]] | None) -> dict[Fraction, tuple[str, ...]]:
    """Template value (as an exact number) -> the clause IDs that hold it."""
    held: dict[Fraction, set[str]] = {}
    for clause, items in (values or {}).items():
        for item in items:
            _, number = _number(str(item))
            if number is not None:
                held.setdefault(number, set()).add(clause)
    return {number: tuple(sorted(ids)) for number, ids in held.items()}


def _number(text: str) -> tuple[bool, Fraction | None]:
    """(does the text read as a number, its exact value if it can be had safely).

    A fraction such as 3/4 and a plain decimal give an exact value. An exponent (1e5), nan and inf
    read as numbers too, but are never expanded: a text such as 1e999999999 must not be able to
    stall the check.
    """
    text = text.strip()
    if "/" in text:
        try:
            return True, Fraction(text)
        except (ValueError, ZeroDivisionError):
            return False, None
    try:
        float(text)
    except ValueError:
        return False, None
    if "e" in text.lower() or "n" in text.lower():
        return True, None
    return True, Fraction(text)


# --- one node ----------------------------------------------------------------------------------
def _inspect(node, line: int, scope: _Scope, found: list[Finding]) -> None:
    rules = scope.rules
    kind = type(node).__name__
    if kind not in rules.node_types:
        _add(found, "node_not_allowed", line, f"{kind} is not allowed in a generated function")
    if isinstance(node, ast.FunctionDef) and node.decorator_list:
        _add(found, "decorator_not_allowed", line, f"the function {node.name} has a decorator")
    if isinstance(node, ast.Import):
        _import(node, line, rules, found)
    elif isinstance(node, ast.ImportFrom):
        _import_from(node, line, rules, found)
    elif isinstance(node, ast.Name):
        _name(node, line, scope, found)
    elif isinstance(node, ast.Attribute):
        _attribute(node, line, scope, found)
    elif isinstance(node, ast.Constant):
        _constant(node.value, line, scope, found)


def _under_another_name(alias: ast.alias, line: int, found: list[Finding]) -> None:
    if alias.asname:
        _add(found, "alias_not_allowed", line, f"{alias.name} is imported under another name")


def _import(node: ast.Import, line: int, rules: Rules, found: list[Finding]) -> None:
    for alias in node.names:
        _under_another_name(alias, line, found)
        if alias.name not in rules.modules:
            _add(found, "import_not_allowed", line, f"the module {alias.name} cannot be imported")


def _import_from(node: ast.ImportFrom, line: int, rules: Rules, found: list[Finding]) -> None:
    module = node.module or ""
    if node.level or module not in rules.modules:
        # No dot is written here: the key separator is a rule value, and a test keeps rule values
        # out of the Python. The message says "relative" in words instead.
        shown = f"a relative import (level {node.level}) of {module}" if node.level else module
        _add(found, "import_not_allowed", line, f"the module {shown} cannot be imported")
        return
    for alias in node.names:
        if not alias.name.isidentifier():
            # Only a star import has a name that is not an identifier. It takes every member, so
            # it is refused as a form, not judged as one member of the list.
            detail = f"a star import from {module} takes members the rules do not list"
            _add(found, "import_not_allowed", line, detail)
            continue
        _under_another_name(alias, line, found)
        if alias.name not in rules.modules[module]:
            detail = f"{alias.name} from {module} cannot be imported"
            _add(found, "import_member_not_allowed", line, detail)


def _name(node: ast.Name, line: int, scope: _Scope, found: list[Finding]) -> None:
    name, rules = node.id, scope.rules
    if name.startswith(rules.name_prefix):
        _add(found, "dunder_name", line, f"the name {name} reaches into the interpreter")
    elif name in rules.float_names:
        _add(found, "float_not_allowed", line, f"{name} makes a number that is not exact")
    elif not isinstance(node.ctx, ast.Load):
        return
    elif name in scope.modules:
        if id(node) not in scope.bases:
            _add(found, "module_used_bare", line, f"the module {name} is used without a member")
    elif name not in scope.bound and name not in rules.builtins:
        _add(found, "name_not_allowed", line, f"the name {name} is not defined or allowed")


def _attribute(node: ast.Attribute, line: int, scope: _Scope, found: list[Finding]) -> None:
    attribute, rules = node.attr, scope.rules
    if attribute.startswith(rules.attribute_prefix):
        _add(found, "private_attribute", line, f"the attribute {attribute} is private")
    elif attribute in rules.forbidden_attributes:
        _add(found, "attribute_not_allowed", line, f"the attribute {attribute} is not allowed")
    elif isinstance(node.value, ast.Name) and node.value.id in scope.modules:
        if attribute not in rules.modules[node.value.id]:
            detail = f"{attribute} of {node.value.id} cannot be used"
            _add(found, "module_member_not_allowed", line, detail)


def _constant(value, line: int, scope: _Scope, found: list[Finding]) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, complex):
        _add(found, "float_literal", line, "a complex number is written in the source")
    elif isinstance(value, float):
        _add(found, "float_literal", line, f"the number {value!r} is written as a float")
        _duplicate(_number(repr(value))[1], repr(value), line, scope, found)
    elif isinstance(value, int):
        if value not in scope.rules.literals:
            _add(found, "hardcoded_value", line, f"the number {value} is written in the source")
            _duplicate(Fraction(value), str(value), line, scope, found)
    elif isinstance(value, str):
        reads_as_number, number = _number(value)
        if reads_as_number:
            _add(found, "hardcoded_value", line, f"the text {value!r} reads as a number")
            _duplicate(number, value, line, scope, found)
    else:
        kind = type(value).__name__
        _add(found, "literal_not_allowed", line, f"a {kind} is written in the source")


def _duplicate(number, shown: str, line: int, scope: _Scope, found: list[Finding]) -> None:
    clauses = scope.template.get(number) if number is not None else None
    if clauses:
        detail = f"{shown} is a value the template holds: read it from the template"
        _add(found, "duplicates_template_value", line, detail, clauses)


# --- the school's values ----------------------------------------------------------------------
def _reads(nodes, rules: Rules):
    """(key, line, problem) for each use of the values parameter. `key` is the plain text key of a
    subscript read, or None; `problem` is None for a good read, else what is wrong with the use."""
    based: set[int] = set()
    for node, _ in nodes:
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == rules.params_name
        ):
            based.add(id(node.value))
    for node, line in nodes:
        if isinstance(node, ast.Subscript) and id(node.value) in based:
            key = node.slice
            if not isinstance(node.ctx, ast.Load):
                yield None, line, "a value is written to the values parameter"
            elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                yield key.value, line, None
            else:
                yield None, line, "the key is not plain text written in the source"
        elif (
            isinstance(node, ast.Name)
            and node.id == rules.params_name
            and id(node) not in based
        ):
            yield None, line, "the values parameter is used without a key"


def _values_read(nodes, rules: Rules, declared: frozenset, found: list[Finding]) -> None:
    read: set[str] = set()
    for key, line, problem in _reads(nodes, rules):
        if problem is not None:
            _add(found, "param_key_not_declared", line, problem)
            continue
        clause = key.partition(rules.key_separator)[0]
        if clause in declared:
            read.add(clause)
        else:
            detail = f"the key {key!r} names a clause the version does not declare"
            _add(found, "param_key_not_declared", line, detail, (clause,) if clause else ())
    for clause in sorted(declared - read):
        detail = f"the version declares {clause} and the source never reads it"
        _add(found, "param_unused", 1, detail, (clause,))


# --- the shape of the whole source -------------------------------------------------------------
def _entry(tree: ast.Module, rules: Rules, found: list[Finding]) -> None:
    name = rules.entry_function
    entries = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
    if not entries:
        _add(found, "entry_missing", 1, f"there is no function {name}")
        return
    function = entries[0]
    args = function.args
    given = tuple(a.arg for a in [*args.posonlyargs, *args.args])
    extras = args.vararg or args.kwarg or args.kwonlyargs or args.defaults
    if given != rules.entry_parameters or extras:
        wanted = ", ".join(rules.entry_parameters)
        _add(found, "entry_signature", function.lineno, f"{name} takes exactly ({wanted})")


def _declared_kind(tree: ast.Module, output_kind, rules: Rules, found: list[Finding]) -> None:
    constant = rules.output_kind_constant
    valid = isinstance(output_kind, str) and output_kind in rules.output_kinds
    if not valid:
        detail = f"the output kind {output_kind!r} is not one of the kinds"
        _add(found, "output_kind_invalid", 1, detail)
    declared = [
        n
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == constant for t in n.targets)
    ]
    if not declared:
        _add(found, "output_kind_missing", 1, f"the source does not declare {constant}")
        return
    if len(declared) > 1:
        _add(found, "output_kind_duplicate", declared[1].lineno, f"{constant} is declared twice")
        return
    value, line = declared[0].value, declared[0].lineno
    if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
        _add(found, "output_kind_missing", line, f"{constant} must be a plain text value")
    elif valid and value.value != output_kind:
        detail = f"the source declares {value.value!r} and the registry holds {output_kind!r}"
        _add(found, "output_kind_mismatch", line, detail)
