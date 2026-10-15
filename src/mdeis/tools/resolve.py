"""resolve_issue: the signed-in approver answers a data problem. Answers are added, never edited."""

from decimal import Decimal, InvalidOperation

from ..core.effective import allowed_answers
from ..core.numbers import exact, two_places
from ._call import logged_tool
from ._shared import situation
from .context import Refusal, ToolContext


def _value(raw) -> Decimal:
    try:
        value = Decimal(str(raw))
    except InvalidOperation as e:
        raise Refusal(f"{raw!r} is not a number") from e
    if not value.is_finite() or value < 0:
        raise Refusal("the value must be a number that is not negative")
    return value


@logged_tool("resolve_issue")
def resolve_issue(
    ctx: ToolContext,
    issue_id: str,
    resolution: str,
    value: float | str | None = None,
    note: str | None = None,
):
    """Record the approver's answer to one problem. The approver is the signed-in person.

    The answer must fit the kind of problem. `use_value` needs a
    value; no other answer takes one. The stored scores are never changed: the answer only changes
    which scores the tools use. Refused, and logged, when nobody is signed in.
    """
    if ctx.approver is None:
        raise Refusal("no approver is signed in, so this answer cannot be recorded")
    row = ctx.conn.execute(
        "SELECT learner_id, kind, severity, subject FROM issues WHERE issue_id = %s", (issue_id,)
    ).fetchone()
    if row is None:
        raise Refusal(f"there is no problem {issue_id}")
    learner_id, kind, _, subject = row
    allowed = allowed_answers(kind)
    if resolution not in allowed:
        options = ", ".join(sorted(allowed))
        raise Refusal(f"for a {kind} problem the answer must be one of: {options}")
    if resolution == "use_value":
        if value is None:
            raise Refusal("use_value needs the value to use")
        number = _value(value)
    elif value is not None:
        raise Refusal("a value can only be given with use_value")
    else:
        number = None
    before = situation(ctx, learner_id, None)
    this = next((i for i in before.issues if i["issue_id"] == issue_id), None)
    if this is None:
        raise Refusal(f"{issue_id} does not appear in the learner's records any more")
    if number is not None:
        card = next(c for c in before.cards if c["card_id"] == this["card_id"])
        if exact(number) > card["denominator"]:
            raise Refusal(f"the value must be between 0 and {two_places(card['denominator'])}")
    ctx.conn.execute(
        "INSERT INTO resolutions (issue_id, resolution, value, note, approver_id, approver_name, "
        "run_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            issue_id,
            resolution,
            number,
            note,
            ctx.approver.id,
            ctx.approver.name,
            ctx.run_id,
        ),
    )
    after = situation(ctx, learner_id, None)
    result = {
        "status": "resolved",
        "issue_id": issue_id,
        "kind": kind,
        "subject": subject,
        "resolution": resolution,
        "unblocked_subjects": sorted(before.blocked - after.blocked),
        "approver": ctx.approver.name,
        "logged_event_id": None,
    }
    return result, "ok"
