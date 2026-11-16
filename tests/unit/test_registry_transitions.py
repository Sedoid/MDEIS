"""The legal moves of a measure version (pure, no database); the code and schema agree."""

import itertools
import re

import pytest

from mdeis.db.migrate import migration_files
from mdeis.registry import transitions as t
from mdeis.sandbox.config import load_rules


def sql():
    (path,) = [f for f in migration_files() if f.name.startswith("008_")]
    return re.sub(r"--[^\n]*", "", path.read_text(encoding="utf-8"))


def test_the_status_order_is_the_one_the_owner_set():
    assert t.STATUSES == (
        "proposed", "testing", "awaiting_review", "awaiting_demo", "approved", "rejected",
        "retired",
    )
    assert "awaiting_dev" not in t.STATUSES


LEGAL_BY_HAND = {
    ("proposed", "testing"), ("testing", "awaiting_review"), ("awaiting_review", "awaiting_demo"),
    ("awaiting_demo", "approved"), ("approved", "retired"),
    ("proposed", "rejected"), ("testing", "rejected"), ("awaiting_review", "rejected"),
    ("awaiting_demo", "rejected"),
    ("proposed", "retired"), ("testing", "retired"), ("awaiting_review", "retired"),
    ("awaiting_demo", "retired"),
}


def test_exactly_these_moves_are_legal_and_every_other_pair_is_not():
    assert set(t.LEGAL) == LEGAL_BY_HAND
    for a, b in itertools.product(t.STATUSES, repeat=2):
        assert t.is_legal(a, b) == ((a, b) in LEGAL_BY_HAND), (a, b)


@pytest.mark.parametrize(
    "a, b",
    [
        ("proposed", "awaiting_review"), ("proposed", "awaiting_demo"), ("proposed", "approved"),
        ("testing", "awaiting_demo"), ("testing", "approved"), ("awaiting_review", "approved"),
        ("awaiting_review", "testing"), ("awaiting_demo", "awaiting_review"),
    ],
)
def test_a_status_cannot_skip_a_step_or_go_back(a, b):
    assert not t.is_legal(a, b)


def test_rejected_and_retired_are_the_end_and_approved_can_only_be_retired():
    for end in ("rejected", "retired"):
        assert not any(t.is_legal(end, to) for to in t.STATUSES)
    assert [to for to in t.STATUSES if t.is_legal("approved", to)] == ["retired"]


def test_a_status_never_moves_to_itself():
    assert not any(t.is_legal(s, s) for s in t.STATUSES)


def test_only_the_teachers_decision_can_approve_and_retire_has_its_own_function():
    assert "approved" not in t.ADVANCE_TARGETS
    assert "retired" not in t.ADVANCE_TARGETS
    assert all(any(t.is_legal(a, b) for a in t.STATUSES) for b in t.ADVANCE_TARGETS)


def test_the_automated_review_can_only_pass_or_hold():
    assert t.REVIEW_RESULTS == ("pass", "hold")


# --------------------------------------------------------------------------- code and schema agree
def test_the_moves_in_the_schema_are_the_moves_in_the_code():
    block = sql().split("INSERT INTO measure_status_moves", 1)[1].split(";", 1)[0]
    in_sql = set(re.findall(r"\('(\w+)',\s*'(\w+)'\)", block))
    assert in_sql == set(t.LEGAL)


def test_the_statuses_sources_results_and_reasons_in_the_schema_are_those_in_the_code():
    code = sql()

    def listed(pattern):
        return tuple(re.findall(r"'(\w+)'", re.search(pattern, code, re.S).group(1)))

    status = r"status\s+text NOT NULL DEFAULT 'proposed'\s+CHECK \(status IN \((.*?)\)\)"
    assert listed(status) == t.STATUSES
    assert listed(r"review_result\s+text CHECK \(review_result IN \((.*?)\)\)") == t.REVIEW_RESULTS
    reasons = r"retired_reason\s+text CHECK \(retired_reason IN \((.*?)\)\)"
    assert listed(reasons) == t.RETIRE_REASONS
    assert listed(r"teacher_decision\s+text CHECK \(teacher_decision IN \((.*?)\)\)") == t.DECISIONS
    scopes = r"input_scope\s+text NOT NULL CHECK \(input_scope IN \((.*?)\)\)"
    assert listed(scopes) == t.INPUT_SCOPES
    helper = re.search(r"coalesce\(t ->> 'source', ''\) NOT IN \((.*?)\)", code, re.S).group(1)
    assert tuple(re.findall(r"'(\w+)'", helper)) == t.TEST_SOURCES


def test_the_output_kinds_in_the_schema_are_those_in_the_sandbox_rules():
    pattern = r"output_kind\s+text NOT NULL CHECK \(output_kind IN \((.*?)\)\)"
    declared = re.search(pattern, sql(), re.S)
    assert tuple(re.findall(r"'(\w+)'", declared.group(1))) == load_rules().output_kinds


def test_independent_sources_are_the_two_that_did_not_come_from_the_model():
    assert t.INDEPENDENT_SOURCES == ("teacher", "document_example")
    assert set(t.INDEPENDENT_SOURCES) <= set(t.TEST_SOURCES)
    assert "build" not in t.INDEPENDENT_SOURCES and "break_it" not in t.INDEPENDENT_SOURCES
    has = re.search(r"registry_has_independent_test.*?IN \((.*?)\)", sql(), re.S).group(1)
    assert tuple(re.findall(r"'(\w+)'", has)) == t.INDEPENDENT_SOURCES
