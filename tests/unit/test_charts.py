"""The chart drawing (E-23): same input gives the same bytes, nothing from outside, text escaped."""

import base64
import re
import xml.etree.ElementTree as ET

from mdeis import chart_image as charts

TERMS = [
    {"label": "T1", "group": "2025/26 · Form 1", "band": ["12.00", "15.00"]},
    {"label": "T2", "group": "2025/26 · Form 1", "band": ["12.00", "15.00"]},
    {"label": "T1", "group": "2026/27 · Form 2", "band": ["13.00", "16.00"]},
]
ONE = {
    "title": "Reading · L-0000",
    "subtitle": "Rulebook v1",
    "scale_max": "20.00",
    "terms": TERMS,
    "series": [{"name": "Reading", "values": ["11.50", "12.25", "11.50"]}],
    "band_legend": "Expected range for the stage (ST-3, v1)",
    "gap": {"index": 2, "from_value": "11.50", "to_value": "13.00", "text": "1.50 below"},
    "source": "Report-card scores only. Not a prediction.",
}
MANY = {
    **ONE,
    "title": "Mathematics, Chemistry, Physics · L-0000",
    "series": [
        {"name": "Mathematics", "values": ["12.00", "13.00", "14.00"]},
        {"name": "Chemistry", "values": ["8.00", None, "11.00"]},
        {"name": "Physics", "values": ["13.50", "13.75", None]},
    ],
    "terms": [{**t, "band": None} for t in TERMS],
    "gap": None,
}


def shown_numbers(svg):
    return set(re.findall(r">(-?\d+(?:\.\d+)?)</text>", svg))


def test_the_same_input_gives_the_same_bytes():
    assert charts.trend_svg(ONE) == charts.trend_svg(dict(ONE))
    assert charts.trend_svg(MANY) == charts.trend_svg(dict(MANY))


def test_the_picture_is_well_formed_and_holds_no_script_or_outside_address():
    for chart in (ONE, MANY):
        svg = charts.trend_svg(chart)
        root = ET.fromstring(svg)
        tags = {element.tag.split("}")[-1] for element in root.iter()}
        assert not tags & {"script", "foreignObject", "image", "a", "style", "use"}
        assert "http" not in svg.replace("http://www.w3.org/2000/svg", "")
        assert "onload" not in svg and "javascript:" not in svg


def test_every_score_drawn_is_one_that_was_passed_in():
    shown = shown_numbers(charts.trend_svg(ONE))
    assert {"11.50", "12.25"} <= shown
    assert shown <= {"0", "5", "10", "15", "20", "11.50", "12.25"}  # the axis and the scores


def test_the_top_of_the_axis_is_the_scale_that_was_passed_in():
    svg = charts.trend_svg({**ONE, "scale_max": "100.00"})
    assert ">100</text>" in svg and ">20</text>" not in svg


def test_text_is_escaped_and_the_legend_is_inside_the_picture():
    hostile = {**ONE, "title": "<script>alert(1)</script> & co", "band_legend": "a<b"}
    svg = charts.trend_svg(hostile)
    ET.fromstring(svg)
    assert "<script" not in svg and "&lt;script&gt;" in svg
    assert "a&lt;b" in svg and "Score in each term" in svg


def test_several_lines_differ_by_shape_and_dash_and_are_named_in_the_legend_and_at_the_end():
    svg = charts.trend_svg(MANY)
    for name in ("Mathematics", "Chemistry", "Physics"):
        assert svg.count(f">{name}</text>") == 2  # once in the legend, once at the line's end
    assert "<circle" in svg and "<rect" in svg and "<polygon" in svg  # three marker shapes
    assert 'stroke-dasharray="8 4"' in svg and 'stroke-dasharray="2 4"' in svg
    assert "Expected range" not in svg  # no single range fits several lines


def test_a_term_with_no_score_breaks_the_line_and_is_never_drawn_as_zero():
    svg = charts.trend_svg(MANY)
    # Chemistry has T1 and T3 but not T2: two points, no line between them (no polyline for it)
    assert svg.count("<polyline") == 2  # Mathematics (3 points) and Physics (2 points) only
    assert "0.00" not in shown_numbers(svg)


def test_line_names_at_the_right_edge_are_kept_apart():
    close = {**MANY, "series": [
        {"name": "A", "values": ["12.00", "12.00", "12.00"]},
        {"name": "B", "values": ["12.10", "12.10", "12.10"]},
    ]}
    ys = [float(y) for y in re.findall(r'<text x="[\d.]+" y="([\d.]+)"[^>]*stroke="#ffffff"',
                                       charts.trend_svg(close))]
    assert len(ys) == 2 and abs(ys[0] - ys[1]) >= charts.END_GAP


def test_a_single_point_and_no_range_still_draw():
    one = {**ONE, "terms": [{"label": "T1", "group": "2025/26 · Form 1", "band": None}],
           "series": [{"name": "Reading", "values": ["9.00"]}], "gap": None}
    ET.fromstring(charts.trend_svg(one))


def test_school_years_are_grouped_under_their_terms():
    svg = charts.trend_svg(ONE)
    assert svg.count(">2025/26 · Form 1</text>") == 1 and svg.count(">2026/27 · Form 2</text>") == 1


def test_the_markdown_image_carries_the_picture_and_a_safe_alt_text():
    svg = charts.trend_svg(ONE)
    image = charts.markdown_image(svg, "Trend [sample]\nline two")
    pattern = r"!\[([^\]\n]*)\]\(data:image/svg\+xml;base64,([A-Za-z0-9+/=]+)\)"
    match = re.fullmatch(pattern, image)
    assert match and base64.b64decode(match.group(2)).decode() == svg
    assert match.group(1) == "Trend (sample) line two"
