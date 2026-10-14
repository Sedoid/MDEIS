"""make_chart: what a progression chart should show, from the stored scores. It draws nothing."""

from ..core import chartdata
from ._call import logged_tool
from ._shared import active_methodology, situation, sources_of, with_hash
from .context import Refusal, ToolContext
from .trend import blocking_reasons


@logged_tool("make_chart")
def make_chart(
    ctx: ToolContext, learner_id: str, skills: list[str], template_version: str | None = None
):
    """Prepare a progression chart of one learner's stored scores, term by term, over the years.

    `skills` names one or more subjects, or learning areas from the rulebook (at most six). Each
    becomes a line. The result lists the scores, the records and the rules they came from. A subject
    with an unanswered blocking problem, or with fewer scores than the rulebook asks for, is left
    off and listed in `excluded` with the reason. The picture is drawn from this result by the
    system, after your answer: you never draw it and never type its numbers. Say in words what it
    shows and why anything was left off.
    """
    found = situation(ctx, learner_id, template_version)
    try:
        data = chartdata.chart_data(found.records, found.rules, skills, found.blocked)
    except (chartdata.UnknownSkill, chartdata.TooManyLines, chartdata.NothingAsked) as why:
        raise Refusal(str(why)) from why
    for item in data["excluded"]:
        if item["reason"] == "blocked":
            subjects = found.rules.areas.get(item["skill"], [item["skill"]])
            item["reasons"] = blocking_reasons(found, subjects)
    names = [line["name"] for line in data["series"]]
    result = {
        "learner_id": learner_id,
        "template_version": found.rules.version,
        "status": "ok" if names else "not_drawn",
        "title": f"{', '.join(names)} · {learner_id}" if names else "",
        "subtitle": f"Rulebook {found.rules.version}",
        **data,
    }
    if data["band_clauses"]:
        listed = ", ".join(data["band_clauses"])
        result["band_legend"] = f"Expected range for the stage ({listed}, {found.rules.version})"
    with_hash(result)  # the hash covers the scores, the ranges and the rulebook version
    result["methodology_document_id"] = active_methodology(ctx.conn)
    result["sources"] = sources_of(ctx.conn, found, data["record_ids"])
    return result, "ok"

