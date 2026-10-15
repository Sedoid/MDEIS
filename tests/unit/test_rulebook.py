"""Loading a rulebook version, giving each rule a vector, and searching the rules.

The database tests are marked `integration` and run in a throwaway schema (they COMMIT, and the
live diary cannot be cleaned up). A fake embedder server stands in for the llama.cpp container, so
the real HTTP client runs; it checks plumbing, not search quality (that is the server-only test).
"""

import ast
import hashlib
from pathlib import Path

import psycopg
import pytest
from fake_embedder import DIM, FakeEmbedder

from mdeis import rulebook
from mdeis.core.template import load_template
from mdeis.rulebook import Embedder, RulebookError

ROOT = Path(__file__).resolve().parents[2]
V1 = ROOT / "templates" / "v1.yaml"
STRICT = ROOT / "templates" / "v2-strict.yaml"
SOURCE = ROOT / "src" / "mdeis" / "rulebook.py"
TUNING = {"HTTP_TIMEOUT_S", "EMBED_BATCH", "SEARCH_K", "EXIT_VECTORS_MISSING"}


# --------------------------------------------------------------------------- no database needed
def test_no_school_number_is_written_in_the_loader():
    """Only 0 and 1, plus a few named constants (timeout, batch size, result count, exit)."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    tuning_nodes = {
        id(n)
        for stmt in tree.body
        if isinstance(stmt, ast.Assign)
        and any(getattr(t, "id", "") in TUNING for t in stmt.targets)
        for n in ast.walk(stmt)
    }
    found = {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, (int, float))
        and not isinstance(n.value, bool)
        and id(n) not in tuning_nodes
    }
    assert found <= {0, 1}, f"unexpected numbers in the loader: {sorted(found - {0, 1})}"
    assert {n.targets[0].id for n in tree.body if isinstance(n, ast.Assign)} >= TUNING


@pytest.fixture
def fake():
    server = FakeEmbedder()
    yield server
    server.stop()


def test_documents_and_queries_get_their_different_prefixes(fake):
    e = Embedder(fake.url, model="m")
    e.embed(["a rule"], rulebook.DOCUMENT_PREFIX)
    e.embed(["a question"], rulebook.QUERY_PREFIX)
    assert fake.texts_sent == ["search_document: a rule", "search_query: a question"]


def test_a_long_list_is_sent_in_batches_and_comes_back_in_order(fake):
    texts = [f"rule number {i}" for i in range(rulebook.EMBED_BATCH * 2 + 1)]
    vectors, model = Embedder(fake.url).embed(texts, "")
    assert len(fake.calls) == 3
    assert model == "fake-embed"  # no EMBED_MODEL_FILE given: the server's own name is used
    from fake_embedder import bag_of_words

    assert vectors[5] == bag_of_words("rule number 5")  # the fake answers in reverse order


@pytest.mark.parametrize("mode", ["error", "short"])
def test_an_embedder_that_fails_or_miscounts_is_reported_as_unavailable(fake, mode):
    fake.mode = mode
    with pytest.raises(rulebook.EmbedderUnavailable):
        Embedder(fake.url, model="m").embed(["x", "y"], "")


def test_an_embedder_that_is_not_there_is_reported_as_unavailable():
    with pytest.raises(rulebook.EmbedderUnavailable):
        Embedder("http://127.0.0.1:1", model="m").embed(["x"], "")


# --------------------------------------------------------------------------- with the database
integration = pytest.mark.integration


@pytest.fixture
def env(monkeypatch, built_schema, fake):
    """Point the command at the scratch schema and the fake embedder."""
    _, url = built_schema
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("EMBEDDER_URL", fake.url)
    monkeypatch.setenv("EMBED_DIM", str(DIM))
    monkeypatch.delenv("EMBED_MODEL_FILE", raising=False)
    return url


@pytest.fixture
def db(env):
    with psycopg.connect(env) as c:
        yield c


def snapshot(db):
    """Everything in the rule tables, as one string, so 'nothing changed' can be compared."""
    db.rollback()
    parts = [
        db.execute(
            "SELECT string_agg(t::text, '|' ORDER BY version_name) FROM templates t"
        ).fetchone()[0],
        db.execute(
            "SELECT string_agg(c::text, '|' ORDER BY version_name, clause_id) FROM clauses c"
        ).fetchone()[0],
    ]
    return hashlib.sha256(repr(parts).encode()).hexdigest()


def diary_tools(db):
    db.rollback()
    return [r[0:2] for r in db.execute("SELECT tool, outcome FROM action_log ORDER BY event_id")]


def run(*args):
    return rulebook.main(list(args))


@integration
def test_both_versions_load_with_the_same_rule_ids_and_the_last_one_is_active(env, db, capsys):
    assert run("load", str(STRICT), str(V1)) == 0
    names = [r[0] for r in db.execute("SELECT version_name FROM templates ORDER BY 1")]
    assert names == ["v1", "v2-strict"]
    ids = {
        v: [
            r[0]
            for r in db.execute(
                "SELECT clause_id FROM clauses WHERE version_name = %s ORDER BY 1", (v,)
            )
        ]
        for v in names
    }
    assert ids["v1"] == ids["v2-strict"]
    assert len(ids["v1"]) == len(load_template(V1).clauses())
    assert {"TR-1", "ST-2"} <= set(ids["v1"])
    assert db.execute("SELECT version_name FROM templates WHERE active").fetchall() == [("v1",)]
    assert "every version has the same rule IDs" in capsys.readouterr().out


@integration
def test_a_rule_keeps_its_own_values_from_the_file_not_from_the_code(env, db):
    run("load", str(V1))
    expected = next(
        r
        for r in load_template(V1).model_dump(mode="json")["stage_expectations"]
        if r["id"] == "ST-1"
    )
    params, text, area = db.execute(
        "SELECT params, text, area FROM clauses WHERE version_name = 'v1' AND clause_id = 'ST-1'"
    ).fetchone()
    assert (area, text) == ("ST", expected["text"])
    assert params["range"] == expected["range"]
    content = db.execute("SELECT content FROM templates WHERE version_name = 'v1'").fetchone()[0]
    assert content == load_template(V1).model_dump(mode="json")


@integration
def test_loading_twice_changes_nothing(env, db, fake):
    run("load", str(STRICT), str(V1))
    before = (snapshot(db), diary_tools(db), len(fake.calls))
    assert run("load", str(STRICT), str(V1)) == 0
    assert (
        snapshot(db),
        diary_tools(db),
        len(fake.calls),
    ) == before  # no rows, no diary, no embedder


@integration
def test_every_rule_gets_a_vector_of_the_right_size_and_the_model_is_recorded(env, db, fake):
    run("load", str(V1))
    total, with_vec, models = db.execute(
        "SELECT count(*), count(embedding), array_agg(DISTINCT embedding_model) FROM clauses"
    ).fetchone()
    assert total == with_vec == len(load_template(V1).clauses())
    assert models == ["fake-embed"]
    assert db.execute("SELECT vector_dims(embedding) FROM clauses LIMIT 1").fetchone()[0] == DIM
    assert all(t.startswith("search_document: ") for t in fake.texts_sent)


@integration
def test_a_changed_file_under_a_loaded_name_is_refused_and_nothing_changes(
    env, db, tmp_path, capsys
):
    run("load", str(V1))
    before = snapshot(db)
    edited = tmp_path / "v1.yaml"
    edited.write_text(V1.read_text(encoding="utf-8").replace("Form 1 literacy", "Form 1 reading"))
    assert run("load", str(edited)) == 1
    assert "new file name" in capsys.readouterr().err
    assert snapshot(db) == before
    assert ("load_rulebook", "refused") in diary_tools(db)


@integration
def test_the_edit_saved_under_a_new_name_loads_and_becomes_the_active_one(env, db, tmp_path):
    run("load", str(V1))
    edited = tmp_path / "v1-b.yaml"
    edited.write_text(V1.read_text(encoding="utf-8").replace("Form 1 literacy", "Form 1 reading"))
    assert run("load", str(edited)) == 0
    assert db.execute("SELECT version_name FROM templates WHERE active").fetchall() == [("v1-b",)]
    old = db.execute(
        "SELECT text FROM clauses WHERE version_name='v1' AND clause_id='ST-1'"
    ).fetchone()
    assert old[0].startswith("Form 1 literacy")  # the old version is untouched


@integration
def test_a_file_that_is_not_a_valid_rulebook_never_gets_in(env, db, tmp_path, capsys):
    bad = tmp_path / "broken.yaml"
    bad.write_text("template_id: x\n")
    assert run("load", str(bad)) == 1
    assert "not a valid rulebook" in capsys.readouterr().err
    assert db.execute("SELECT count(*) FROM templates").fetchone()[0] == 0


@integration
def test_load_without_activating_leaves_the_active_version_alone(env, db):
    run("load", str(V1))
    run("load", str(STRICT), "--no-activate")
    assert db.execute("SELECT version_name FROM templates WHERE active").fetchall() == [("v1",)]
    assert run("activate", "v2-strict") == 0
    assert db.execute("SELECT version_name FROM templates WHERE active").fetchall() == [
        ("v2-strict",)
    ]
    assert run("activate", "nope") == 1


@integration
def test_if_the_embedder_is_down_the_rules_still_load_and_a_later_run_fills_the_vectors(
    env, db, fake, monkeypatch, capsys
):
    monkeypatch.setenv("EMBEDDER_URL", "http://127.0.0.1:1")
    assert run("load", str(V1)) == 0  # rules in, warning printed
    assert "no vectors were made" in capsys.readouterr().err
    assert db.execute("SELECT count(*), count(embedding) FROM clauses").fetchone()[0:2] == (
        len(load_template(V1).clauses()),
        0,
    )
    assert (
        run("load", str(V1), "--require-embeddings") == rulebook.EXIT_VECTORS_MISSING
    )  # still down, and asked to insist
    monkeypatch.setenv("EMBEDDER_URL", fake.url)
    assert run("load", str(V1)) == 0  # the file is unchanged, the vectors get filled
    assert db.execute("SELECT count(*) = count(embedding) FROM clauses").fetchone()[0]


@integration
def test_only_rules_without_a_vector_are_sent_to_the_embedder(env, db, fake):
    run("load", str(V1))
    first = len(fake.texts_sent)
    run("load", str(STRICT))
    assert len(fake.texts_sent) - first == len(load_template(STRICT).clauses())  # not v1 again


@integration
def test_a_vector_of_the_wrong_size_is_refused_and_nothing_is_stored(env, db, fake):
    fake.mode = "wrong_size"
    run("load", str(V1))
    assert db.execute("SELECT count(embedding) FROM clauses").fetchone()[0] == 0
    assert run("embed") == 1


@integration
def test_a_setting_that_disagrees_with_the_database_column_is_refused(env, db, monkeypatch, capsys):
    run("load", str(V1), "--no-embed")
    monkeypatch.setenv("EMBED_DIM", str(DIM - 1))
    assert run("embed") == 1
    assert "must match" in capsys.readouterr().err


@integration
def test_force_redoes_every_vector(env, db, fake):
    run("load", str(V1))
    n = len(fake.texts_sent)
    run("embed")
    assert len(fake.texts_sent) == n
    run("embed", "--force")
    assert len(fake.texts_sent) == 2 * n


@integration
def test_search_finds_the_rule_whose_words_match_and_uses_the_query_prefix(env, db, fake):
    run("load", str(V1))
    hits = rulebook.search_clauses(db, Embedder(), "Form 1 literacy expected", version="v1", k=3)
    assert hits[0]["clause_id"] == "ST-1"
    assert [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)
    assert fake.texts_sent[-1] == "search_query: Form 1 literacy expected"


@integration
def test_search_defaults_to_the_active_version_and_says_when_it_cannot_search(env, db, fake):
    with pytest.raises(RulebookError, match="no rulebook version is active"):
        rulebook.search_clauses(db, Embedder(), "x")
    run("load", str(V1), "--no-embed")
    with pytest.raises(RulebookError, match="no vectors"):
        rulebook.search_clauses(db, Embedder(), "x")
    run("embed")
    assert rulebook.search_clauses(db, Embedder(), "pass mark", k=1)
    with pytest.raises(RulebookError, match="no version named"):
        rulebook.search_clauses(db, Embedder(), "x", version="v9")


@integration
def test_every_change_is_in_the_diary_and_the_chain_holds(env, db):
    run("load", str(STRICT), str(V1))
    run("load", str(STRICT), str(V1))  # the repeat adds nothing
    tools = diary_tools(db)
    assert tools.count(("load_rulebook", "ok")) == 2
    assert tools.count(("embed_rulebook", "ok")) == 1
    assert db.execute("SELECT count(*) FROM verify_action_log()").fetchone()[0] == 0


# --------------------------------------------------------------------------- who did it
def who(db, tool):
    db.rollback()
    return db.execute(
        "SELECT approver_name FROM action_log WHERE tool = %s ORDER BY event_id", (tool,)
    ).fetchall()


@integration
def test_a_named_person_is_recorded_on_the_rulebook_and_in_the_diary(env, db, capsys):
    assert run("load", str(V1), "--by", "Ada Teacher") == 0
    assert db.execute("SELECT loaded_by FROM templates WHERE version_name = 'v1'").fetchone() == (
        "Ada Teacher",
    )
    assert ("Ada Teacher",) in who(db, "load_rulebook")
    assert "Ada Teacher" in capsys.readouterr().out  # `status` shows who loaded it


@integration
def test_without_a_name_the_record_says_the_command_line_did_it(env, db):
    run("load", str(V1))
    assert db.execute("SELECT loaded_by FROM templates WHERE version_name = 'v1'").fetchone() == (
        rulebook.LOADED_BY,
    )
    assert who(db, "load_rulebook") == [(None,)]


@integration
def test_the_name_can_come_from_the_environment(env, db, monkeypatch):
    monkeypatch.setenv("RULEBOOK_BY", "Env Person")
    run("load", str(V1))
    assert db.execute("SELECT loaded_by FROM templates WHERE version_name = 'v1'").fetchone() == (
        "Env Person",
    )


@integration
def test_switching_the_active_version_records_who_did_it(env, db):
    run("load", str(V1), str(STRICT), "--no-activate")
    assert run("activate", "v2-strict", "--by", "Ben Head") == 0
    assert who(db, "activate_rulebook") == [("Ben Head",)]


@integration
def test_a_refused_load_records_who_tried(env, db, tmp_path):
    run("load", str(V1))
    edited = tmp_path / "v1.yaml"
    edited.write_text(V1.read_text(encoding="utf-8").replace("Form 1 literacy", "Form 1 reading"))
    assert run("load", str(edited), "--by", "Cy Clerk") == 1
    refused_lines = db.execute(
        "SELECT approver_name FROM action_log WHERE tool = 'load_rulebook' AND outcome = 'refused'"
    ).fetchall()
    assert refused_lines == [("Cy Clerk",)]


# --------------------------------------------------------------------------- two runs at once
@integration
def test_a_run_that_loses_a_race_reports_what_the_other_left_and_does_not_duplicate(
    env, db, monkeypatch
):
    real_rows = rulebook._rule_rows
    state = {"other_has_run": False}

    def rows_then_the_other_run_finishes(template):
        rows = real_rows(template)
        if not state["other_has_run"]:
            state["other_has_run"] = True
            with psycopg.connect(env) as other:
                rulebook.load_file(other, V1, url=env)  # finishes between our check and our insert
        return rows

    monkeypatch.setattr(rulebook, "_rule_rows", rows_then_the_other_run_finishes)
    result = rulebook.load_file(db, V1, url=env)
    assert result.status == "unchanged"
    assert db.execute("SELECT count(*) FROM templates").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM clauses").fetchone()[0] == len(
        load_template(V1).clauses()
    )
    assert ("load_rulebook", "error") in diary_tools(db)  # the failed insert is on record


# --------------------------------------------------------------------------- no database needed
def test_a_database_that_is_not_there_gives_one_plain_line_not_a_traceback(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none?connect_timeout=2")
    assert run("status") == 1
    err = capsys.readouterr().err
    assert "refused: a database problem" in err
    assert "Traceback" not in err


def test_the_admin_name_from_the_spec_runs_the_same_load(monkeypatch):
    from mdeis import admin

    seen = []
    monkeypatch.setattr(rulebook, "main", lambda argv: seen.append(argv) or 0)
    assert admin.main(["load-criteria", "a.yaml", "b.yaml", "--by", "Ada Teacher"]) == 0
    assert seen == [["load", "a.yaml", "b.yaml", "--by", "Ada Teacher"]]
    assert admin.main([]) == 2
    assert admin.main(["nonsense"]) == 2
