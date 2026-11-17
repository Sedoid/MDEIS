"""make_chart: what a progression chart should show, from the stored scores or from approved measure
results. It draws nothing."""

from ..core import chartdata
from ._call import logged_tool
from ._shared import active_methodology, rules_text, situation, sources_of, with_hash
from .context import Refusal, ToolContext
from .measures import results_in_run, stale_measures
from .trend import blocking_reasons


@logged_tool("make_chart")
def make_chart(
    ctx: ToolContext,
    learner_id: str,
    skills: list[str] | None = None,
    template_version: str | None = None,
    measure_hashes: list[str] | None = None,
    measure_field: str | None = None,
):
    """Prepare a progression chart of one learner's stored scores, term by term, over the years.

    `skills` names one or more subjects, or learning areas from the rulebook (at most six). Each
    becomes a line. The result lists the scores, the records and the rules they came from. A subject
    with an unanswered blocking problem, or with fewer scores than the rulebook asks for, is left
    off and listed in `excluded` with the reason. To chart the result of an approved measure
    instead, give `measure_hashes`: the `result_hash` of each `compute_measure` result of this run,
    and no `skills`. A measure result with several numbers is drawn one number at a time: name it in
    `measure_field`. A chart takes no number from you, only those hashes and a field name. The
    picture is drawn from this result by the system, after your answer: you never draw it and never
    type its numbers. Say in words what it shows and why anything was left off.
    """
    if measure_hashes:
        if skills:
            raise Refusal("a chart shows subjects or measure results, not both")
        return _measure_chart(
            ctx, learner_id, template_version, list(measure_hashes), measure_field
        )
    found = situation(ctx, learner_id, template_version)
    try:
        data = chartdata.chart_data(found.records, found.rules, skills or [], found.blocked)
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
    result["rules"] = rules_text(ctx.conn, found.rules.version, data["clause_ids"])
    return result, "ok"


def _measure_chart(
    ctx: ToolContext, learner_id: str, template_version, hashes: list, field: str | None
):
    """A chart of approved measure results this run returned. The values are the text the tool
    wrote, taken from the action log by hash: nothing a caller typed can reach the picture."""
    results = results_in_run(ctx, hashes)
    if any(r.get("learner_id") != learner_id for r in results):
        raise Refusal("a result about one learner is not drawn for another")
    if stale_measures(ctx.conn, results):
        raise Refusal("a measure in this chart is no longer in force")
    versions = {r["template_version"] for r in results}
    if len(versions) != 1 or template_version not in (None, *versions):
        raise Refusal("the results must all come from one rulebook version")
    found = situation(ctx, learner_id, versions.pop())
    try:
        data = chartdata.measure_chart_data(results, found.rules, field)
    except (
        chartdata.MixedKinds, chartdata.TooManyLines, chartdata.NothingAsked, chartdata.FieldNeeded
    ) as why:
        raise Refusal(str(why)) from why
    names = [line["name"] for line in data["series"]]
    result = {
        "learner_id": learner_id,
        "template_version": found.rules.version,
        "status": "ok" if names else "not_drawn",
        "title": f"{', '.join(names)} · {learner_id}" if names else "",
        "subtitle": f"Rulebook {found.rules.version}",
        **data,
    }
    with_hash(result)  # the hash covers the values, the measure versions and the rulebook version
    result["methodology_document_id"] = active_methodology(ctx.conn)
    result["sources"] = sources_of(ctx.conn, found, data["record_ids"])
    result["rules"] = rules_text(ctx.conn, found.rules.version, data["clause_ids"])
    return result, "ok"
