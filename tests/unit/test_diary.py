"""The diary helper (mdeis.db.diary): each line on its own short connection, fail closed.

Marked `integration`. These tests COMMIT diary lines (that is the point of the helper), and the live
diary can never be cleaned up, so every test works in a throwaway schema built from the migrations
(the `built_schema` fixture). The live diary is not touched.
"""

import threading
import time
from datetime import datetime
from decimal import Decimal

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from mdeis.db import diary

pytestmark = pytest.mark.integration

DEAD_URL = "postgresql://nobody:x@127.0.0.1:1/none"  # nothing listens on port 1


def lines(url):
    with psycopg.connect(url) as c:
        return c.execute(
            "SELECT event_id, run_id, tool, inputs, outputs, outcome FROM action_log "
            "ORDER BY event_id"
        ).fetchall()


def chain_problems(url):
    with psycopg.connect(url) as c:
        return c.execute("SELECT * FROM verify_action_log()").fetchall()


def learner_count(url):
    with psycopg.connect(url) as c:
        return c.execute("SELECT count(*) FROM learners").fetchone()[0]


def add_learner(conn, ref):
    conn.execute("INSERT INTO learners (school_code, school_ref) VALUES ('TEST', %s)", (ref,))


# --------------------------------------------------------------------------- one line
def test_a_line_is_committed_at_once_and_the_chain_stays_intact(built_schema):
    _, url = built_schema
    n = diary.write("get_criterion", run_id="R-0001", inputs={"criterion": "C-1"}, url=url)
    rows = lines(url)  # read from a different connection: it only sees committed rows
    assert [r[0] for r in rows] == [n]
    assert rows[0][1:3] == ("R-0001", "get_criterion")
    assert rows[0][3] == {"criterion": "C-1"}
    assert rows[0][5] == "ok"
    assert chain_problems(url) == []


def test_values_that_json_cannot_hold_are_written_as_text(built_schema):
    _, url = built_schema
    diary.write(
        "t", inputs={"when": datetime(2026, 10, 12, 9, 0), "mark": Decimal("12.5")}, url=url
    )
    assert lines(url)[0][3] == {"when": "2026-10-12 09:00:00", "mark": "12.5"}


def test_a_bad_outcome_or_an_empty_tool_name_is_refused_before_touching_the_database():
    with pytest.raises(ValueError):
        diary.write("t", outcome="fine", url=DEAD_URL)
    with pytest.raises(ValueError):
        diary.write("", url=DEAD_URL)


# --------------------------------------------------------------------------- the reason it exists
def test_a_rolled_back_call_still_leaves_its_diary_line(built_schema):
    _, url = built_schema
    with psycopg.connect(url) as work:
        with pytest.raises(RuntimeError, match="boom"):
            with diary.logged_call(
                "record_score", run_id="R-0002", inputs={"x": 1}, work=work, url=url
            ):
                add_learner(work, "L1")
                raise RuntimeError("boom")
        work.commit()  # if the helper had not rolled back, this would keep the learner
    assert learner_count(url) == 0  # the work was undone
    rows = lines(url)
    assert [(r[2], r[5]) for r in rows] == [("record_score", "error")]  # the call was not
    assert "boom" in rows[0][4]["error"]
    assert chain_problems(url) == []


def test_a_refusal_is_logged_with_its_reason_and_still_raised(built_schema):
    _, url = built_schema
    with psycopg.connect(url) as work:
        with pytest.raises(diary.CallRefused):
            with diary.logged_call("release_report", inputs={"r": "R-1"}, work=work, url=url):
                raise diary.CallRefused("no passed check", outcome="blocked")
    (row,) = lines(url)
    assert row[5] == "blocked"
    assert row[4] == {"reason": "no passed check"}


def test_a_good_call_commits_its_work_only_after_the_line_is_in(built_schema):
    _, url = built_schema
    with psycopg.connect(url) as work:
        with diary.logged_call("add_learner", inputs={"ref": "L2"}, work=work, url=url) as call:
            add_learner(work, "L2")
            assert learner_count(url) == 0  # not visible to others yet
            call.outputs = {"ref": "L2"}
        assert call.event_id is not None
    assert learner_count(url) == 1
    assert [(r[2], r[5]) for r in lines(url)] == [("add_learner", "ok")]


def test_if_the_diary_cannot_be_written_the_work_is_rolled_back_and_the_call_fails(built_schema):
    _, url = built_schema
    with psycopg.connect(url) as work:
        with pytest.raises(diary.DiaryUnavailable):
            with diary.logged_call("add_learner", work=work, url=DEAD_URL):
                add_learner(work, "L3")
        work.commit()  # as above: only an explicit rollback by the helper keeps the learner out
    assert learner_count(url) == 0
    assert lines(url) == []


def test_if_the_commit_fails_after_the_line_a_second_line_says_so(built_schema):
    _, url = built_schema

    class BrokenCommit:
        rolled_back = False

        def commit(self):
            raise RuntimeError("disk full")

        def rollback(self):
            self.rolled_back = True

    work = BrokenCommit()
    with pytest.raises(RuntimeError, match="disk full"):
        with diary.logged_call("add_learner", work=work, url=url):
            pass
    assert work.rolled_back
    assert [r[5] for r in lines(url)] == ["ok", "error"]
    assert lines(url)[1][4] == {"commit_failed": "disk full"}


# --------------------------------------------------------------------------- locks
def test_an_open_transaction_elsewhere_does_not_block_a_diary_write(built_schema):
    _, url = built_schema
    with psycopg.connect(url) as slow:  # a tool in the middle of long work
        add_learner(slow, "L4")
        start = time.monotonic()
        diary.write("other_tool", url=url)
        assert time.monotonic() - start < 1.0
        slow.rollback()
    assert [r[2] for r in lines(url)] == ["other_tool"]


def test_a_stuck_diary_writer_makes_the_next_one_fail_fast_not_hang(built_schema):
    _, url = built_schema
    with psycopg.connect(url) as stuck:
        # What the old design did: a diary insert inside a long transaction holds the lock.
        stuck.execute("INSERT INTO action_log (tool) VALUES ('held')")
        start = time.monotonic()
        with pytest.raises(diary.DiaryUnavailable):
            diary.write("waiting", url=url, lock_timeout_ms=200, attempts=1)
        assert time.monotonic() - start < 2.0
        stuck.rollback()
    assert lines(url) == []


def test_many_writers_at_once_all_get_a_line_and_the_chain_holds(built_schema):
    _, url = built_schema
    ids, errors = [], []

    def worker(k):
        try:
            for i in range(10):
                ids.append(diary.write("busy", run_id=f"R-{k}", inputs={"i": i}, url=url))
        except Exception as e:  # pragma: no cover - only on failure
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert len(ids) == 60 == len(set(ids))
    assert len(lines(url)) == 60
    assert chain_problems(url) == []


def test_options_already_in_the_url_are_kept(built_schema):
    name, url = built_schema
    # the helper adds its timeouts to the URL's options; the scratch search_path must survive
    assert "search_path" in make_conninfo(url)
    assert diary._conninfo(url, 500).count("search_path") == 1
    diary.write("kept", url=url)
    with psycopg.connect(url) as c:
        assert c.execute(f'SELECT count(*) FROM "{name}".action_log').fetchone()[0] == 1
