"""What the system prompt tells the model about a PDF of a released report (T-076).

Earlier the section said the model cannot make a PDF and sent people to the Files page. The model
now has the `export_report` tool, so the section says how to use it, and how to answer when the
report is not released. The model never writes a link: the system puts the download link under the
answer. Read from the file; no model is called.
"""

import re
from pathlib import Path

import pytest

from orchestrator import loop

ROOT = Path(__file__).resolve().parents[2]
PROMPT = (ROOT / "src" / "orchestrator" / "prompts" / "system.md").read_text("utf-8")
HEADING = "## PDF of a released report"
BANNED = ("weak", "remedial", "gifted", "lazy", "slow", "failing", "brilliant", "rank",
          "ranking", "pathway", "career", "label")  # fmt: skip


def section():
    start = PROMPT.index(HEADING)
    end = PROMPT.find("\n## ", start + len(HEADING))
    return PROMPT[start: end if end != -1 else len(PROMPT)]


def test_the_section_exists_once_and_sits_between_how_to_work_and_charts():
    assert PROMPT.count(HEADING) == 1
    assert PROMPT.index("## How to work") < PROMPT.index(HEADING) < PROMPT.index("## Charts")


@pytest.mark.parametrize("old", [
    "You cannot make, fetch or send a PDF, and no tool does",
    "from the Files page of the portal",
    "by typing the report number",
    "Do not promise a file, a link or a time",
])
def test_the_old_claims_are_gone(old):
    assert old not in PROMPT, old


@pytest.mark.parametrize("phrase", [
    "call `export_report`",
    "a report number the person gave",
    "`draft_summary`",
    "`release_report`",
    "in this conversation",
    "Never invent a report number",
    "If the report is not released, say so plainly",
    "the approver releases it",
    "The system puts the download link under your answer",
    "never write a link, a path or a file name",
    "You cannot make or send a PDF yourself",
])
def test_the_section_says_how_to_use_the_tool_and_what_never_to_do(phrase):
    assert phrase in section(), phrase


def test_the_section_does_not_promise_a_file_for_a_draft():
    assert "Do not promise a file for a draft" in section()


def test_the_section_holds_no_address_no_link_and_no_wording_about_a_learner():
    text = section()
    for bad in ("/portal", "#/files", "[Download]", "http", "/pdf", ".pdf"):
        assert bad not in text, bad
    words = set(re.findall(r"[a-z]+", text.lower()))
    assert not words & set(BANNED), words & set(BANNED)


def test_no_part_of_the_prompt_shows_the_model_a_link_or_the_portal_address():
    for part in ("system.md", "graph_note.md", "class_view_on.md", "class_view_off.md"):
        text = (ROOT / "src" / "orchestrator" / "prompts" / part).read_text("utf-8")
        assert "/portal/" not in text and "#/files" not in text and "[Download]" not in text, part


def test_the_prompt_the_loop_builds_carries_the_section_in_every_mode():
    for graph in (False, True):
        for view in (False, True):
            assert HEADING in loop.system_prompt(graph=graph, class_view=view)
