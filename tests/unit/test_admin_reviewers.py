"""`python -m mdeis.admin grant-reviewer` (T-052, slice 2): who may clear a hold or confirm as the
teacher is recorded by the server operator, in `registry_reviewers`.

The first group needs no database. The second is integration (DATABASE_URL), in a throwaway schema.

What these tests hold the code to:
  * the operator who runs the command is recorded as the one who granted, read from the machine,
    never from an argument, and never the signed-in person of a model session;
  * only the two roles the registry knows are accepted, and a wrong role is refused;
  * granting twice is not an error and changes nothing; granting again with another name while the
    first grant stands is refused; revoking twice is not an error;
  * a revoked reviewer is no longer accepted by the registry functions;
  * every grant, revoke and refusal is a row in the action log;
  * nothing the model can reach can call it.
"""

import getpass
import inspect
from pathlib import Path

import psycopg
import pytest
from dbtools import run_migrate
from psycopg.rows import dict_row

from mdeis import admin
from mdeis.registry import reviewers, store
from mdeis.tools.context import Approver, Refusal, ToolContext

ROOT = Path(__file__).resolve().parents[2]
OPERATOR = "ops1"
GRANTED_BY = f"cli:{OPERATOR}"


# --------------------------------------------------------------------------- no database needed
def test_the_usage_text_lists_the_new_step():
    assert "grant-reviewer" in admin.USAGE
    assert "--revoke" in admin.USAGE


def test_the_roles_are_the_two_the_registry_knows():
    assert reviewers.ROLES == ("developer", "teacher")


def test_a_role_outside_the_two_is_refused_by_the_command_line_before_anything_is_touched(capsys):
    with pytest.raises(SystemExit) as info:
        admin.main(["grant-reviewer", "--user-id", "u-1", "--name", "A", "--role", "admin"])
    assert info.value.code != 0
    assert "role" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["grant-reviewer"],
        ["grant-reviewer", "--user-id", "u-1", "--role", "teacher"],  # a grant needs a name
        ["grant-reviewer", "--name", "A", "--role", "teacher"],
        ["grant-reviewer", "--user-id", "u-1", "--name", "A"],
        ["grant-reviewer", "--user-id", "u-1", "--name", "A", "--role", "teacher", "--by", "Me"],
        ["grant-reviewer", "--user-id", "u-1", "--name", "A", "--role", "teacher",
         "--approver", "x"],
    ],
)
def test_missing_or_extra_arguments_are_refused(argv, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit) as info:
        admin.main(argv)
    assert info.value.code != 0


def test_the_functions_take_a_connection_and_the_reviewer_and_no_signed_in_person():
    for name in ("grant_reviewer", "revoke_reviewer"):
        parameters = set(inspect.signature(getattr(reviewers, name)).parameters)
        assert {"approver", "ctx", "session", "token", "teacher_id", "approver_id"}.isdisjoint(
            parameters), (name, parameters)
        assert {"conn", "user_id", "role", "by"} <= parameters


def test_the_operator_is_the_account_that_runs_the_command(monkeypatch):
    monkeypatch.setattr(getpass, "getuser", lambda: OPERATOR)
    assert reviewers.operator() == GRANTED_BY


def test_an_account_that_cannot_be_named_is_still_recorded_as_a_command_line_step(monkeypatch):
    def nobody():
        raise OSError("no such user")

    monkeypatch.setattr(getpass, "getuser", nobody)
    got = reviewers.operator()
    assert got.startswith("cli:") and len(got) > len("cli:")


def test_nothing_the_model_can_reach_can_grant_or_revoke_a_reviewer():
    places = [ROOT / "src" / "mdeis" / "server.py", ROOT / "src" / "mdeis" / "tools"]
    places += [ROOT / "src" / "orchestrator", ROOT / "src" / "gateway", ROOT / "src" / "portal"]
    files = []
    for place in places:
        files += [place] if place.is_file() else sorted(place.rglob("*.py"))
    assert files
    for file in files:
        text = file.read_text(encoding="utf-8")
        for word in ("grant_reviewer", "revoke_reviewer", "registry.reviewers",
                     "registry import reviewers", "mdeis.admin", "import admin"):
            assert word not in text, (file, word)


def test_the_registry_package_does_not_offer_the_grant_as_one_of_its_functions():
    from mdeis import registry

    assert "grant_reviewer" not in registry.__all__ and "revoke_reviewer" not in registry.__all__


# ------------------------------------------------------------------------------- integration
@pytest.fixture
def db(scratch_schema, monkeypatch):
    schema, url = scratch_schema
    built = run_migrate(url)
    assert built.returncode == 0, built.stderr
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(getpass, "getuser", lambda: OPERATOR)
    with psycopg.connect(url, row_factory=dict_row) as conn:
        conn.url = url
        yield conn
        conn.rollback()


def grant(*extra, user="u-teach", name="Ms Teacher", role="teacher"):
    return admin.main(["grant-reviewer", "--user-id", user, "--name", name, "--role", role, *extra])


def revoke(user="u-teach", role="teacher", name=None):
    argv = ["grant-reviewer", "--user-id", user, "--role", role, "--revoke"]
    if name:
        argv += ["--name", name]
    return admin.main(argv)


def rows(db, user="u-teach"):
    db.rollback()  # see what the command committed
    return db.execute(
        "SELECT user_id, role, name, granted_by, granted_at, revoked_at "
        "FROM registry_reviewers WHERE user_id = %s ORDER BY role", (user,)
    ).fetchall()


def logged(db, tool=None):
    db.rollback()
    sql = "SELECT tool, inputs, outputs, outcome, approver_id FROM action_log"
    if tool:
        return db.execute(sql + " WHERE tool = %s ORDER BY event_id", (tool,)).fetchall()
    return db.execute(sql + " ORDER BY event_id").fetchall()


def ctx(db, who):
    return ToolContext(conn=db, run_id="RUN-T", approver=who, database_url=db.url, commit=False)


@pytest.mark.integration
def test_a_grant_records_the_reviewer_and_the_operator_and_says_so(db, capsys):
    assert grant() == 0
    (row,) = rows(db)
    assert (row["user_id"], row["role"], row["name"]) == ("u-teach", "teacher", "Ms Teacher")
    assert row["granted_by"] == GRANTED_BY and row["revoked_at"] is None
    assert "now recorded as a teacher" in capsys.readouterr().out


@pytest.mark.integration
def test_a_grant_is_a_row_in_the_action_log_with_the_operator_and_no_signed_in_person(db):
    grant()
    (line,) = logged(db, "admin_grant_reviewer")
    assert line["outcome"] == "ok"
    assert line["inputs"] == {"user_id": "u-teach", "name": "Ms Teacher", "role": "teacher",
                              "by": GRANTED_BY}
    assert line["outputs"]["changed"] is True
    assert line["approver_id"] is None


@pytest.mark.integration
def test_the_registry_accepts_a_granted_teacher_and_a_developer_for_their_own_role_only(db):
    grant(user="u-teach", name="Ms Teacher", role="teacher")
    grant(user="u-dev", name="Dev One", role="developer")
    teacher = Approver("u-teach", "Whatever the browser says")
    who, name, _ = store._registered(ctx(db, teacher), "teacher")
    assert who.id == "u-teach" and name == "Ms Teacher"  # the recorded name, not the typed one
    with pytest.raises(Refusal):
        store._registered(ctx(db, teacher), "developer")
    assert store._registered(ctx(db, Approver("u-dev", "x")), "developer")[1] == "Dev One"


@pytest.mark.integration
def test_the_same_person_can_hold_both_roles_as_two_rows(db):
    grant(role="teacher")
    grant(role="developer")
    assert [r["role"] for r in rows(db)] == ["developer", "teacher"]


@pytest.mark.integration
def test_a_duplicate_grant_changes_nothing_and_is_not_an_error(db, capsys):
    assert grant() == 0
    (first,) = rows(db)
    capsys.readouterr()
    assert grant() == 0
    (second,) = rows(db)
    assert second == first  # the same granted_at too
    assert "nothing changed" in capsys.readouterr().out
    lines = logged(db, "admin_grant_reviewer")
    assert [line["outputs"]["changed"] for line in lines] == [True, False]
    assert all(line["outcome"] == "ok" for line in lines)


@pytest.mark.integration
def test_a_grant_with_other_spaces_around_the_words_is_the_same_grant(db):
    grant()
    assert grant(user="  u-teach ", name=" Ms Teacher  ") == 0
    assert len(rows(db)) == 1 and rows(db)[0]["name"] == "Ms Teacher"


@pytest.mark.integration
def test_a_second_name_for_a_standing_grant_is_refused_and_the_row_is_kept(db, capsys):
    grant()
    capsys.readouterr()
    assert grant(name="Someone Else") == 1
    assert "revoke" in capsys.readouterr().err
    assert rows(db)[0]["name"] == "Ms Teacher"
    last = logged(db, "admin_grant_reviewer")[-1]
    assert last["outcome"] == "refused" and "reason" in last["outputs"]


@pytest.mark.integration
def test_a_wrong_role_given_to_the_function_is_refused_and_logged(db):
    with pytest.raises(reviewers.ReviewerRefused):
        reviewers.grant_reviewer(db, "u-1", "A", "admin", by=GRANTED_BY, url=db.url)
    assert rows(db, "u-1") == []
    (line,) = logged(db, "admin_grant_reviewer")
    assert line["outcome"] == "refused"


@pytest.mark.integration
@pytest.mark.parametrize("user, name", [("", "A"), ("  ", "A"), ("u-1", ""), ("u-1", "   ")])
def test_a_blank_id_or_name_is_refused_and_nothing_is_stored(db, user, name):
    with pytest.raises(reviewers.ReviewerRefused):
        reviewers.grant_reviewer(db, user, name, "teacher", by=GRANTED_BY, url=db.url)
    assert db.execute("SELECT count(*) AS n FROM registry_reviewers").fetchone()["n"] == 0


@pytest.mark.integration
def test_a_revoke_ends_the_grant_and_the_registry_stops_accepting_the_person(db, capsys):
    grant()
    capsys.readouterr()
    assert revoke() == 0
    (row,) = rows(db)
    assert row["revoked_at"] is not None  # the row stays: who was recorded, and when, is history
    assert "no longer recorded as a teacher" in capsys.readouterr().out
    with pytest.raises(Refusal):
        store._registered(ctx(db, Approver("u-teach", "x")), "teacher")
    (line,) = logged(db, "admin_revoke_reviewer")
    assert line["outcome"] == "ok" and line["outputs"]["changed"] is True
    assert line["inputs"] == {"user_id": "u-teach", "role": "teacher", "revoke": True,
                              "by": GRANTED_BY}


@pytest.mark.integration
def test_a_revoke_needs_no_name_but_accepts_one(db):
    grant()
    assert revoke(name="Ms Teacher") == 0
    assert rows(db)[0]["revoked_at"] is not None


@pytest.mark.integration
def test_a_second_revoke_changes_nothing_and_is_not_an_error(db, capsys):
    grant()
    revoke()
    (first,) = rows(db)
    capsys.readouterr()
    assert revoke() == 0
    assert rows(db)[0]["revoked_at"] == first["revoked_at"]
    assert "nothing changed" in capsys.readouterr().out
    assert [line["outputs"]["changed"] for line in logged(db, "admin_revoke_reviewer")] == [
        True, False]


@pytest.mark.integration
def test_revoking_someone_who_was_never_granted_is_refused_and_logged(db, capsys):
    assert revoke(user="u-nobody") == 1
    assert "no" in capsys.readouterr().err
    (line,) = logged(db, "admin_revoke_reviewer")
    assert line["outcome"] == "refused"


@pytest.mark.integration
def test_a_revoke_of_one_role_leaves_the_other_role(db):
    grant(role="teacher")
    grant(role="developer")
    revoke(role="teacher")
    by_role = {r["role"]: r for r in rows(db)}
    assert by_role["teacher"]["revoked_at"] is not None
    assert by_role["developer"]["revoked_at"] is None


@pytest.mark.integration
def test_a_grant_after_a_revoke_makes_the_person_a_reviewer_again_with_the_new_name(db):
    grant()
    revoke()
    assert grant(name="Ms T. Teacher") == 0
    (row,) = rows(db)
    assert row["revoked_at"] is None and row["name"] == "Ms T. Teacher"
    assert row["granted_by"] == GRANTED_BY
    who, name, _ = store._registered(ctx(db, Approver("u-teach", "x")), "teacher")
    assert name == "Ms T. Teacher"


@pytest.mark.integration
def test_the_command_line_step_commits_and_leaves_no_other_table_changed(db):
    before = db.execute("SELECT count(*) AS n FROM measure_versions").fetchone()["n"]
    grant()
    assert db.execute("SELECT count(*) AS n FROM measure_versions").fetchone()["n"] == before
    assert db.execute("SELECT count(*) AS n FROM measures").fetchone()["n"] == 0
