"""Criteria template loader.

Reads a YAML criteria template (shape: docs/TEMPLATE.md), checks it, and returns typed models.
Every rule the system applies comes from here. This module holds no threshold, scale, stage
range or wording of its own. It only holds the schema: which sections exist, which clause ID
prefix each section uses, and which kinds of value are allowed.

The only numbers in this file are 0 and 1, used for "not negative", "positive" and "exactly one".

Public API:
    load_template(path) -> Template      read and validate a file
    parse_template(data) -> Template     validate data that is already parsed
    TemplateError                        raised with a plain message for every rejection
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

# Schema details (docs/TEMPLATE.md), not school rules.
# Section key in the YAML -> clause ID area. The order is the order of `Template.clauses()`.
SECTION_AREAS: dict[str, str] = {
    "scales": "SC",
    "coefficients": "WT",
    "areas": "SK",
    "stage_expectations": "ST",
    "levels": "LV",
    "trend": "TR",
    "exceptions": "EX",
    "wording": "WD",
    "group_minimum": "GM",
}

# Scale types the loader understands. Anything else is rejected.
SCALE_TYPES: tuple[str, ...] = ("out_of",)

# Parameters that must be set by exactly one rule of the section (a value the code will read).
REQUIRED_PARAMS: dict[str, tuple[str, ...]] = {
    "trend": (
        "min_scores",
        "min_terms",
        "move_threshold",
        "missing_term",
        "gap_direction_threshold",
    ),
    "exceptions": ("absences",),
    "wording": ("banned",),
    "group_minimum": ("min_group_size",),
}

CLAUSE_ID = re.compile(r"^([A-Z]{2})-([1-9][0-9]*)$")

# strict=True: a quoted number, a decimal where a whole number is needed, or true/false is refused.
Num = Annotated[float, Field(strict=True)]
PosNum = Annotated[float, Field(strict=True, gt=0)]
PosInt = Annotated[int, Field(strict=True, gt=0)]
Name = Annotated[str, Field(min_length=1)]


class TemplateError(ValueError):
    """The template is not acceptable. The message says what is wrong and where."""


class _Model(BaseModel):
    # forbid: a misspelt field is an error, not a silently ignored rule.
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Rule(_Model):
    """Fields every rule has: a clause ID, citable text, and an optional methodology reference."""

    id: str
    text: str
    method_ref: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _text_and_method_ref(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        rid = data.get("id", "(no id)")
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"rule {rid} has no text")
        ref = data.get("method_ref")
        if ref is not None and (not isinstance(ref, str) or not ref.strip()):
            raise ValueError(f"method_ref of {rid} must be a quoted, non-empty string")
        return data


class GradeBand(_Model):
    grade: Name
    min: Num  # lowest annual score in the band


class ScaleRule(_Rule):
    """Either a scale (type and max) or the grade bands. Not both."""

    type: str | None = None
    max: PosNum | None = None
    grade_bands: list[GradeBand] | None = None

    @model_validator(mode="after")
    def _one_kind(self) -> ScaleRule:
        if self.type is not None and self.type not in SCALE_TYPES:
            known = ", ".join(SCALE_TYPES)
            raise ValueError(f"unknown scale type {self.type!r} in {self.id}; known types: {known}")
        is_scale = self.type is not None and self.max is not None
        has_bands = self.grade_bands is not None
        stray = (self.type is not None) != (self.max is not None)
        if is_scale == has_bands or stray:
            raise ValueError(f"scale {self.id} needs either a type with max, or grade_bands")
        return self


class CoefficientRule(_Rule):
    subjects: Annotated[dict[Name, PosInt], Field(min_length=1)]

    @model_validator(mode="before")
    @classmethod
    def _whole_positive(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("subjects"), dict):
            for name, value in data["subjects"].items():
                if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                    raise ValueError(
                        f"coefficient for {name} in {data.get('id')} "
                        f"must be a positive whole number, got {value!r}"
                    )
        return data


class AreaRule(_Rule):
    area: Name
    subjects: Annotated[list[Name], Field(min_length=1)]


class StageRule(_Rule):
    stage: Name
    area: Name
    range: tuple[Num, Num]

    @model_validator(mode="after")
    def _low_below_high(self) -> StageRule:
        if not self.range[0] < self.range[1]:
            raise ValueError(f"range of {self.id} must run from a lower to a higher score")
        return self


class LevelRule(_Rule):
    name: Name
    statement: Name


class TrendRule(_Rule):
    min_scores: PosInt | None = None
    min_terms: PosInt | None = None
    move_threshold: PosNum | None = None
    missing_term: Literal["ask_approver"] | None = None
    gap_direction_threshold: PosNum | None = None


class ExceptionRule(_Rule):
    absences: Literal["context_only"] | None = None


class WordingRule(_Rule):
    banned: Annotated[list[Name], Field(min_length=1)] | None = None


class GroupMinimumRule(_Rule):
    min_group_size: PosInt | None = None


class Institution(_Model):
    name: Name
    code: Name


class Clause(_Model):
    """One citable rule, as the clause index will store it."""

    id: str
    area: str
    text: str
    method_ref: str | None = None


class Template(_Model):
    template_id: Name
    version: int
    language: Name
    institution: Institution
    scales: list[ScaleRule]
    coefficients: list[CoefficientRule]
    foundation_areas: Annotated[list[Name], Field(min_length=1)]
    areas: list[AreaRule]
    stage_expectations: list[StageRule]
    levels: list[LevelRule]
    trend: list[TrendRule]
    exceptions: list[ExceptionRule]
    wording: list[WordingRule]
    group_minimum: list[GroupMinimumRule]

    # -- rejection of a missing area comes first, with a clear message --------------------------

    @model_validator(mode="before")
    @classmethod
    def _required_areas(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            raise ValueError("a template must be a mapping")
        for section, area in SECTION_AREAS.items():
            rules = data.get(section)
            if not isinstance(rules, list) or not rules:
                raise ValueError(
                    f"missing required area {area}: the template needs at least one rule "
                    f"under '{section}'"
                )
        return data

    # -- checks that need the whole template ------------------------------------------------------

    @model_validator(mode="after")
    def _whole_template(self) -> Template:
        self._check_clause_ids()
        max_score = self._check_scales()
        self._check_coefficients()
        self._check_areas()
        self._check_stages(max_score)
        self._check_required_params()
        return self

    def _section(self, section: str) -> list[Any]:
        return getattr(self, section)

    def _check_clause_ids(self) -> None:
        seen: set[str] = set()
        for section, area in SECTION_AREAS.items():
            for rule in self._section(section):
                match = CLAUSE_ID.match(rule.id)
                if not match:
                    raise ValueError(
                        f"clause ID {rule.id!r} is not of the form AREA-n (for example {area}-1)"
                    )
                if match.group(1) != area:
                    raise ValueError(f"clause ID {rule.id} in '{section}' must start with {area}-")
                if rule.id in seen:
                    raise ValueError(f"duplicate clause ID {rule.id}")
                seen.add(rule.id)

    def _check_scales(self) -> float:
        scales = [r for r in self.scales if r.type is not None]
        banded = [r for r in self.scales if r.grade_bands is not None]
        if len(scales) != 1:
            raise ValueError("exactly one scale rule (type with max) is required under 'scales'")
        if len(banded) != 1:
            raise ValueError("exactly one grade_bands rule is required under 'scales'")
        top = scales[0].max
        assert top is not None  # ScaleRule guarantees a max when there is a type
        bands = banded[0].grade_bands or []
        bid = banded[0].id
        grades: set[str] = set()
        starts: set[float] = set()
        for band in bands:
            if band.grade in grades:
                raise ValueError(f"grade {band.grade} appears twice in {bid}")
            grades.add(band.grade)
        for band in bands:
            if band.min < 0 or band.min > top:
                raise ValueError(
                    f"grade bands in {bid} are not contiguous: band {band.grade} starts at "
                    f"{band.min:g}, outside 0 to {top:g}"
                )
            if band.min in starts:
                raise ValueError(
                    f"grade bands in {bid} are not contiguous: two bands start at {band.min:g}"
                )
            starts.add(band.min)
        lowest = min(starts)
        if lowest != 0:
            raise ValueError(
                f"grade bands in {bid} are not contiguous from 0 to {top:g}: "
                f"the lowest band starts at {lowest:g}"
            )
        return top

    def _check_coefficients(self) -> None:
        owner: dict[str, str] = {}
        for rule in self.coefficients:
            for subject in rule.subjects:
                if subject in owner:
                    raise ValueError(
                        f"subject {subject} has a coefficient in both {owner[subject]} "
                        f"and {rule.id}"
                    )
                owner[subject] = rule.id

    def _check_areas(self) -> None:
        with_coefficient = {s for r in self.coefficients for s in r.subjects}
        area_of: dict[str, str] = {}
        defined: set[str] = set()
        for rule in self.areas:
            if rule.area not in self.foundation_areas:
                raise ValueError(
                    f"{rule.id} names area {rule.area!r}, which is not in foundation_areas"
                )
            if rule.area in defined:
                raise ValueError(f"area {rule.area!r} has more than one SK rule")
            defined.add(rule.area)
            for subject in rule.subjects:
                if subject in area_of:
                    raise ValueError(
                        f"subject {subject} is in two areas: {area_of[subject]} and {rule.area}"
                    )
                area_of[subject] = rule.area
                if subject not in with_coefficient:
                    raise ValueError(f"subject {subject} in {rule.id} has no coefficient")
        for name in self.foundation_areas:
            if name not in defined:
                raise ValueError(f"foundation area {name!r} has no SK rule")

    def _check_stages(self, max_score: float) -> None:
        seen: dict[tuple[str, str], str] = {}
        for rule in self.stage_expectations:
            if rule.area not in self.foundation_areas:
                raise ValueError(
                    f"{rule.id} names area {rule.area!r}, which is not a foundation area"
                )
            low, high = rule.range
            if low < 0 or high > max_score:
                raise ValueError(f"range of {rule.id} must lie between 0 and {max_score:g}")
            key = (rule.stage, rule.area)
            if key in seen:
                raise ValueError(
                    f"{seen[key]} and {rule.id} both set the range for {rule.stage} {rule.area}"
                )
            seen[key] = rule.id

    def _check_required_params(self) -> None:
        for section, params in REQUIRED_PARAMS.items():
            for param in params:
                setters = [r.id for r in self._section(section) if getattr(r, param) is not None]
                if not setters:
                    raise ValueError(f"no {section} rule sets {param}")
                if len(setters) != 1:
                    raise ValueError(f"{param} is set by both {setters[0]} and {setters[1]}")

    # -- reading the template ---------------------------------------------------------------------

    def clauses(self) -> list[Clause]:
        """Every rule as a citable clause, in section order then file order."""
        return [
            Clause(id=r.id, area=area, text=r.text, method_ref=r.method_ref)
            for section, area in SECTION_AREAS.items()
            for r in self._section(section)
        ]

    def clause(self, clause_id: str) -> Clause:
        """One clause by ID. Raises KeyError if the template has no such rule."""
        for c in self.clauses():
            if c.id == clause_id:
                return c
        raise KeyError(clause_id)


def _describe(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        message = err["msg"].removeprefix("Value error, ")
        where = ".".join(str(p) for p in err["loc"])
        parts.append(f"{where}: {message}" if where else message)
    return "invalid template: " + "; ".join(parts)


def parse_template(data: Any) -> Template:
    """Validate template data that is already parsed. Raises TemplateError."""
    if not isinstance(data, dict):
        raise TemplateError("a template must be a mapping at the top level")
    try:
        return Template.model_validate(data)
    except ValidationError as exc:
        raise TemplateError(_describe(exc)) from exc


def load_template(path: str | Path) -> Template:
    """Read a YAML template file and validate it. Raises TemplateError."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TemplateError(f"cannot read template {path}: {exc.strerror or exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise TemplateError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise TemplateError(f"{path}: a template must be a mapping at the top level")
    return parse_template(data)
