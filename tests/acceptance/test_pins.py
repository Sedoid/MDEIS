"""E-16 and E-17 (docs/TESTS.md): the version pins and the source references (docs/step-09.md).

A draft is written under one rulebook and one methodology. If either is replaced before the draft is
released, the release is refused with `stale_pin` and a fresh draft is needed. A citation leads to
its source: a record to its document, page and row, a rule to the rulebook version, a methodology
section to its document and page. A section the report's methodology does not have fails the check.

These use the same throwaway database as the tool contract tests (`tool_schema`). A methodology is
put in force on the test's own connection, which is rolled back at the end.
"""

import hashlib
import json
import re
import uuid
from pathlib import Path

import pytest

from mdeis.ingest import methodology

ROOT = Path(__file__).resolve().parents[2]
METHOD = ROOT / "data" / "synthetic" / "methodology"
APPROVER = ("u-ada", "Ada Teacher")


def tool(name):
    import importlib

    return getattr(importlib.import_module("mdeis.tools"), name)


@pytest.fixture(scope="module")
def learner(files):
    fixtures = json.loads(files["truth/fixtures.json"])
    return next(f for f in fixtures if f["handle"] == "FX-01")["gen_id"]


def put_in_force(ctx, name):
    """Store a methodology document the way the worker does, on the test's connection."""
    document_id = ctx.conn.execute(
        "INSERT INTO documents (kind, original_name, stored_name, sha256, size_bytes, uploader_id, "
        "status) VALUES ('methodology', %s, %s, %s, 1, 'test', 'processing') RETURNING document_id",
        (name, uuid.uuid4().hex + ".md", hashlib.sha256(uuid.uuid4().bytes).hexdigest()),
    ).fetchone()[0]
    parsed = methodology.parse_methodology(METHOD / name)
    return document_id, methodology.store_methodology(ctx.conn, document_id, parsed)


def draft_with_section(ctx, learner, section_text):
    """A draft that states FX-01's English change and cites a methodology section."""
    trend = tool("compute_trend")(ctx, learner_id=learner, skill="English")
    tool("get_criterion")(ctx, query="M-6.4")
    sources = " ".join(f"[{r}]" for r in trend["record_ids"])
    text = f"English scores fell by 2.00 points {sources} [TR-2]. {section_text}"
    return tool("draft_summary")(
        ctx, learner_id=learner, text=text, finding_hashes=[trend["result_hash"]]
    )["report_id"]


GOOD = "The document says how a trend is described [M-6.4]."


def test_a_result_names_the_methodology_in_force_and_the_hash_does_not_depend_on_it(
    make_ctx, learner
):
    ctx = make_ctx()
    before = tool("compute_trend")(ctx, learner_id=learner, skill="English")
    assert before["methodology_document_id"] is None
    base, _ = put_in_force(ctx, "methodology-v2.0.md")
    after = tool("compute_trend")(ctx, learner_id=learner, skill="English")
    assert after["methodology_document_id"] == base
    assert after["result_hash"] == before["result_hash"], "the hash covers inputs and rulebook only"


def test_a_draft_remembers_the_rulebook_and_the_methodology_it_was_written_under(make_ctx, learner):
    ctx = make_ctx(APPROVER)
    base, _ = put_in_force(ctx, "methodology-v2.0.md")
    report_id = draft_with_section(ctx, learner, GOOD)
    pinned = ctx.conn.execute(
        "SELECT template_version, methodology_document_id FROM reports WHERE report_id = %s",
        (report_id,),
    ).fetchone()
    assert pinned == ("v1", base)


def test_a_citation_leads_to_its_document_page_and_row_its_rulebook_and_its_section(
    make_ctx, learner
):
    ctx = make_ctx(APPROVER)
    base, _ = put_in_force(ctx, "methodology-v2.0.md")
    report_id = draft_with_section(ctx, learner, GOOD)
    checked = tool("check_citations")(ctx, report_id=report_id)
    assert checked["status"] == "ok", checked["problems"]
    by_kind = {}
    for source in checked["sources"]:
        by_kind.setdefault(source["kind"], []).append(source)
    for record in by_kind["record"]:
        assert re.fullmatch(r"D-\d{6,}", record["document_id"]) and record["page"] and record["row"]
    assert by_kind["clause"][0]["template_version"] == "v1"
    section = by_kind["section"][0]
    assert (section["id"], section["document_id"], section["title"]) == (
        "M-6.4", base, "Trend labels"
    )
    assert section["page"] >= 1


def test_a_section_the_report_does_not_have_fails_the_check(make_ctx, learner):
    ctx = make_ctx(APPROVER)
    report_id = draft_with_section(ctx, learner, "The document says so [M-6.4].")
    checked = tool("check_citations")(ctx, report_id=report_id)
    reasons = {p["reason"] for p in checked["problems"]}
    assert checked["status"] == "fail" and "unknown_section" in reasons, "no methodology is pinned"
    put_in_force(ctx, "methodology-v2.0.md")
    report_id = draft_with_section(ctx, learner, "The document says so [M-99].")
    checked = tool("check_citations")(ctx, report_id=report_id)
    assert {p["reason"] for p in checked["problems"]} == {"unknown_section"}


def test_a_draft_goes_stale_when_a_new_methodology_is_acknowledged_and_a_fresh_one_releases(
    make_ctx, learner
):
    ctx = make_ctx(APPROVER)
    put_in_force(ctx, "methodology-v2.0.md")
    old_draft = draft_with_section(ctx, learner, GOOD)
    assert tool("check_citations")(ctx, report_id=old_draft)["status"] == "ok"
    newer, stored = put_in_force(ctx, "methodology-v2.1-small.md")
    assert stored["status"] == "needs_review", "a changed document waits for a person"
    still = tool("release_report")(ctx, report_id=old_draft)
    assert still["status"] == "released", "nothing changed under the draft yet"

    second = draft_with_section(ctx, learner, GOOD)
    assert tool("check_citations")(ctx, report_id=second)["status"] == "ok"
    methodology.acknowledge(ctx.conn, newer, "Ada Teacher", "read 6.4", ctx.database_url)
    refused = tool("release_report")(ctx, report_id=second)
    assert refused["status"] == "refused" and refused["reason"] == "stale_pin"
    lines = ctx.conn.execute(
        "SELECT outcome FROM action_log WHERE run_id = %s AND tool = 'release_report' "
        "ORDER BY event_id",
        (ctx.run_id,),
    ).fetchall()
    assert [o for (o,) in lines] == ["ok", "refused"]

    third = draft_with_section(ctx, learner, GOOD)
    pinned = ctx.conn.execute(
        "SELECT methodology_document_id FROM reports WHERE report_id = %s", (third,)
    ).fetchone()[0]
    assert pinned == newer, "a fresh draft is pinned to the methodology now in force"
    assert tool("check_citations")(ctx, report_id=third)["status"] == "ok"
    assert tool("release_report")(ctx, report_id=third)["status"] == "released"


def test_a_released_report_is_never_recomputed_under_a_newer_version(make_ctx, learner):
    ctx = make_ctx(APPROVER)
    base, _ = put_in_force(ctx, "methodology-v2.0.md")
    report_id = draft_with_section(ctx, learner, GOOD)
    tool("check_citations")(ctx, report_id=report_id)
    assert tool("release_report")(ctx, report_id=report_id)["status"] == "released"
    newer, _ = put_in_force(ctx, "methodology-v2.1-small.md")
    methodology.acknowledge(ctx.conn, newer, "Ada Teacher", "", ctx.database_url)
    state = ctx.conn.execute(
        "SELECT status, methodology_document_id FROM reports WHERE report_id = %s", (report_id,)
    ).fetchone()
    assert state == ("released", base), "it keeps the pin it was written under"
