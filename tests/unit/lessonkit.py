"""Building blocks for the lesson tests (T-060). No database, no network.

The rules here are written as inline YAML, in the shape the template files will have once the
lesson fields are merged into a new version (`wording` clauses with `banned_phrases`, a `lessons`
section, `min_cell_size` in `group_minimum`). The values are test values typed by hand; they are not
the school's. A test that needs a different value changes the content with `rules(mutate)`.
"""

import copy
import re

import yaml

from mdeis.lessons.edits import Edit
from mdeis.lessons.findings import finding_from_mapping
from mdeis.lessons.guard import Roster
from mdeis.lessons.parse import parse_lesson
from mdeis.lessons.rules import lesson_rules_from_content

RULES_YAML = """
foundation_areas: [literacy, numeracy]
calculations:
  - {id: CA-1, terms_per_year: 3, annual_score: mean_of_terms, text: "The mean of the terms."}
stage_expectations:
  - {id: ST-1, stage: "Form 1", area: literacy, range: [10, 14], text: "Form 1 literacy range."}
  - {id: ST-3, stage: "Form 3", area: literacy, range: [12, 16], text: "Form 3 literacy range."}
  - {id: ST-6, stage: "Form 3", area: numeracy, range: [12, 16], text: "Form 3 numeracy range."}
levels:
  - id: LV-1
    name: below_expected_range
    statement: "The average of the last 3 term scores is below the expected range for the stage."
    text: "Below the range."
  - id: LV-2
    name: within_expected_range
    statement: "The average of the last 3 term scores is within the expected range for the stage."
    text: "Within the range."
  - id: LV-3
    name: above_expected_range
    statement: "The average of the last 3 term scores is above the expected range for the stage."
    text: "Above the range."
  - id: LV-4
    name: insufficient_evidence
    statement: "There are too few term scores to say where the result sits against the range."
    text: "Too few scores."
wording:
  - id: WD-1
    banned: [weak, remedial, gifted, lazy, slow, failing, brilliant]
    text: "Describe the data. Never attach a label to the child."
  - id: WD-2
    banned_phrases: ["top group", "bottom group", "ability group", "fast track"]
    text: "No tracks and no ability grouping."
  - id: WD-3
    banned_phrases: ["career path", "subject choice"]
    text: "No pathway and no subject choice."
group_minimum:
  - {id: GM-1, min_group_size: 10, text: "Groups smaller than 10 are never shown."}
  - {id: GM-2, min_cell_size: 5, text: "A count above zero and below 5 is hidden."}
lessons:
  - id: LS-1
    max_notes_chars: 8000
    max_edits: 12
    max_edit_chars: 1500
    text: "Size limits for lesson notes and edits."
"""

_CONTENT = yaml.safe_load(RULES_YAML)
VERSION = "t-lessons"


def content(mutate=None) -> dict:
    out = copy.deepcopy(_CONTENT)
    if mutate:
        mutate(out)
    return out


def rules(mutate=None):
    return lesson_rules_from_content(content(mutate), VERSION, "a" * 64)


def limits(**values):
    """A `mutate` that changes the LS-1 limits."""

    def change(c):
        c["lessons"][0].update(values)

    return change


# --------------------------------------------------------------------------- the lesson
OBJECTIVE = "Learners explain the main idea of a short text."

LESSON = """Objective: Learners explain the main idea of a short text.

## Starter
Read the short passage aloud to the class. Ask what the passage is about.

## Main activity
Learners read the passage in pairs and underline the key sentence.
Then they write one sentence that sums up the passage.

## Plenary
Share the sentences and check them against the passage.
"""

A_STARTER = "Read the short passage aloud to the class."
A_ASK = "Ask what the passage is about."
A_PAIRS = "Learners read the passage in pairs and underline the key sentence."
A_SUM = "Then they write one sentence that sums up the passage."
A_PLENARY = "Share the sentences and check them against the passage."


def lesson(text=LESSON, objective=OBJECTIVE):
    return parse_lesson(text, objective)


# --------------------------------------------------------------------------- findings
FINDING_ID = "CF-a1b2c3d4"
OTHER_ID = "CF-0f0f0f0f"

# Hand-worked class: 22 learners, 14 below, 6 within, 2 above. 14 + 6 + 2 = 22.
BINS = [
    {"bin": "below_expected_range", "shown": True, "count": 14},
    {"bin": "within_expected_range", "shown": True, "count": 6},
    {"bin": "above_expected_range", "shown": True, "count": 2},
    {"bin": "not_enough_evidence", "shown": True, "count": 0},
    {"bin": "blocked", "shown": True, "count": 0},
]


def finding_mapping(**over) -> dict:
    base = {
        "finding_id": FINDING_ID,
        "status": "ok",
        "stage": "Form 3",
        "area": "literacy",
        "term": 2,
        "template_version": VERSION,
        "group_size": 22,
        "bins": copy.deepcopy(BINS),
        "clause_ids": ["SK-1", "ST-3", "TR-1", "CA-5", "GM-1", "GM-2", "GM-3"],
    }
    base.update(over)
    return base


def finding(**over):
    return finding_from_mapping(finding_mapping(**over))


def with_below(count, shown=True, **over):
    """A finding whose below-the-range bin has the given count (the other bins are left alone)."""
    bins = copy.deepcopy(BINS)
    bins[0] = {"bin": "below_expected_range", "shown": shown, "count": count if shown else None}
    return finding(bins=bins, **over)


# --------------------------------------------------------------------------- edits
def edit(
    id="e1",
    type="reword",
    anchor=A_ASK,
    replacement="Ask what the passage tells us and why.",
    finding_ids=(FINDING_ID,),
    suggestion="Give learners a question that asks for a reason.",
    resource_ids=(),
):
    return Edit(
        id=id,
        type=type,
        anchor_quote=anchor,
        replacement=replacement,
        finding_ids=tuple(finding_ids),
        resource_ids=tuple(resource_ids),
        suggestion=suggestion,
    )


def add_after(anchor, text, id="e1", type="add_example", **over):
    return edit(id=id, type=type, anchor=anchor, replacement=anchor + text, **over)


def edit_mapping(**over) -> dict:
    base = {
        "id": "e1",
        "type": "reword",
        "anchor_quote": A_ASK,
        "replacement": "Ask what the passage tells us and why.",
        "finding_ids": [FINDING_ID],
        "resource_ids": [],
        "suggestion": "Give learners a question that asks for a reason.",
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- the roster
def roster():
    return Roster(names=("Amara Nfor", "Joy Mbeki", "Zoë Tanda"), refs=("OCC/2025/014",))


def digits_in(text):
    return re.findall(r"[0-9]+", text)
