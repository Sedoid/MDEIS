"""The diary helper (src/mdeis/db/diary.py): one line per call, even when the call rolls back.

The first four tests need no database. The rest are marked `integration` and run on the server
(`make test`), in the throwaway schema made by the `test_db_url` fixture. Lines these tests commit
stay in that schema and are dropped with it.
"""

import inspect
import os

import psycopg
import pytest
from dbtools import one

from mdeis.db.diary import DiaryError, log_event, write_line

integration = pytest.mark.integration


def unique(prefix):
    return f"{prefix}_{os.urandom(3).hex()}"


def lines(url, tool):
    with psycopg.connect(url) as c:
        return one(c, "SELECT count(*) FROM action_log WHERE tool = %s", (tool,))


# --------------------------------------------------------------------------- no database needed
def test_the_helper_has_no_way_to_set_the_event_number_or_the_hashes():
    names = set(inspect.signature(log_event).parameters) | set(
        inspect.signature(write_line).parameters
    )
    assert not names & {"event_id", "prev_hash", "row_hash", "hash", "at"}


def test_an_unknown_outcome_is_refused_before_anything_is_written():
    with pytest.raises(ValueError):
        write_line(None, tool="x", outcome="fine")


def test_without_a_database_address_the_line_fails_instead_of_being_skipped(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(DiaryError):
        log_event(tool="x")


def test_an_unreachable_database_fails_the_call_quickly():
    with pytest.raises(DiaryError):
        log_event(
            tool="x",
            database_url="postgresql://nobody@127.0.0.1:1/none",
            connect_timeout_s=2,
        )


# --------------------------------------------------------------------------- needs the server
@integration
def test_a_line_from_log_event_stays_when_the_work_it_describes_is_rolled_back(test_db_url):
    tool = unique("refused_call")
    run = "RUN-" + tool
    with psycopg.connect(test_db_url) as work:
        work.execute("INSERT INTO runs (run_id) VALUES (%s)", (run,))
        event = log_event(
            tool=tool,
            run_id=run,
            inputs={"report_id": "R-000001"},
            outputs={"reason": "no approver"},
            outcome="refused",
            database_url=test_db_url,
        )
        work.rollback()
    assert isinstance(event, int)
    with psycopg.connect(test_db_url) as c:
        assert one(c, "SELECT count(*) FROM runs WHERE run_id = %s", (run,)) == 0
        row = c.execute(
            "SELECT outcome, inputs ->> 'report_id', outputs ->> 'reason', approver_id "
            "FROM action_log WHERE event_id = %s",
            (event,),
        ).fetchone()
    assert row == ("refused", "R-000001", "no approver", None)


@integration
def test_write_line_is_saved_or_lost_together_with_the_transaction_it_is_in(test_db_url):
    kept, lost = unique("kept"), unique("lost")
    with psycopg.connect(test_db_url) as c:
        write_line(c, tool=kept)
        c.commit()
        write_line(c, tool=lost)
        c.rollback()
    assert lines(test_db_url, kept) == 1
    assert lines(test_db_url, lost) == 0


@integration
def test_a_line_that_cannot_get_the_diary_lock_fails_instead_of_waiting(test_db_url):
    tool = unique("blocked_call")
    with psycopg.connect(test_db_url) as holder:
        # This line is not committed yet, so the diary's lock is held until the transaction ends.
        write_line(holder, tool=unique("holder"))
        with pytest.raises(DiaryError):
            log_event(tool=tool, database_url=test_db_url, lock_timeout_ms=300)
        holder.rollback()
    assert lines(test_db_url, tool) == 0
    # With the lock free again the same call works.
    log_event(tool=tool, database_url=test_db_url)
    assert lines(test_db_url, tool) == 1


@integration
def test_lines_from_the_helper_keep_the_chain_intact(test_db_url):
    for i in range(3):
        log_event(tool=unique(f"chain{i}"), database_url=test_db_url)
    with psycopg.connect(test_db_url) as c:
        assert one(c, "SELECT count(*) FROM verify_action_log()") == 0
