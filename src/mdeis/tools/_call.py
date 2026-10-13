"""One wrapper that every tool goes through, so no tool can forget the diary.

A tool body returns `(result, outcome)`. This wrapper then writes the diary line (the approver is
read from the context, whatever the outcome), and only then commits the database work. If the diary
line cannot be written, the work is rolled back and the call fails: a call that is not in the diary
did not happen (non-negotiable 9).

A body may also raise `Refusal` (or a rulebook problem). That becomes a normal refused result,
logged like any other, so a refusal leaves a trace.
"""

import functools
import inspect

from mdeis.db import diary

from ..core.rules import RulebookProblem
from ..core.trend import RulebookGap
from .context import Refusal, ToolContext


def logged_tool(name: str):
    def decorate(body):
        signature = inspect.signature(body)

        @functools.wraps(body)
        def run(ctx: ToolContext, *args, **kwargs):
            # Binding first means an unknown argument (such as a made-up approver) is a TypeError
            # before anything happens.
            bound = signature.bind(ctx, *args, **kwargs)
            bound.apply_defaults()
            arguments = dict(list(bound.arguments.items())[1:])
            try:
                result, outcome = body(*bound.args, **bound.kwargs)
            except (Refusal, RulebookProblem, RulebookGap) as refusal:
                result, outcome = {"status": "refused", "reason": str(refusal)}, "refused"
            except Exception as error:
                ctx.conn.rollback()
                _write(ctx, name, arguments, {"error": f"{type(error).__name__}: {error}"}, "error")
                raise
            event_id = _write(ctx, name, arguments, result, outcome)
            if "logged_event_id" in result and result["logged_event_id"] is None:
                result["logged_event_id"] = event_id
            _finish(ctx, name, arguments)
            return result

        return run

    return decorate


def _write(ctx, name, arguments, outputs, outcome) -> int:
    approver = ctx.approver
    try:
        return diary.write(
            name,
            run_id=ctx.run_id,
            inputs=arguments,
            outputs=outputs,
            outcome=outcome,
            approver_id=approver.id if approver else None,
            approver_name=approver.name if approver else None,
            url=ctx.database_url,
        )
    except diary.DiaryUnavailable:
        ctx.conn.rollback()
        raise


def _finish(ctx, name, arguments) -> None:
    if not ctx.commit:
        return
    try:
        ctx.conn.commit()
    except Exception as error:
        ctx.conn.rollback()
        _write(ctx, name, arguments, {"commit_failed": str(error)}, "error")
        raise
