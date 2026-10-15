"""The eight tools. Each is a plain function `tool(ctx, **arguments)`. The server wraps them.

The approver is in `ctx`, set from the verified token. No tool takes one as an argument.
"""

from .chart import make_chart
from .citations import check_citations
from .class_view import stage_gap_counts
from .criterion import get_criterion
from .draft import draft_summary
from .ingest import ingest_assessments
from .release import release_report
from .resolve import resolve_issue
from .trend import compute_trend

__all__ = [
    "check_citations",
    "compute_trend",
    "draft_summary",
    "get_criterion",
    "ingest_assessments",
    "make_chart",
    "release_report",
    "resolve_issue",
    "stage_gap_counts",
]
