"""The MDEIS MCP server (Streamable HTTP): eight tools, no model, no model key.

Each tool here only translates an MCP call into a call of the plain function in `mdeis.tools`.
The two things the model must not control are read from the HTTP headers the orchestrator sets,
and never from tool arguments:

    X-Approver-Token   the signed-in person, signed by the gateway (mdeis.core.approver_token).
                       Missing, forged or expired: there is no approver, so acting tools refuse.
    X-Run-Id           the run the call belongs to; the diary and the citation check group by it.

Every call opens its own database connection, so no call sees another's half-finished work.
"""

import os
import uuid

import psycopg
from mcp.server.fastmcp import Context, FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import tools
from .core import approver_token
from .tools import class_view
from .tools.context import Approver, ToolContext
from .tools.criterion import DEFAULT_RESULTS

APPROVER_HEADER = "x-approver-token"
RUN_HEADER = "x-run-id"

mcp = FastMCP(
    "mdeis",
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_PORT", "8100")),
)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def _headers(request: Context) -> dict:
    """The HTTP headers of this call, or none (a call that did not come over HTTP)."""
    try:
        return dict(request.request_context.request.headers)
    except (AttributeError, ValueError):
        return {}


def _approver(headers: dict) -> Approver | None:
    token = headers.get(APPROVER_HEADER)
    if not token:
        return None
    try:
        who = approver_token.verify(os.environ.get("APPROVER_TOKEN_SECRET", ""), token)
    except approver_token.TokenError:
        return None  # a bad token is the same as no token
    return Approver(id=who.id, name=who.name)


def _run(request: Context, tool, **arguments) -> dict:
    headers = _headers(request)
    url = os.environ["DATABASE_URL"]
    with psycopg.connect(url) as conn:
        ctx = ToolContext(
            conn=conn,
            run_id=headers.get(RUN_HEADER) or f"RUN-{uuid.uuid4()}",
            approver=_approver(headers),
            database_url=url,
        )
        return tool(ctx, **arguments)


def _about(tool) -> str:
    return " ".join((tool.__doc__ or "").split())


@mcp.tool(description=_about(tools.ingest_assessments))
def ingest_assessments(
    request: Context, learner_id: str, template_version: str | None = None
) -> dict:
    return _run(
        request, tools.ingest_assessments, learner_id=learner_id, template_version=template_version
    )


@mcp.tool(description=_about(tools.compute_trend))
def compute_trend(
    request: Context, learner_id: str, skill: str, template_version: str | None = None
) -> dict:
    return _run(
        request,
        tools.compute_trend,
        learner_id=learner_id,
        skill=skill,
        template_version=template_version,
    )


@mcp.tool(description=_about(tools.resolve_issue))
def resolve_issue(
    request: Context,
    issue_id: str,
    resolution: str,
    value: float | str | None = None,
    note: str | None = None,
) -> dict:
    return _run(
        request,
        tools.resolve_issue,
        issue_id=issue_id,
        resolution=resolution,
        value=value,
        note=note,
    )


@mcp.tool(description=_about(tools.get_criterion))
def get_criterion(
    request: Context,
    query: str,
    template_version: str | None = None,
    k: int = DEFAULT_RESULTS,
) -> dict:
    return _run(
        request, tools.get_criterion, query=query, template_version=template_version, k=k
    )


@mcp.tool(description=_about(tools.draft_summary))
def draft_summary(
    request: Context, learner_id: str, text: str, finding_hashes: list[str]
) -> dict:
    return _run(
        request,
        tools.draft_summary,
        learner_id=learner_id,
        text=text,
        finding_hashes=finding_hashes,
    )


@mcp.tool(description=_about(tools.check_citations))
def check_citations(request: Context, report_id: str) -> dict:
    return _run(request, tools.check_citations, report_id=report_id)


@mcp.tool(description=_about(tools.make_chart))
def make_chart(
    request: Context, learner_id: str, skills: list[str], template_version: str | None = None
) -> dict:
    return _run(
        request,
        tools.make_chart,
        learner_id=learner_id,
        skills=skills,
        template_version=template_version,
    )


@mcp.tool(description=_about(tools.release_report))
def release_report(request: Context, report_id: str) -> dict:
    return _run(request, tools.release_report, report_id=report_id)


if class_view.enabled():  # locked unless CLASS_VIEW_ENABLED is switched on (docs/SPEC.md 5.8)

    @mcp.tool(description=_about(tools.stage_gap_counts))
    def stage_gap_counts(
        request: Context,
        school_id: str,
        stage: str,
        skill: str,
        term: int,
        template_version: str | None = None,
    ) -> dict:
        return _run(
            request,
            tools.stage_gap_counts,
            school_id=school_id,
            stage=stage,
            skill=skill,
            term=term,
            template_version=template_version,
        )


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
