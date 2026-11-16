"""`redact` on logged_tool: a named text argument is written to the diary as a hash and a length
(T-060, slice 3). The diary is hash-chained and cannot be corrected, so a refused note that names a
learner must never enter it. No database: the diary writer is replaced by a recorder.
"""

import hashlib
import json

import pytest

from mdeis.tools import _call
from mdeis.tools.context import Approver, Refusal, ToolContext

SECRET = "Let Amara Nfor lead the reading."


class FakeConn:
    def __init__(self):
        self.rolled_back = False

    def rollback(self):
        self.rolled_back = True

    def commit(self):
        pass


@pytest.fixture
def written(monkeypatch):
    rows = []

    def record(tool, **kwargs):
        rows.append({"tool": tool, **kwargs})
        return len(rows)

    monkeypatch.setattr(_call.diary, "write", record)
    return rows


def ctx():
    who = Approver("u1", "Ms T")
    return ToolContext(conn=FakeConn(), run_id="RUN-T", approver=who, commit=False)


def everything_written(rows):
    return json.dumps(rows, ensure_ascii=False, default=str)


def test_a_redacted_argument_is_logged_as_a_hash_and_the_body_still_gets_the_text(written):
    seen = {}

    @_call.logged_tool("t_notes", redact=("notes",))
    def tool(ctx, stage, notes):
        seen["notes"] = notes
        return {"status": "ok"}, "ok"

    tool(ctx(), "Form 3", SECRET)
    assert seen["notes"] == SECRET
    assert written[0]["inputs"] == {
        "stage": "Form 3",
        "notes": {
            "redacted": True,
            "sha256": hashlib.sha256(SECRET.encode("utf-8")).hexdigest(),
            "length": len(SECRET),
        },
    }
    assert "Amara" not in everything_written(written)


def test_a_refused_call_logs_the_redacted_form_too(written):
    @_call.logged_tool("t_refuse", redact=("notes",))
    def tool(ctx, notes):
        raise Refusal("the notes name a learner")

    result = tool(ctx(), SECRET)
    assert result["status"] == "refused"
    assert written[0]["outcome"] == "refused"
    assert "Amara" not in everything_written(written)


def test_a_failed_call_logs_the_redacted_form_too(written):
    @_call.logged_tool("t_error", redact=("notes",))
    def tool(ctx, notes):
        raise ValueError("broken")

    with pytest.raises(ValueError):
        tool(ctx(), SECRET)
    assert written[0]["outcome"] == "error"
    assert "Amara" not in everything_written(written)


def test_several_arguments_can_be_redacted_and_the_rest_stay_as_they_were(written):
    @_call.logged_tool("t_two", redact=("notes", "objective"))
    def tool(ctx, notes, objective, term=2):
        return {"status": "ok"}, "ok"

    tool(ctx(), "first text", "second text")
    inputs = written[0]["inputs"]
    assert inputs["term"] == 2
    assert inputs["notes"]["length"] == len("first text")
    assert inputs["objective"]["length"] == len("second text")
    assert "first text" not in everything_written(written)


def test_without_redact_every_argument_is_logged_as_given(written):
    @_call.logged_tool("t_plain")
    def tool(ctx, notes):
        return {"status": "ok"}, "ok"

    tool(ctx(), "plain text")
    assert written[0]["inputs"] == {"notes": "plain text"}


def test_a_redact_name_that_is_not_an_argument_is_refused_when_the_tool_is_defined():
    with pytest.raises(TypeError, match="notess"):

        @_call.logged_tool("t_typo", redact=("notess",))
        def tool(ctx, notes):
            return {"status": "ok"}, "ok"


def test_a_value_that_is_not_text_is_left_alone(written):
    @_call.logged_tool("t_none", redact=("notes",))
    def tool(ctx, notes=None):
        return {"status": "ok"}, "ok"

    tool(ctx())
    assert written[0]["inputs"] == {"notes": None}


def test_the_approver_still_comes_from_the_context_not_from_an_argument(written):
    @_call.logged_tool("t_who", redact=("notes",))
    def tool(ctx, notes):
        return {"status": "ok"}, "ok"

    with pytest.raises(TypeError):
        tool(ctx(), "x", approver="someone")
    tool(ctx(), "x")
    assert (written[0]["approver_id"], written[0]["approver_name"]) == ("u1", "Ms T")


def test_a_structured_argument_is_redacted_by_the_hash_of_its_json(written):
    @_call.logged_tool("t_struct", redact=("model_output",))
    def tool(ctx, model_output):
        return {"status": "ok"}, "ok"

    value = {"edits": [{"suggestion": SECRET}]}
    tool(ctx(), value)
    logged = written[0]["inputs"]["model_output"]
    assert logged["redacted"] is True
    assert logged["sha256"] == hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    assert "Amara" not in everything_written(written)
