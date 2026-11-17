"""The citation check and the draft, for measure results (T-072). No database.

A finding may cite a measure result exactly as it cites a trend: the record IDs, the clause IDs and
the measure's own ID must have been returned by a tool in the same run, and every number must be one
the findings state. The exact form of a number ("61/4"), the values the template gave the function
and the hashes of the code are not numbers a draft may state, so they open no number. The
integration side (a real run, a real draft) is in test_measure_tools.py.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from mdeis.tools import citations, draft


def problems(body, ids, numbers=(), measures=None, clauses=None):
    return citations.problems_in(body, set(ids), set(numbers), [], clauses, None, measures)


def reasons(found):
    return [(p["reason"], p["detail"]) for p in found]


# ----------------------------------------------------------------------- the marker for a measure
def test_a_measure_the_run_returned_and_the_draft_is_built_from_can_be_cited():
    found = problems("The change was 11 [MS-0001].", {"MS-0001"}, {Decimal(11)}, {"MS-0001"})
    assert found == []


def test_a_measure_no_tool_returned_in_the_run_is_refused_with_its_own_reason():
    found = problems("The change was 11 [MS-0001].", {"A-000001"}, {Decimal(11)}, {"MS-0001"})
    assert reasons(found) == [("unknown_measure", "MS-0001")]


def test_a_measure_returned_in_the_run_but_not_among_the_findings_is_refused():
    found = problems("The change was 11 [MS-0002].", {"MS-0002"}, {Decimal(11)}, {"MS-0001"})
    assert reasons(found) == [("unknown_measure", "MS-0002")]


def test_without_the_list_of_findings_only_what_the_run_returned_is_checked():
    assert problems("It was so [MS-0001].", {"MS-0001"}, (), None) == []
    assert reasons(problems("It was so [MS-0001].", set(), (), None)) == [
        ("unknown_measure", "MS-0001")
    ]


def test_a_measure_marker_is_never_judged_as_a_clause_or_a_record_or_a_section():
    found = problems("It was so [MS-0001].", {"MS-0001"}, (), {"MS-0001"}, clauses={"TR-1"})
    assert found == [], "MS-0001 is not in the clauses of the rulebook, and need not be"


def test_the_other_markers_are_judged_as_before():
    found = problems(
        "One [A-000001]. Two [TR-1]. Three [M-6.4]. Four [A-000009]. Five [TR-9].",
        {"A-000001", "TR-1"},
        (),
        set(),
        clauses={"TR-1"},
    )
    assert reasons(found) == [
        ("unknown_section", "M-6.4"),
        ("unknown_record", "A-000009"),
        ("unknown_clause", "TR-9"),
    ]


def test_the_measure_id_is_not_read_as_a_number():
    assert citations.numbers_in("Measure MS-0001 gave 11") == {Decimal(11)}


def test_the_check_reads_a_measure_result_as_a_finding_source():
    assert "compute_measure" in citations.RETURNED_BY
    assert "compute_measure" in draft.FINDING_TOOLS
    assert {"compute_trend", "ingest_assessments", "get_criterion"} <= set(citations.RETURNED_BY)
    assert set(draft.FINDING_TOOLS) == {
        "compute_trend", "ingest_assessments", "get_criterion", "compute_measure",
    }  # fmt: skip


# ----------------------------------------------------------------------- what opens a number
class Rows:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, *args, **kwargs):
        return self

    def fetchall(self):
        return self.rows


RESULT = {
    "status": "ok",
    "measure_id": "MS-0001",
    "measure_version": 1,
    "learner_id": "L-0001",
    "code_sha256": "9" * 31 + "1" * 33,
    "section_sha256": "7" * 64,
    "clause_ids": ["MP-1", "TR-1"],
    "record_ids": ["A-000001", "A-000002"],
    "values_used": {"MP-1": "3/5", "TR-1.window_terms": "3"},
    "result": {
        "fields": {
            "change": {"value": "15.25", "exact": "61/4"},
            "scores_used": {"value": "2.00", "exact": "2"},
        }
    },
    "rules": [
        {"id": "MP-1", "text": "The pattern is moved up when the change is 10 points or more."}
    ],
    "result_hash": "4" * 64,
}


def numbers_for(result):
    ctx = SimpleNamespace(conn=Rows([("compute_measure", result)]))
    return citations.returned_in_run(ctx, "RUN-1", [result])


def test_ids_come_from_every_measure_result_and_numbers_only_from_what_it_states():
    ids, numbers = numbers_for(RESULT)
    assert ids == {"MS-0001", "MP-1", "TR-1", "A-000001", "A-000002"}
    # The value 15.25, the count 2.00 (the same as 2), the version 1, and the numbers in the wording
    # of the rule it applied: 10. Not 61 or 4 (the exact form), not 3 or 5 (the template values in
    # `values_used`), and none of the digits inside the two hashes.
    assert numbers == {Decimal("15.25"), Decimal(2), Decimal(1), Decimal(10)}


def test_a_number_that_is_only_in_the_exact_form_or_the_template_values_is_not_a_finding():
    ids, numbers = numbers_for(RESULT)
    text = "It was 61 points of 4 [MS-0001]. The value 3 was used [MP-1]."
    found = citations.problems_in(text, ids, numbers, [], None, None, {"MS-0001"})
    assert sorted(d for _, d in reasons(found)) == ["3", "4", "61"]


@pytest.mark.parametrize(
    "key", ["result_hash", "exact", "values_used", "code_sha256", "section_sha256"]
)
def test_these_keys_open_no_number(key):
    assert key in citations.NOT_COUNTED


def test_a_refused_result_is_not_a_source_because_only_results_that_are_ok_are_read():
    # The query asks for outcome = 'ok'; this checks the question, since the rows are a stand-in.
    import inspect

    source = inspect.getsource(citations.returned_in_run)
    assert "outcome = 'ok'" in source
    assert "outcome = 'ok'" in inspect.getsource(draft.findings_for)
