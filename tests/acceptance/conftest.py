"""Shared set-up for the acceptance tests of the practice data (T-002, T-032)."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SEED = 42


@pytest.fixture(scope="session")
def root():
    return ROOT


@pytest.fixture(scope="session")
def templates_dir():
    return ROOT / "templates"


@pytest.fixture(scope="session")
def built(templates_dir):
    """(files, learners, rules) for seed 42, built once. Nothing is written to disk."""
    from synthetic import generate

    return generate.build_outputs(SEED, templates_dir)


@pytest.fixture(scope="session")
def files(built):
    return built[0]


@pytest.fixture(scope="session")
def learners(built):
    return built[1]


@pytest.fixture(scope="session")
def rules(built):
    return built[2]


@pytest.fixture(scope="session")
def key(files):
    return json.loads(files["truth/answer_key.json"])


@pytest.fixture(scope="session")
def problems(files):
    return json.loads(files["truth/injected_problems.json"])
