"""The sandbox rules as a checked, read-only object. Standard library only.

The rules are data: `templates/sandbox-v1.yaml` is read by `config.py` and handed to
`Rules.from_dict`. This file holds the shape of the data and the checks that it is complete. It
holds no list, name or limit of its own, so a rule changes by editing the file and never the code.
The runner (slice 2) can build the same object from a dict sent to it, which is why this file
reads no file and imports nothing outside the standard library.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

SINGLE_VALUE = "text"
WORD_LIST = "words"
WORD_MAP = "map"
WHOLE_NUMBERS = "ints"
COUNT = "count"


class SandboxRulesError(Exception):
    """The rules are missing, not the right shape, or name something that does not exist."""


# The key each part of the file must carry, and what kind of value it holds.
SHAPE = {
    "sandbox_version": SINGLE_VALUE,
    "entry_function": SINGLE_VALUE,
    "entry_parameters": WORD_LIST,
    "output_kind_constant": SINGLE_VALUE,
    "output_kinds": WORD_LIST,
    "allowed_modules": WORD_MAP,
    "allowed_builtins": WORD_LIST,
    "float_names": WORD_LIST,
    "forbidden_name_prefix": SINGLE_VALUE,
    "forbidden_attribute_prefix": SINGLE_VALUE,
    "forbidden_attributes": WORD_LIST,
    "allowed_node_types": WORD_LIST,
    "allowed_literals": WHOLE_NUMBERS,
    "source_max_chars": COUNT,
}
LIMIT_KEYS = ("wall_s", "cpu_s", "memory_mb", "input_kb", "output_kb")


def _words(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(w, str) and w.strip() for w in value)
    )


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _kind_ok(value: Any, kind: str) -> bool:
    if kind == SINGLE_VALUE:
        return isinstance(value, str) and bool(value.strip())
    if kind == WORD_LIST:
        return _words(value)
    if kind == WORD_MAP:
        return (
            isinstance(value, dict)
            and bool(value)
            and all(isinstance(k, str) and k.strip() and _words(v) for k, v in value.items())
        )
    if kind == WHOLE_NUMBERS:
        return (
            isinstance(value, list)
            and bool(value)
            and all(isinstance(n, int) and not isinstance(n, bool) for n in value)
        )
    return _count(value)


@dataclass(frozen=True)
class Rules:
    version: str
    entry_function: str
    entry_parameters: tuple[str, ...]
    output_kind_constant: str
    output_kinds: tuple[str, ...]
    modules: dict  # module name -> tuple of the members that may be used
    builtins: frozenset[str]
    float_names: frozenset[str]
    name_prefix: str
    attribute_prefix: str
    forbidden_attributes: frozenset[str]
    node_types: frozenset[str]
    literals: frozenset[int]
    source_max_chars: int
    limits: dict  # wall_s, cpu_s, memory_mb, input_kb, output_kb

    @classmethod
    def from_dict(cls, data: Any) -> Rules:
        if not isinstance(data, dict):
            raise SandboxRulesError("the sandbox rules are not a mapping")
        for key, kind in SHAPE.items():
            if not _kind_ok(data.get(key), kind):
                raise SandboxRulesError(f"`{key}` is missing or is not a {kind}")
        limits = data.get("limits")
        if not isinstance(limits, dict):
            raise SandboxRulesError("`limits` is missing or is not a mapping")
        for key in LIMIT_KEYS:
            if not _count(limits.get(key)):
                raise SandboxRulesError(
                    f"`limits.{key}` is missing or is not a whole number above zero"
                )
        unknown = [n for n in data["allowed_node_types"] if not hasattr(ast, n)]
        if unknown:
            raise SandboxRulesError(
                f"`allowed_node_types` names {unknown}, which Python's ast does not have"
            )
        return cls(
            version=data["sandbox_version"],
            entry_function=data["entry_function"],
            entry_parameters=tuple(data["entry_parameters"]),
            output_kind_constant=data["output_kind_constant"],
            output_kinds=tuple(data["output_kinds"]),
            modules={m: tuple(members) for m, members in data["allowed_modules"].items()},
            builtins=frozenset(data["allowed_builtins"]),
            float_names=frozenset(data["float_names"]),
            name_prefix=data["forbidden_name_prefix"],
            attribute_prefix=data["forbidden_attribute_prefix"],
            forbidden_attributes=frozenset(data["forbidden_attributes"]),
            node_types=frozenset(data["allowed_node_types"]),
            literals=frozenset(data["allowed_literals"]),
            source_max_chars=data["source_max_chars"],
            limits={k: limits[k] for k in LIMIT_KEYS},
        )
