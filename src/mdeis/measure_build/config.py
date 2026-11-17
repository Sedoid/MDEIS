"""Load the build rules from `templates/measure-build-v1.yaml`.

Every list, word, pattern and limit the build uses is in that file. The lookup follows the gate's:
MEASURE_BUILD_FILE if set, else the templates folder of the repo, else /templates.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

BUILD_ENV = "MEASURE_BUILD_FILE"
BUILD_NAME = "measure-build-v1.yaml"
FOLDER_NAME = "templates"
CONTAINER_FOLDER = "/templates"


class BuildRulesError(Exception):
    """The build rules are missing, not the right shape, or not usable."""


@dataclass(frozen=True)
class Home:
    setting: str
    section: str  # the template section that holds the field (for example `trend`)
    field: str  # the field of a rule in that section (a setting name from the build rules file)
    unit: str
    names: tuple[str, ...]


@dataclass(frozen=True)
class BuildRules:
    version: str
    id_pattern: str
    version_pattern: str
    reference_words: tuple[str, ...]
    homes: tuple[Home, ...]
    fixed_tools: dict
    repair_rounds: int
    max_rules_per_section: int
    quote_min_chars: int
    min_document_examples: int
    output_kinds: tuple[str, ...]
    output_key_pattern: str
    record_fields: tuple[str, ...]

    def home_for(self, *names: str) -> Home | None:
        """The home whose setting or alias is one of `names` (lower case, spaces as underscores)."""
        wanted = {_slug(n) for n in names if isinstance(n, str) and n.strip()}
        for home in self.homes:
            if wanted & {home.setting, *home.names}:
                return home
        return None

    @classmethod
    def from_dict(cls, data: Any) -> BuildRules:
        if not isinstance(data, dict):
            raise BuildRulesError("the build rules are not a mapping")
        document = _mapping(data, "document")
        output = _mapping(data, "output")
        homes = data.get("homes")
        if not isinstance(homes, list) or not homes:
            raise BuildRulesError("`homes` is missing or is not a list")
        parsed = []
        for entry in homes:
            if not isinstance(entry, dict):
                raise BuildRulesError("each entry of `homes` must be a mapping")
            setting = _text(entry, "setting", "a home")
            where = f"home {setting}"
            names = _words(entry, "names", where)
            parsed.append(
                Home(
                    _slug(setting),
                    _text(entry, "section", where),
                    _text(entry, "field", where),
                    _text(entry, "unit", where),
                    tuple(_slug(n) for n in names),
                )
            )
        if len({h.setting for h in parsed}) != len(parsed):
            raise BuildRulesError("two homes name the same setting")
        tools = data.get("fixed_tools")
        if not isinstance(tools, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and v for k, v in tools.items()
        ):
            raise BuildRulesError("`fixed_tools` must map an area to a tool name")
        for key in ("id_pattern", "version_pattern"):
            _pattern(document, key, "document")
        _pattern(output, "key_pattern", "output")
        return cls(
            version=_text(data, "build_version", "the file"),
            id_pattern=document["id_pattern"],
            version_pattern=document["version_pattern"],
            reference_words=tuple(_words(data, "reference_words", "the file")),
            homes=tuple(parsed),
            fixed_tools=dict(tools),
            repair_rounds=_count(data, "repair_rounds"),
            max_rules_per_section=_count(data, "max_rules_per_section"),
            quote_min_chars=_count(data, "quote_min_chars"),
            min_document_examples=_count(data, "min_document_examples"),
            output_kinds=tuple(_words(output, "kinds", "output")),
            output_key_pattern=output["key_pattern"],
            record_fields=tuple(_words(data, "record_fields", "the file")),
        )


def _slug(text: str) -> str:
    return "_".join(text.strip().lower().replace("-", " ").split())


def _mapping(data: dict, key: str) -> dict:
    value = data.get(key)
    if not isinstance(value, dict):
        raise BuildRulesError(f"`{key}` is missing or is not a mapping")
    return value


def _text(data: dict, key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BuildRulesError(f"{where}: `{key}` is missing or is not text")
    return value


def _words(data: dict, key: str, where: str) -> list:
    value = data.get(key)
    if not (
        isinstance(value, list)
        and value
        and all(isinstance(w, str) and w.strip() for w in value)
    ):
        raise BuildRulesError(f"{where}: `{key}` is missing or is not a list of words")
    return value


def _count(data: dict, key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise BuildRulesError(f"`{key}` is missing or is not a whole number above zero")
    return value


def _pattern(data: dict, key: str, where: str) -> None:
    value = _text(data, key, where)
    try:
        re.compile(value)
    except re.error as error:
        raise BuildRulesError(f"{where}: `{key}` is not a valid pattern: {error}") from error


def build_rules_path() -> Path:
    named = os.environ.get(BUILD_ENV)
    if named:
        return Path(named)
    for folder in Path(__file__).resolve().parents:
        beside_source = folder / FOLDER_NAME / BUILD_NAME
        if beside_source.is_file():
            return beside_source
    return Path(CONTAINER_FOLDER) / BUILD_NAME


def load_build_rules(path: str | Path | None = None) -> BuildRules:
    """Read and check the file. Raises BuildRulesError, with a plain message, for any fault."""
    where = Path(path) if path else build_rules_path()
    try:
        text = where.read_text(encoding="utf-8")
    except OSError as error:
        raise BuildRulesError(f"cannot read the build rules at {where}: {error}") from error
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise BuildRulesError(f"the build rules at {where} are not valid YAML: {error}") from error
    return BuildRules.from_dict(data)
