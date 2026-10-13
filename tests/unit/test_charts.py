"""The chart drawing (E-23): same input gives the same bytes, nothing from outside, text escaped."""

import base64
import re
import xml.etree.ElementTree as ET

from mdeis.core import charts

CHART = {
    "title": "Reading · L-0000",
    "subtitle": "Rulebook v1",
    "scale_max": 20,
    "points": [
        {"label": "2025/26 T1", "sub": "Form 1", "value": 11.5, "band": [12, 15]},
        {"label": "2025/26 T2", "sub": "Form 1", "value": 12.25, "band": [12, 15]},
    ],
    "band_legend": "Expected range for the stage (ST-3, v1)",
    "gap": {"index": 1, "from_value": 12.25, "to_value": 12, "text": "0 below"},
    "source": "Report-card scores only. Not a prediction.",
}


def test_the_same_input_gives_the_same_bytes():
    assert charts.trend_svg(CHART) == charts.trend_svg(dict(CHART))


def test_the_picture_is_well_formed_and_holds_no_script_or_outside_address():
    svg = charts.trend_svg(CHART)
    root = ET.fromstring(svg)
    tags = {element.tag.split("}")[-1] for element in root.iter()}
    assert not tags & {"script", "foreignObject", "image", "a", "style", "use"}
    assert "http" not in svg.replace("http://www.w3.org/2000/svg", "")
    assert "onload" not in svg and "javascript:" not in svg


def test_every_value_drawn_is_one_that_was_passed_in():
    svg = charts.trend_svg(CHART)
    shown = set(re.findall(r">(-?\d+(?:\.\d+)?)</text>", svg))
    assert {"11.5", "12.25"} <= shown
    assert shown <= {"0", "5", "10", "15", "20", "11.5", "12.25"}  # the axis and the two scores


def test_the_top_of_the_axis_is_the_scale_that_was_passed_in():
    other = {**CHART, "scale_max": 100}
    assert ">100</text>" in charts.trend_svg(other) and ">20</text>" not in charts.trend_svg(other)


def test_text_is_escaped_and_the_legend_is_inside_the_picture():
    hostile = {**CHART, "title": "<script>alert(1)</script> & co", "band_legend": "a<b"}
    svg = charts.trend_svg(hostile)
    ET.fromstring(svg)  # still well formed
    assert "<script" not in svg and "&lt;script&gt;" in svg
    assert "a&lt;b" in svg and "Score in each term" in svg


def test_a_single_point_and_no_range_still_draw():
    one = {**CHART, "points": [{"label": "T1", "value": 9, "band": None}], "gap": None}
    ET.fromstring(charts.trend_svg(one))


def test_the_markdown_image_carries_the_picture_and_a_safe_alt_text():
    svg = charts.trend_svg(CHART)
    image = charts.markdown_image(svg, "Trend [sample]\nline two")
    match = re.fullmatch(r"!\[([^\]\n]*)\]\(data:image/svg\+xml;base64,([A-Za-z0-9+/=]+)\)", image)
    assert match and base64.b64decode(match.group(2)).decode() == svg
    assert match.group(1) == "Trend (sample) line two"
