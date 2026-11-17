"""`export_report` in the Langfuse trace (T-076). Nothing special: it is a `tool` span, with the
tool name, the outcome and the time, and no argument and no result by default. Uses the fake
Langfuse of `test_tracing.py` (httpx.MockTransport), so there is no network.
"""

from orchestrator_fakes import FakeModel, ask, say
from test_tracing import Wire, attrs, chat, tracer_for

IO_IN, IO_OUT = "langfuse.observation.input", "langfuse.observation.output"
OK = {"status": "ok", "report_id": "R-000731", "file_name": "R-000731.pdf"}
REFUSED = {"status": "refused", "reason": "report_not_released",
           "detail": "Only a released report can be exported."}


def run(results, **env):
    wire = Wire()
    model = FakeModel(ask("export_report", {"report_id": "R-000731"}), say("Done."))
    chat(tracer_for(wire, **env), model, results={"export_report": results},
         text="PDF of report R-000731 please")
    return wire


def test_the_call_is_an_ordinary_tool_span_with_its_name_and_outcome():
    wire = run(OK)
    [span] = wire.tool_spans()
    mine = attrs(span)
    assert span["name"] == "export_report"
    assert mine["langfuse.observation.type"] == "tool"
    assert mine["langfuse.observation.metadata.outcome"] == "ok"
    assert mine["langfuse.observation.metadata.server"] == "mdeis"


def test_a_refusal_is_a_warning_span():
    [span] = run(REFUSED).tool_spans()
    mine = attrs(span)
    assert mine["langfuse.observation.metadata.outcome"] == "refused"
    assert mine["langfuse.observation.level"] == "WARNING"


def test_by_default_the_span_has_no_argument_and_no_result_and_the_number_is_not_sent():
    wire = run(OK)
    [span] = wire.tool_spans()
    assert IO_IN not in attrs(span) and IO_OUT not in attrs(span)
    assert "R-000731" not in wire.text() and "R-000731.pdf" not in wire.text()


def test_content_capture_alone_does_not_put_the_tool_data_on_the_span():
    wire = run(OK, LANGFUSE_CAPTURE_CONTENT="true")
    [span] = wire.tool_spans()
    assert IO_IN not in attrs(span) and IO_OUT not in attrs(span)
    assert "R-000731.pdf" not in wire.text()


def test_the_result_never_holds_file_bytes_so_the_tool_data_setting_cannot_send_a_file():
    wire = run(OK, LANGFUSE_CAPTURE_TOOL_IO="true")
    [span] = wire.tool_spans()
    mine = attrs(span)
    assert '"report_id": "R-000731"' in mine[IO_OUT]
    assert "%PDF" not in wire.text() and '"pdf"' not in mine[IO_OUT]
