"""Step 5, acceptance 5: a search by meaning finds the right rule, with the REAL embedding model.

Needs the embedder container (EMBEDDER_URL, set by the test service) and a database, so it is
skipped on a machine without them, and the log must say so. It runs in a throwaway schema.
Run on the server:   make test ARGS="tests/unit/test_rulebook_real_embedder.py -q -s"
`-s` prints the rank each question got, which is the evidence to paste into the log.
"""

import os
from pathlib import Path

import httpx
import psycopg
import pytest

from mdeis import rulebook
from mdeis.rulebook import Embedder

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
FILES = [ROOT / "templates" / "v1.yaml", ROOT / "templates" / "v2-strict.yaml"]

# (question a person might ask, the rule that answers it)
QUESTIONS = [
    ("expected reading level for Form 1", "ST-1"),
    ("what score counts as a pass", "SC-3"),
    ("which grade does a score of 15 get", "SC-2"),
    ("what happens when a term score is missing", "TR-3"),
    ("how much change counts as improving or declining", "TR-2"),
]
TOP = 3  # the answer must be among the first three rules returned


def embedder_is_up():
    url = os.environ.get("EMBEDDER_URL")
    if not url:
        return False
    try:
        return httpx.get(f"{url.rstrip('/')}/health", timeout=5).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture(scope="module")
def loaded():
    if not embedder_is_up():
        pytest.skip(
            "EMBEDDER_URL is not set or the embedder is not healthy: real-model test skipped"
        )
    return True


@pytest.mark.parametrize("version", ["v1", "v2-strict"])
def test_questions_find_their_rule_with_the_real_model(loaded, built_schema, version, capsys):
    _, url = built_schema
    with psycopg.connect(url) as conn:
        for path in FILES:
            rulebook.load_file(conn, path, url=url)
        embedder = Embedder()
        rulebook.embed_missing(conn, embedder, url=url)
        total = conn.execute(
            "SELECT count(*) FROM clauses WHERE version_name = %s", (version,)
        ).fetchone()[0]
        all_hits = {}
        for question, wanted in QUESTIONS:
            hits = rulebook.search_clauses(conn, embedder, question, version=version, k=total)
            ids = [h["clause_id"] for h in hits]
            all_hits[question] = (wanted, ids.index(wanted) + 1, ids[:TOP])
    with capsys.disabled():
        print(f"\n--- rank of the right rule, version {version} (1 is best) ---")
        for question, (wanted, rank, first) in all_hits.items():
            print(f"{rank:>3}  {wanted:<6} {question!r:<58} top {TOP}: {first}")
    missed = {q: v for q, v in all_hits.items() if v[1] > TOP}
    assert not missed, f"not in the top {TOP}: {missed}"
