"""What a chart shows when it is drawn from approved measure results (T-072, widening make_chart).

Pure functions in `mdeis.core.chartdata`, no database. A measure line is drawn only from a result a
tool returned in the run: its value is the text the tool wrote, passed through unchanged. A value
that does not fit the rulebook's score axis is left off and listed, never clipped or changed. The
existing subject and area charts are tested in test_chartdata.py and are not touched here.
"""

from fractions import Fraction
from types import SimpleNamespace

import pytest

from mdeis.core import chartdata

RULES = SimpleNamespace(scale_max=Fraction(100), version="baobab-mp")


def number(value="11.00", exact="11", mid="MS-0001", name="Change over the window", **over):
    out = {
        "status": "ok", "measure_id": mid, "measure_version": 1, "name": name,
        "output_kind": "number", "learner_id": "L-0001", "template_version": "baobab-mp",
        "clause_ids": ["MP-1", "TR-1"], "record_ids": ["A-000001", "A-000003"],
        "code_sha256": "a" * 64, "result_hash": "b" * 64,
        "result": {"fields": {"change": {"value": value, "exact": exact}}},
    }  # fmt: skip
    out.update(over)
    return out


def point(year, term, value, exact, ids):
    return {"year": year, "term": term, "stage": "Form 1", "value": value, "exact": exact,
            "record_ids": ids}  # fmt: skip


def series(points, mid="MS-0002", name="Score on the rulebook scale", **over):
    return number(mid=mid, name=name, output_kind="series", result={"points": points}, **over)


# ----------------------------------------------------------------------- a number
def test_a_number_is_one_point_on_one_line_with_its_text_unchanged():
    data = chartdata.measure_chart_data([number()], RULES)
    assert data["scale_max"] == "100.00"
    assert [t["label"] for t in data["terms"]] == ["Result"]
    assert data["series"] == [
        {
            "name": "Change over the window",
            "kind": "measure",
            "values": ["11.00"],
            "record_ids": [["A-000001", "A-000003"]],
            "legend": chartdata.MEASURE_LEGEND,
        }
    ]
    assert data["excluded"] == []
    assert data["record_ids"] == ["A-000001", "A-000003"]
    assert data["clause_ids"] == ["MP-1", "TR-1"]
    assert data["band_clauses"] == [] and data["gap"] is None
    assert data["source"] == chartdata.MEASURE_SOURCE


def test_the_measure_the_line_came_from_is_named_with_its_version_and_hashes():
    data = chartdata.measure_chart_data([number()], RULES)
    assert data["measures"] == [
        {"measure_id": "MS-0001", "version": 1, "code_sha256": "a" * 64, "result_hash": "b" * 64}
    ]


def test_two_numbers_are_two_lines_at_the_same_place_and_ids_are_listed_once():
    first = number()
    second = number(value="62.00", exact="62", mid="MS-0003", name="Latest score",
                    clause_ids=["TR-1", "SC-1"], record_ids=["A-000003", "A-000002"])
    data = chartdata.measure_chart_data([first, second], RULES)
    assert [line["values"] for line in data["series"]] == [["11.00"], ["62.00"]]
    assert data["record_ids"] == ["A-000001", "A-000002", "A-000003"]
    assert data["clause_ids"] == ["MP-1", "TR-1", "SC-1"], "in first-seen order, once each"


@pytest.mark.parametrize(
    ("value", "exact"),
    [("0.00", "0"), ("100.00", "100"), ("99.99", "9999/100"), ("62.00", "62")],
)
def test_a_value_inside_the_axis_including_its_edges_is_drawn(value, exact):
    data = chartdata.measure_chart_data([number(value=value, exact=exact)], RULES)
    assert data["series"][0]["values"] == [value] and data["excluded"] == []


@pytest.mark.parametrize(
    ("value", "exact"), [("-3.00", "-3"), ("100.01", "10001/100"), ("250.00", "250")]
)
def test_a_value_off_the_axis_is_left_off_and_listed_not_clipped(value, exact):
    data = chartdata.measure_chart_data([number(value=value, exact=exact)], RULES)
    assert data["series"] == []
    assert data["excluded"] == [
        {"skill": "Change over the window", "measure_id": "MS-0001",
         "reason": "outside_the_score_scale"}
    ]  # fmt: skip
    assert data["record_ids"] == [] and data["clause_ids"] == []


def test_a_result_that_is_not_ok_or_not_a_kind_the_chart_knows_is_left_off_with_its_reason():
    not_enough = number(status="blocked", result={})
    table = number(mid="MS-0004", name="A table", output_kind="table", result={"rows": []})
    data = chartdata.measure_chart_data([not_enough, table], RULES)
    assert data["series"] == []
    assert [(e["measure_id"], e["reason"]) for e in data["excluded"]] == [
        ("MS-0001", "no_value"),
        ("MS-0004", "kind_not_drawn"),
    ]


# ----------------------------------------------------------------------- which number
def several(**over):
    fields = {
        "change": {"value": "11.00", "exact": "11"},
        "scores_used": {"value": "3.00", "exact": "3"},
        "threshold_met": {"value": "1.00", "exact": "1"},
    }
    return number(result={"fields": fields}, **over)


def test_a_result_with_several_numbers_is_drawn_one_number_at_a_time_and_the_line_says_which():
    data = chartdata.measure_chart_data([several()], RULES, "scores_used")
    assert [(s["name"], s["values"]) for s in data["series"]] == [
        ("Change over the window: scores_used", ["3.00"])
    ]


def test_a_result_with_several_numbers_and_no_name_is_refused_with_the_names_it_has():
    with pytest.raises(chartdata.FieldNeeded) as info:
        chartdata.measure_chart_data([several()], RULES)
    assert "change, scores_used, threshold_met" in str(info.value)
    with pytest.raises(chartdata.FieldNeeded):
        chartdata.measure_chart_data([several()], RULES, "no_such_number")


def test_a_name_given_for_a_result_that_lacks_it_is_refused_not_guessed():
    with pytest.raises(chartdata.FieldNeeded):
        chartdata.measure_chart_data([number()], RULES, "scores_used")


def test_the_one_number_of_a_result_needs_no_name_and_may_be_named():
    assert chartdata.measure_chart_data([number()], RULES, "change")["series"][0]["values"] == [
        "11.00"
    ]
    assert chartdata.measure_chart_data([number()], RULES)["series"][0]["values"] == ["11.00"]


def test_the_axis_test_is_made_on_the_number_that_is_drawn_not_on_the_others():
    big = several()
    big["result"]["fields"]["change"] = {"value": "250.00", "exact": "250"}
    off = chartdata.measure_chart_data([big], RULES, "change")
    assert off["series"] == [] and off["excluded"][0]["reason"] == "outside_the_score_scale"
    on = chartdata.measure_chart_data([big], RULES, "scores_used")
    assert on["series"][0]["values"] == ["3.00"] and on["excluded"] == []


# ----------------------------------------------------------------------- a series
def test_a_series_is_one_line_over_its_own_terms_in_time_order():
    points = [
        point("2026/2027", 1, "70.00", "70", ["A-000009"]),
        point("2025/2026", 3, "62.50", "125/2", ["A-000004"]),
    ]
    data = chartdata.measure_chart_data([series(points)], RULES)
    assert [t["label"] for t in data["terms"]] == ["T3", "T1"]
    assert [t["group"] for t in data["terms"]] == ["2025/26 · Form 1", "2026/27 · Form 1"]
    assert data["series"][0]["values"] == ["62.50", "70.00"]
    assert data["series"][0]["record_ids"] == [["A-000004"], ["A-000009"]]
    assert data["record_ids"] == ["A-000004", "A-000009"]


def test_two_series_share_a_term_axis_and_a_term_one_lacks_is_a_gap_not_a_zero():
    a = series([point("2025/2026", 1, "50.00", "50", ["A-000001"]),
                point("2025/2026", 2, "60.00", "60", ["A-000002"])], mid="MS-0002", name="One")
    b = series([point("2025/2026", 2, "55.00", "55", ["A-000007"])], mid="MS-0005", name="Two")
    data = chartdata.measure_chart_data([a, b], RULES)
    assert [t["label"] for t in data["terms"]] == ["T1", "T2"]
    assert [line["values"] for line in data["series"]] == [["50.00", "60.00"], [None, "55.00"]]
    assert data["series"][1]["record_ids"] == [[], ["A-000007"]]


def test_a_series_with_a_point_off_the_axis_is_left_off_whole():
    points = [point("2025/2026", 1, "50.00", "50", ["A-000001"]),
              point("2025/2026", 2, "130.00", "130", ["A-000002"])]
    data = chartdata.measure_chart_data([series(points)], RULES)
    assert data["series"] == [] and data["excluded"][0]["reason"] == "outside_the_score_scale"


def test_a_series_with_no_points_is_left_off_as_having_no_value():
    data = chartdata.measure_chart_data([series([])], RULES)
    assert data["series"] == [] and data["excluded"][0]["reason"] == "no_value"


def test_a_number_and_a_series_cannot_share_one_chart():
    with pytest.raises(chartdata.MixedKinds):
        chartdata.measure_chart_data(
            [number(), series([point("2025/2026", 1, "50.00", "50", ["A-000001"])])], RULES
        )


# ----------------------------------------------------------------------- the same limits as before
def test_nothing_asked_and_too_many_lines_are_refused_as_for_a_subject_chart():
    with pytest.raises(chartdata.NothingAsked):
        chartdata.measure_chart_data([], RULES)
    many = [number(mid=f"MS-{n:04d}", name=f"M{n}") for n in range(1, chartdata.LINE_CAP + 2)]
    with pytest.raises(chartdata.TooManyLines):
        chartdata.measure_chart_data(many, RULES)
    chartdata.measure_chart_data(many[: chartdata.LINE_CAP], RULES)


def test_the_same_result_twice_is_one_line():
    data = chartdata.measure_chart_data([number(), number()], RULES)
    assert len(data["series"]) == 1


def test_a_chart_of_measures_has_the_same_keys_a_chart_of_subjects_has_for_the_drawing():
    wanted = {"scale_max", "terms", "series", "band_clauses", "gap", "excluded", "record_ids",
              "clause_ids", "source"}  # fmt: skip
    assert wanted <= set(chartdata.measure_chart_data([number()], RULES))


# ----------------------------------------------------------------------- the picture and its words
def drawn_result(*results):
    data = chartdata.measure_chart_data(list(results), RULES)
    return {"status": "ok", "title": "A chart", "subtitle": "Rulebook baobab-mp", **data}


def test_the_picture_is_drawn_from_the_result_and_its_legend_says_what_the_line_is():
    from mdeis import chart_image

    result = drawn_result(number())
    keys = ("title", "subtitle", "scale_max", "terms", "series", "gap", "source")
    svg = chart_image.trend_svg({k: result[k] for k in keys} | {"band_legend": ""})
    assert chartdata.MEASURE_LEGEND in svg and chartdata.MEASURE_SOURCE in svg
    assert "Score in each term, from the report cards" not in svg


def test_a_subject_chart_keeps_its_old_legend():
    from mdeis import chart_image

    chart = {
        "title": "T", "subtitle": "S", "scale_max": "100.00", "gap": None, "band_legend": "",
        "terms": [{"label": "T1", "group": "2025/26 · Form 1", "band": None}],
        "series": [{"name": "English", "kind": "subject", "values": ["50.00"], "record_ids": [[]]}],
        "source": chartdata.SOURCE,
    }  # fmt: skip
    assert "Score in each term, from the report cards" in chart_image.trend_svg(chart)


def test_the_reply_says_a_measure_chart_was_computed_by_a_measure_and_names_what_was_left_off():
    from orchestrator import chart_reply

    off = number(value="250.00", exact="250", mid="MS-0003", name="Too big")
    result = drawn_result(number(), off)
    text = chart_reply.caption(result)
    assert "computed by an approved measure" in text and "term by term" not in text
    assert "Rules: [MP-1] [TR-1]." in text
    assert "Too big (its value does not fit on the score scale)" in text
    assert chart_reply.picture(result).startswith("![Chart: Change over the window]")
    assert chart_reply.is_chart("make_chart", result)


def test_a_subject_chart_keeps_its_old_caption():
    from orchestrator import chart_reply

    result = {
        "status": "ok", "series": [{"name": "English"}], "clause_ids": ["TR-1"], "excluded": [],
    }  # fmt: skip
    assert chart_reply.caption(result).startswith(
        "Chart of English, term by term, from the stored report-card scores."
    )
