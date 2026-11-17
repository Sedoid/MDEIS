"""The PDF link under a chat answer (T-076). The model may call `export_report`, but it never writes
a link. After the model has answered, `export_reply` adds one line, built by code from the portal
address setting and a report number the tool returned in this run, and only when the tool said `ok`.
Any link-like text the model writes is removed, as chart_reply removes an invented image.

Scripted fake model and servers: no network, no key, no database.

What is held here: the line is exactly `PDF of report R-000001: [Download](/portal/#/files?report=
R-000001)` for an `ok` result of that tool with a well-formed number, and for nothing else (a
refused or failed call, another tool, a malformed number); the address comes from PORTAL_URL,
and an unsafe value falls back to the default; links, anchors, bare addresses, script and data
addresses and portal paths in the model's words are replaced by a plain notice (never the label
or the target); an image is still handled by chart_reply; the model is never shown the link or the
address; a link in an earlier turn is not sent back; both engines (linear and graph) give the same
line in the same place (under the words, above the sources footer); and the tool is on the
allow-list and nothing else changed there.
"""

import asyncio
import json
from pathlib import Path

import pytest
from orchestrator_fakes import MDEIS_LISTING, FakeModel, FakeServer, ask, say, spec

from orchestrator import chart_reply, export_reply, graph, loop, tools
from orchestrator.config import load_settings

ROOT = Path(__file__).resolve().parents[2]
ENV = {"ORCHESTRATOR_API_KEY": "k", "OPENROUTER_API_KEY": "x", "OPENROUTER_MODEL": "m"}
SETTINGS = load_settings({**ENV, "ORCHESTRATOR_MODE": "graph"})
TOKEN = "signed.token.value"
GONE = "[a link was removed]"
OK = {"status": "ok", "report_id": "R-000001", "file_name": "R-000001.pdf"}
LINE = "PDF of report R-000001: [Download](/portal/#/files?report=R-000001)"
ASK = ("export_report", {"report_id": "R-000001"})
USER = [{"role": "user", "content": "Please give me the PDF of report R-000001"}]


def run_loop(messages, model, server, settings=SETTINGS):
    async def collect():
        return [e async for e in loop.run(messages=messages, token=TOKEN, run_id="RUN-1",
                                          settings=settings, model=model,
                                          servers={tools.MDEIS: server})]

    return asyncio.run(collect())


def run_graph(messages, model, server, settings=SETTINGS):
    async def collect():
        return [e async for e in graph.run(messages=messages, token=TOKEN, run_id="RUN-1",
                                           settings=settings, model=model,
                                           servers={tools.MDEIS: server}, engine="plain")]

    return asyncio.run(collect())


RUNNERS = pytest.mark.parametrize("runner", [run_loop, run_graph], ids=["linear", "graph"])


def final(events):
    return next(e["data"] for e in events if e["event"] == "final")


def seen_by_model(model):
    return " ".join(str(m) for call in model.seen for m in call[0])


# --------------------------------------------------------------------------- the settings
def test_the_portal_address_defaults_to_the_portal_path():
    assert export_reply.DEFAULT_PORTAL_URL == "/portal/"
    assert load_settings({}).portal_url == "/portal/"
    assert load_settings({"PORTAL_URL": ""}).portal_url == "/portal/"


def test_the_portal_address_comes_from_the_environment_and_ends_in_a_slash():
    assert load_settings({"PORTAL_URL": "https://school.example.org/portal/"}).portal_url == (
        "https://school.example.org/portal/")
    assert load_settings({"PORTAL_URL": "/portal"}).portal_url == "/portal/"


@pytest.mark.parametrize("given,expected", [
    (None, "/portal/"),
    ("", "/portal/"),
    ("   ", "/portal/"),
    ("/portal/", "/portal/"),
    ("/portal", "/portal/"),
    ("/", "/"),
    ("https://school.example.org/portal/", "https://school.example.org/portal/"),
    ("https://school.example.org:8443/p", "https://school.example.org:8443/p/"),
    ("http://127.0.0.1:8300/portal/", "http://127.0.0.1:8300/portal/"),
    # unsafe or odd values fall back to the default, never into a link
    ("javascript:alert(1)", "/portal/"),
    ("//evil.example/", "/portal/"),
    ("/portal/x y", "/portal/"),
    ("/portal/)", "/portal/"),
    ("/portal/?a=1", "/portal/"),
    ("/portal/#x", "/portal/"),
    ("ftp://h.example/p/", "/portal/"),
    ("https://h.example/\np/", "/portal/"),
    (" /portal/ \n", "/portal/"),  # outer whitespace is trimmed
    ('/portal/"onclick', "/portal/"),
    ("data:text/html,x", "/portal/"),
    ("https://user@h.example/p/", "/portal/"),
])
def test_the_address_is_made_safe_or_replaced_by_the_default(given, expected):
    assert export_reply.portal_base(given) == expected


# --------------------------------------------------------------------------- when a line is made
@pytest.mark.parametrize("name,result,expected", [
    ("export_report", OK, True),
    ("export_report", {"status": "ok", "report_id": "R-123456789"}, True),
    ("make_chart", OK, False),
    ("release_report", OK, False),
    ("export_report", {"status": "refused", "reason": "report_not_released"}, False),
    ("export_report", {"status": "refused", "reason": "report_not_found",
                       "report_id": "R-000001"}, False),
    ("export_report", {"status": "error", "report_id": "R-000001"}, False),
    ("export_report", {"status": "not_drawn", "report_id": "R-000001"}, False),
    ("export_report", {"report_id": "R-000001"}, False),
    ("export_report", {"status": "ok"}, False),
    ("export_report", {"status": "ok", "report_id": None}, False),
    ("export_report", {"status": "ok", "report_id": 1}, False),
    ("export_report", {"status": "ok", "report_id": "R-12"}, False),
    ("export_report", {"status": "ok", "report_id": "r-000001"}, False),
    ("export_report", {"status": "ok", "report_id": "R-000001\n"}, False),
    ("export_report", {"status": "ok", "report_id": "../R-000001"}, False),
    ("export_report", {"status": "ok", "report_id": "R-000001) [x](https://e.example"}, False),
    ("export_report", ["ok"], False),
    ("export_report", None, False),
    ("export_report", "ok", False),
])
def test_only_an_ok_result_of_the_export_tool_with_a_well_formed_number_counts(
    name, result, expected
):
    assert export_reply.is_export(name, result) is expected


def test_the_tool_name_is_the_servers():
    assert export_reply.TOOL == "export_report"


def test_found_numbers_are_kept_in_order_and_once():
    found = []
    found = export_reply.add_export(found, "export_report", OK)
    found = export_reply.add_export(found, "export_report", {**OK, "report_id": "R-000002"})
    found = export_reply.add_export(found, "export_report", OK)
    found = export_reply.add_export(found, "export_report", {"status": "refused"})
    found = export_reply.add_export(found, "make_chart", {**OK, "report_id": "R-000003"})
    assert found == ["R-000001", "R-000002"]


def test_adding_to_the_found_numbers_does_not_change_the_list_it_was_given():
    before = ["R-000001"]
    export_reply.add_export(before, "export_report", {**OK, "report_id": "R-000002"})
    assert before == ["R-000001"]


# --------------------------------------------------------------------------- the line itself
def test_the_line_is_a_markdown_link_to_the_files_page_with_the_report_in_the_address():
    assert export_reply.link_line(["R-000001"], "/portal/") == LINE


def test_the_line_uses_the_address_setting():
    assert export_reply.link_line(["R-000001"], "https://school.example.org/portal/") == (
        "PDF of report R-000001: "
        "[Download](https://school.example.org/portal/#/files?report=R-000001)")


def test_an_unsafe_address_setting_gives_the_default_in_the_line():
    assert export_reply.link_line(["R-000001"], "javascript:alert(1)") == LINE


def test_one_line_for_each_number_each_a_paragraph_of_its_own():
    assert export_reply.link_line(["R-000001", "R-000002"], "/portal/") == (
        LINE + "\n\nPDF of report R-000002: [Download](/portal/#/files?report=R-000002)")


def test_no_numbers_no_line():
    assert export_reply.link_line([], "/portal/") == ""


def test_a_number_that_does_not_match_is_never_put_into_a_line_even_if_handed_in():
    assert export_reply.link_line(["R-12", "R-000001) [x](https://e.example", "R-000001\n"],
                                  "/portal/") == ""


# --------------------------------------------------------------------------- links the model writes
@pytest.mark.parametrize("words,expected", [
    ("Download: [PDF](https://evil.example/x.pdf) now", f"Download: {GONE} now"),
    ("See https://evil.example/a?b=1 for it.", f"See {GONE} for it."),
    ("(see https://x.example/a).", f"(see {GONE})."),
    ('<a href="/portal/#/files?report=R-000001">PDF</a> is ready',
     f"{GONE} is ready"),
    ("<A HREF='http://e.example'>x</A>", GONE),
    ("<https://evil.example>", GONE),
    ("Open /portal/#/files?report=R-000001 to get it", f"Open {GONE} to get it"),
    ("Go to #/files?report=R-000001 now", f"Go to {GONE} now"),
    ("[click](javascript:alert(1))", GONE),
    ("[a](x) and [b](y)", f"{GONE} and {GONE}"),
    ("Open data:text/html;base64,PHNjcmlwdD4= please", f"Open {GONE} please"),
    ("mailto:a@b.example", GONE),
    ("javascript:alert(1)", GONE),
    ("www.evil.example/x", GONE),
    ("[1]: https://evil.example/doc", f"[1]: {GONE}"),
    ("Take it from [here](/api/reports/R-000001/pdf)", f"Take it from {GONE}"),
])
def test_a_link_the_model_writes_is_replaced_by_a_plain_notice(words, expected):
    assert export_reply.strip_links(words) == expected


@pytest.mark.parametrize("words", [
    "Reading is steady [A-000001] (see [TR-2]); the ratio is 3:1 at 10:30.",
    "Note: the data: here is fine. Data: ok. The file: R-000001.pdf is not a link.",
    "Report R-000001 is released and the approver is Ada.",
    "Mathematics moved from 14.00 to 16.00 [A-000007].",
    "A profile: x and a bifile:y are not links either.",
])
def test_ordinary_words_are_left_exactly_as_written(words):
    assert export_reply.strip_links(words) == words


def test_stripping_twice_gives_the_same_as_stripping_once():
    words = ("[x](https://e.example) https://e.example/a "
             "/portal/#/files?report=R-000001 <a href=y>z</a>")
    once = export_reply.strip_links(words)
    assert export_reply.strip_links(once) == once


def test_the_notice_is_not_itself_a_link():
    assert export_reply.strip_links(GONE) == GONE


def test_a_non_string_is_returned_as_it_is():
    assert export_reply.strip_links(None) is None


# --------------------------------------------------------------------------- compose
def test_compose_puts_the_line_under_the_words_and_takes_the_models_link_out():
    out = chart_reply.compose(
        "Report R-000001 is released. [Download](https://evil.example/x)", [],
        exports=["R-000001"])
    assert out == f"Report R-000001 is released. {GONE}\n\n{LINE}"


def test_compose_puts_the_line_above_the_sources_footer():
    sources = [{"document_id": "D-000001", "file": "a form1.pdf", "academic_year": "2022/2023"}]
    out = chart_reply.compose("Steady.", [], sources, exports=["R-000001"])
    assert out == f"Steady.\n\n{LINE}\n\n---\n\n*Sources (report cards): a form1.pdf.*"


def test_compose_adds_no_line_without_a_number_and_still_takes_links_out():
    assert chart_reply.compose("Here: [x](https://e.example)", []) == f"Here: {GONE}"
    assert chart_reply.compose("Fine.", [], exports=[]) == "Fine."


def test_compose_leaves_the_existing_output_unchanged_when_no_link_is_involved():
    words = "English is steady [A-000001]."
    assert chart_reply.compose(words, []) == words


def test_an_image_is_still_handled_as_an_image_before_links_are_taken_out():
    out = chart_reply.compose("![x](https://e.example/a.png) and [y](https://e.example)", [])
    assert out == f"{chart_reply.SEEN} and {GONE}"


def test_the_line_built_by_code_is_not_taken_out_of_the_answer():
    out = chart_reply.compose("Done.", [], exports=["R-000001"], portal_url="/portal/")
    assert out.endswith(LINE) and GONE not in out


def test_compose_uses_the_portal_address_it_is_given():
    out = chart_reply.compose("Done.", [], exports=["R-000001"],
                              portal_url="https://school.example.org/portal/")
    assert out.endswith("[Download](https://school.example.org/portal/#/files?report=R-000001)")


# --------------------------------------------------------------------------- the whole run
@RUNNERS
def test_an_ok_export_adds_the_line_under_the_models_words(runner):
    server = FakeServer(MDEIS_LISTING, {"export_report": OK})
    model = FakeModel(ask(*ASK), say("Report R-000001 is released, so a PDF is available."))
    out = final(runner(USER, model, server))
    assert out["answer"] == f"Report R-000001 is released, so a PDF is available.\n\n{LINE}"
    assert out["tools"] == ["export_report"]
    assert server.calls == [("export_report", {"report_id": "R-000001"})]


@RUNNERS
@pytest.mark.parametrize("result", [
    {"status": "refused", "reason": "report_not_released",
     "detail": "Only a released report can be exported."},
    {"status": "refused", "reason": "report_not_found", "detail": "There is no such report."},
    {"status": "error", "detail": "the tool failed (RuntimeError)"},
], ids=["draft", "unknown", "error"])
def test_a_refused_or_failed_export_adds_no_line(runner, result):
    server = FakeServer(MDEIS_LISTING, {"export_report": result})
    model = FakeModel(ask(*ASK), say("That report is not released yet."))
    assert final(runner(USER, model, server))["answer"] == "That report is not released yet."


@RUNNERS
def test_a_link_the_model_writes_with_no_export_call_is_removed_and_no_line_is_added(runner):
    model = FakeModel(say("Here you go: [Download](https://evil.example/R-000001.pdf)"))
    answer = final(runner(USER, model, FakeServer(MDEIS_LISTING)))["answer"]
    assert answer == f"Here you go: {GONE}"
    assert "evil.example" not in answer and "Download" not in answer


@RUNNERS
def test_with_an_ok_export_a_link_the_model_wrote_is_replaced_and_only_the_system_link_stays(
    runner,
):
    server = FakeServer(MDEIS_LISTING, {"export_report": OK})
    words = ("Released. Get it at https://evil.example/R-000001.pdf or "
             "[here](/portal/#/files?report=R-999999).")
    model = FakeModel(ask(*ASK), say(words))
    answer = final(runner(USER, model, server))["answer"]
    assert answer == f"Released. Get it at {GONE} or {GONE}.\n\n{LINE}"
    assert "R-999999" not in answer and "evil.example" not in answer


@RUNNERS
def test_the_number_in_the_link_is_the_one_the_tool_returned_not_the_one_the_model_wrote(runner):
    returned = {"status": "ok", "report_id": "R-000007", "file_name": "R-000007.pdf"}
    server = FakeServer(MDEIS_LISTING, {"export_report": returned})
    model = FakeModel(ask("export_report", {"report_id": "R-000001"}), say("Done."))
    answer = final(runner(USER, model, server))["answer"]
    assert answer.endswith("[Download](/portal/#/files?report=R-000007)")
    assert "R-000001" not in answer


@RUNNERS
def test_a_number_the_model_made_up_gets_no_line_unless_the_tool_returned_it(runner):
    model = FakeModel(say("The PDF of R-000042 is at [Download](/portal/#/files?report=R-000042)"))
    answer = final(runner(USER, model, FakeServer(MDEIS_LISTING)))["answer"]
    assert "R-000042" in answer and "/portal/" not in answer and "[Download]" not in answer


@RUNNERS
def test_the_model_is_never_shown_the_link_or_the_address(runner):
    server = FakeServer(MDEIS_LISTING, {"export_report": OK})
    model = FakeModel(ask(*ASK), say("Done."))
    runner(USER, model, server)
    seen = seen_by_model(model)
    assert "/portal/" not in seen and "#/files" not in seen and "[Download]" not in seen
    told = model.seen[1][0][-1]
    assert told["role"] == "tool" and json.loads(told["content"]) == OK


@RUNNERS
def test_the_address_setting_is_used_in_both_engines(runner):
    settings = load_settings({**ENV, "ORCHESTRATOR_MODE": "graph",
                              "PORTAL_URL": "https://school.example.org/portal/"})
    server = FakeServer(MDEIS_LISTING, {"export_report": OK})
    model = FakeModel(ask(*ASK), say("Done."))
    answer = final(runner(USER, model, server, settings))["answer"]
    assert answer.endswith("[Download](https://school.example.org/portal/#/files?report=R-000001)")


@RUNNERS
def test_two_reports_give_two_lines_and_the_same_report_twice_gives_one(runner):
    other = {"status": "ok", "report_id": "R-000002", "file_name": "R-000002.pdf"}
    server = FakeServer(MDEIS_LISTING, {"export_report": [OK, OK, other]})
    model = FakeModel(ask(*ASK, id="c1"), ask(*ASK, id="c2"),
                      ask("export_report", {"report_id": "R-000002"}, id="c3"), say("Done."))
    answer = final(runner(USER, model, server))["answer"]
    second = "PDF of report R-000002: [Download](/portal/#/files?report=R-000002)"
    assert answer == f"Done.\n\n{LINE}\n\n{second}"


@RUNNERS
def test_the_line_sits_above_the_sources_footer_in_both_engines(runner):
    sourced = {"status": "ok", "trend": "steady", "sources": [
        {"document_id": "D-000001", "file": "a form1.pdf", "academic_year": "2022/2023",
         "stage": "Form 1"}]}
    server = FakeServer(MDEIS_LISTING, {"export_report": OK, "compute_trend": sourced})
    model = FakeModel(ask("compute_trend", {"learner_id": "L-1", "skill": "Mathematics"}),
                      ask(*ASK), say("Steady."))
    answer = final(runner(USER, model, server))["answer"]
    assert answer == f"Steady.\n\n{LINE}\n\n---\n\n*Sources (report cards): a form1.pdf.*"


def test_a_run_that_hits_the_step_limit_still_adds_the_line_for_what_was_exported():
    settings = load_settings({**ENV, "ORCHESTRATOR_MAX_STEPS": "1"})
    server = FakeServer(MDEIS_LISTING, {"export_report": OK})
    model = FakeModel(ask(*ASK))
    out = final(run_loop(USER, model, server, settings))
    assert out["incomplete"] is True and out["answer"].endswith(LINE)


# --------------------------------------------------------------------------- the history
@RUNNERS
def test_a_link_from_an_earlier_turn_is_not_sent_to_the_model_again(runner):
    history = [
        {"role": "user", "content": "PDF of R-000001 please"},
        {"role": "assistant", "content": f"It is released.\n\n{LINE}"},
        {"role": "user", "content": "Thanks. What does the rulebook say about a move?"},
    ]
    model = FakeModel(say("A move is described in the rulebook."))
    runner(history, model, FakeServer(MDEIS_LISTING))
    sent = " ".join(m["content"] or "" for m in model.seen[0][0])
    assert "/portal/" not in sent and "#/files" not in sent and "[Download]" not in sent
    assert f"PDF of report R-000001: {GONE}" in sent


def test_a_persons_own_message_is_sent_as_they_wrote_it():
    said = "Please look at https://example.org/policy for the school rule"
    history = [{"role": "user", "content": said}]
    kept = loop.conversation_from(history, TOKEN)
    assert kept == history


# --------------------------------------------------------------------------- the allow-list
def test_the_tool_is_on_the_allow_list_after_release_report_and_the_rest_is_as_it_was():
    assert tools.MDEIS_TOOLS == (
        "ingest_assessments", "resolve_issue", "compute_trend", "list_measures",
        "compute_measure", "get_criterion", "draft_summary", "check_citations", "make_chart",
        "release_report", "export_report",
    )
    assert len(set(tools.MDEIS_TOOLS)) == 11


@pytest.mark.parametrize("view", [False, True], ids=["view off", "view on"])
def test_the_model_is_offered_export_report_and_the_count_is_fifteen_or_sixteen(view):
    pg_names = load_settings({}).pg_tools
    listed = {tools.MDEIS: [spec(n) for n in (*tools.MDEIS_TOOLS, tools.CLASS_VIEW_TOOL)],
              tools.PG: [spec(n) for n in pg_names]}
    definitions, owner, dropped = tools.bind(listed, pg_names, view)
    offered = {d["function"]["name"] for d in definitions}
    assert "export_report" in offered and owner["export_report"] == tools.MDEIS
    assert len(offered) == 4 + 11 + (1 if view else 0)
    assert dropped == []


def test_the_tool_is_dropped_if_the_server_ever_gave_it_an_approver_or_user_argument():
    for extra in ("approver", "user", "approver_name", "by"):
        listing = [spec("export_report", {"report_id": {}, extra: {}})]
        definitions, owner, dropped = tools.bind({tools.MDEIS: listing}, ())
        assert dropped == ["export_report"] and definitions == [] and owner == {}


def test_no_tool_that_returns_a_file_is_on_the_allow_list():
    for name in (*tools.MDEIS_TOOLS, tools.CLASS_VIEW_TOOL):
        assert "pdf" not in name and "download" not in name and "file" not in name, name


# --------------------------------------------------------------------------- the code's reach
def test_the_orchestrator_names_the_tool_but_not_the_export_code_or_the_download_route():
    for path in (ROOT / "src" / "orchestrator").rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            text = path.read_text("utf-8", errors="ignore")
            for word in ("report_pdf", "export_released_report", "render_report_pdf",
                         "mdeis.export", "/pdf", "human/reports", "released_by"):
                assert word not in text, (path, word)


def test_the_export_reply_module_has_no_model_client_no_network_and_no_secret():
    text = (ROOT / "src" / "orchestrator" / "export_reply.py").read_text("utf-8")
    for word in ("httpx", "requests", "urllib", "openai", "APPROVER_TOKEN_SECRET", "approver"):
        assert word not in text, word
