"""Loading the new rulebook versions (v1.1 and v2.1-strict) next to the old ones.

Marked `integration`: a throwaway schema built from the migrations (the loader commits). The admin
step is `python -m mdeis.admin load-criteria`, called here through `admin.main`, with no vectors
(`--no-embed`) because search is not what is tested. One connection is open at a time.
"""

import hashlib
from pathlib import Path

import psycopg
import pytest

from mdeis import admin
from mdeis.core.rules import load_rules

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
T = ROOT / "templates"
V1, V2, V11, V21 = (T / n for n in ("v1.yaml", "v2-strict.yaml", "v1.1.yaml", "v2.1-strict.yaml"))


@pytest.fixture
def url(monkeypatch, built_schema):
    _, scoped = built_schema
    monkeypatch.setenv("DATABASE_URL", scoped)
    return scoped


def run(*args):
    return admin.main(["load-criteria", *map(str, args), "--no-embed", "--by", "Ada Teacher"])


def rows(url, names):
    with psycopg.connect(url) as c:
        return c.execute(
            "SELECT version_name, sha256, content::text, active FROM templates "
            "WHERE version_name = ANY(%s) ORDER BY version_name",
            (names,),
        ).fetchall()


def clause_ids(url, name):
    with psycopg.connect(url) as c:
        return [
            r[0]
            for r in c.execute(
                "SELECT clause_id FROM clauses WHERE version_name = %s ORDER BY clause_id", (name,)
            )
        ]


def test_the_new_files_load_as_new_versions_and_the_old_two_are_not_touched(url):
    assert run(V2, V1) == 0
    before = rows(url, ["v1", "v2-strict"])
    assert [r[0] for r in before] == ["v1", "v2-strict"]
    assert run(V21, V11) == 0
    after = rows(url, ["v1", "v2-strict"])
    assert [(r[0], r[1], r[2]) for r in after] == [(r[0], r[1], r[2]) for r in before], (
        "the stored text and hash of v1 and v2-strict are exactly as they were"
    )
    assert after[0][1] == hashlib.sha256(V1.read_bytes()).hexdigest()
    assert after[1][1] == hashlib.sha256(V2.read_bytes()).hexdigest()


def test_the_new_versions_have_the_new_clauses_and_the_old_ones_do_not(url):
    assert run(V2, V1, V21, V11) == 0
    assert {"GM-2", "GM-3"} <= set(clause_ids(url, "v1.1"))
    assert {"GM-2", "GM-3"} <= set(clause_ids(url, "v2.1-strict"))
    assert not {"GM-2", "GM-3"} & set(clause_ids(url, "v1"))
    assert not {"GM-2", "GM-3"} & set(clause_ids(url, "v2-strict"))


def test_the_last_file_named_becomes_the_active_rulebook_and_it_gives_the_class_view_rules(url):
    assert run(V2, V1) == 0
    with psycopg.connect(url) as c:
        assert load_rules(c).version == "v1" and load_rules(c).min_cell is None
    assert run(V21, V11) == 0
    with psycopg.connect(url) as c:
        active = load_rules(c)
        assert active.version == "v1.1"
        assert (active.min_group, active.min_cell) == (10, 5)
        assert active.clause["cell_minimum"] == "GM-2"
        other = load_rules(c, "v2.1-strict")
        assert (other.min_group, other.min_cell) == (15, 10)
        old = load_rules(c, "v1")
        assert old.min_cell is None, "the old version still gives no class-view rules"
    # By name, not by position: the database's text order puts punctuation in different places.
    flags = {r[0]: r[3] for r in rows(url, ["v1", "v1.1", "v2-strict", "v2.1-strict"])}
    assert flags == {"v1": False, "v1.1": True, "v2-strict": False, "v2.1-strict": False}


def test_loading_the_new_files_twice_leaves_everything_unchanged(url):
    assert run(V21, V11) == 0
    first = rows(url, ["v1.1", "v2.1-strict"])
    assert run(V21, V11) == 0
    assert rows(url, ["v1.1", "v2.1-strict"]) == first


def test_the_loaded_version_can_be_made_active_again_by_the_existing_step(url):
    assert run(V2, V1, V21, V11) == 0
    from mdeis import rulebook

    assert rulebook.main(["activate", "v1", "--by", "Ada Teacher"]) == 0
    with psycopg.connect(url) as c:
        assert load_rules(c).version == "v1"


def test_the_lesson_rules_are_stored_with_the_new_versions_and_the_lesson_reader_reads_them(url):
    from mdeis.lessons.rules import LessonsUnavailable, load_lesson_rules

    assert run(V2, V1, V21, V11) == 0
    for name in ("v1.1", "v2.1-strict"):
        assert {"WD-2", "WD-3", "LS-1"} <= set(clause_ids(url, name)), name
    for name in ("v1", "v2-strict"):
        assert not {"WD-2", "WD-3", "LS-1"} & set(clause_ids(url, name)), name
    with psycopg.connect(url) as c:
        active = load_lesson_rules(c)
        assert active.version == "v1.1"
        assert (active.max_notes_chars, active.max_edits, active.max_edit_chars) == (8000, 12, 1500)
        assert (active.min_group, active.min_cell) == (10, 5)
        assert load_lesson_rules(c, "v2.1-strict").min_group == 15
        for old in ("v1", "v2-strict"):
            with pytest.raises(LessonsUnavailable):
                load_lesson_rules(c, old)
