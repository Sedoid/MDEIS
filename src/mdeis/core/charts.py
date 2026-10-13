"""Draws one trend chart as an SVG. A pure function: numbers in, text out, no database, no model.

Every value on the chart is passed in. Nothing here works out a score, a range or a gap, and no
scale is fixed here: the top of the axis is `scale_max` from the rulebook. The same input gives the
same bytes. The picture holds no script and no outside address. Its legend is drawn inside it, so
the two cannot be separated, and it carries its own light background so it reads in a dark theme.

`chart` is a dict:
    title, subtitle          text shown at the top
    scale_max                the top of the score axis (the rulebook's scale)
    points                   [{label, sub, value, band}] in time order; band is [low, high] or None
    band_legend              the legend text for the shaded band (its clause and rulebook version)
    gap                      None, or {index, from_value, to_value, text}: the distance to draw
    source                   the line at the foot
"""

from __future__ import annotations

import base64
from xml.sax.saxutils import escape, quoteattr

WIDTH, HEIGHT = 680, 470
LEFT, RIGHT, TOP, BOTTOM = 60, 92, 88, 186  # BOTTOM leaves room for the axis labels and legend
INK, MUTED, GRID = "#1c2530", "#4d5864", "#dde2e7"
LINE, BAND, BAND_EDGE, GAP = "#1f3a5f", "#cfe2f5", "#3f74ab", "#b45309"
FONT = "system-ui, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif"
TICKS = 4
PAD = 24  # space at the left and right of the text blocks
SIZE = {"title": 21, "subtitle": 13, "tick": 12.5, "value": 14, "term": 13, "stage": 12,
        "legend": 13.5, "gap": 14, "source": 12}
ROW = 26  # distance between legend rows


def _n(value) -> str:
    """A number as the picture prints it: two places at most, no trailing zeros."""
    text = f"{float(value):.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _text(x, y, content, size, fill=INK, anchor="start", weight="normal") -> str:
    return (
        f'<text x="{_n(x)}" y="{_n(y)}" font-size="{size}" fill="{fill}" text-anchor="{anchor}" '
        f'font-weight="{weight}">{escape(str(content))}</text>'
    )


def trend_svg(chart: dict) -> str:
    points, top = chart["points"], float(chart["scale_max"])
    left, right = LEFT, WIDTH - RIGHT
    plot_top, plot_bottom = TOP, HEIGHT - BOTTOM
    span = plot_bottom - plot_top
    count = len(points)
    step = (right - left) / max(count, 1)

    def x_of(i):
        return left + step * (i + 0.5)

    def y_of(value):
        return plot_bottom - span * float(value) / top

    parts = []
    summary = ", ".join(f"{p['label']} {_n(p['value'])}" for p in points)
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" font-family="{FONT}">'
    )
    parts.append(f"<title>{escape(chart['title'])}</title>")
    parts.append(f"<desc>{escape(summary)}. Scale 0 to {_n(top)}.</desc>")
    parts.append(
        f'<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{HEIGHT - 1}" rx="12" fill="#ffffff" '
        'stroke="#c9d0d8"/>'
    )
    parts.append(_text(PAD, 38, chart["title"], SIZE["title"], INK, weight="bold"))
    parts.append(_text(PAD, 60, chart["subtitle"], SIZE["subtitle"], MUTED))

    for k in range(TICKS + 1):  # axis: grid lines and the scale's own numbers
        value = top * k / TICKS
        y = y_of(value)
        parts.append(
            f'<line x1="{left}" y1="{_n(y)}" x2="{right}" y2="{_n(y)}" stroke="{GRID}" '
            'stroke-width="1"/>'
        )
        parts.append(_text(left - 10, y + 4.5, _n(value), SIZE["tick"], MUTED, "end"))

    for i, point in enumerate(points):  # the expected range, one column per term
        if not point.get("band"):
            continue
        low, high = (float(v) for v in point["band"])
        x0 = left + step * i
        parts.append(
            f'<rect x="{_n(x0)}" y="{_n(y_of(high))}" width="{_n(step)}" '
            f'height="{_n(y_of(low) - y_of(high))}" fill="{BAND}" fill-opacity="0.65"/>'
        )
        for edge in (low, high):
            parts.append(
                f'<line x1="{_n(x0)}" y1="{_n(y_of(edge))}" x2="{_n(x0 + step)}" '
                f'y2="{_n(y_of(edge))}" stroke="{BAND_EDGE}" stroke-width="1.3" '
                'stroke-dasharray="6 4"/>'
            )

    path = " ".join(f"{_n(x_of(i))},{_n(y_of(p['value']))}" for i, p in enumerate(points))
    if count > 1:
        parts.append(
            f'<polyline points="{path}" fill="none" stroke="{LINE}" stroke-width="2.75" '
            'stroke-linejoin="round"/>'
        )

    gap = chart.get("gap")
    if gap:  # a bracket from the last score to the nearest edge of the range, clear of its label
        x = x_of(gap["index"]) + 30
        y1, y2 = y_of(gap["from_value"]), y_of(gap["to_value"])
        parts.append(
            f'<path d="M{_n(x - 6)},{_n(y1)} H{_n(x)} V{_n(y2)} H{_n(x - 6)}" fill="none" '
            f'stroke="{GAP}" stroke-width="2.25"/>'
        )
        parts.append(
            _text(x + 8, (y1 + y2) / 2 + 5, gap["text"], SIZE["gap"], GAP, weight="bold")
        )

    for i, point in enumerate(points):  # score markers, their values and the term labels
        x, y = x_of(i), y_of(point["value"])
        parts.append(
            f'<circle cx="{_n(x)}" cy="{_n(y)}" r="5.5" fill="#ffffff" stroke="{LINE}" '
            'stroke-width="2.75"/>'
        )
        parts.append(_text(x, y - 13, _n(point["value"]), SIZE["value"], INK, "middle", "bold"))
        parts.append(_text(x, plot_bottom + 24, point["label"], SIZE["term"], INK, "middle"))
        if point.get("sub"):
            parts.append(_text(x, plot_bottom + 42, point["sub"], SIZE["stage"], MUTED, "middle"))

    parts.append(  # a rule between the chart and its legend
        f'<line x1="{PAD}" y1="{plot_bottom + 62}" x2="{WIDTH - PAD}" y2="{plot_bottom + 62}" '
        f'stroke="{GRID}" stroke-width="1"/>'
    )
    row = plot_bottom + 88  # the centre line of the first legend row
    text_x = PAD + 44
    parts.append(
        f'<line x1="{PAD}" y1="{row}" x2="{PAD + 32}" y2="{row}" stroke="{LINE}" '
        'stroke-width="2.75"/>'
        f'<circle cx="{PAD + 16}" cy="{row}" r="5" fill="#ffffff" stroke="{LINE}" '
        'stroke-width="2.75"/>'
    )
    parts.append(
        _text(text_x, row + 5, "Score in each term, from the report cards", SIZE["legend"])
    )
    row += ROW
    parts.append(
        f'<rect x="{PAD}" y="{row - 7}" width="32" height="14" fill="{BAND}" fill-opacity="0.65" '
        f'stroke="{BAND_EDGE}" stroke-dasharray="6 4"/>'
    )
    parts.append(_text(text_x, row + 5, chart["band_legend"], SIZE["legend"]))
    if gap:
        row += ROW
        parts.append(
            f'<path d="M{PAD + 10},{row - 8} H{PAD + 16} V{row + 8} H{PAD + 10}" fill="none" '
            f'stroke="{GAP}" stroke-width="2.25"/>'
        )
        parts.append(
            _text(text_x, row + 5, "Distance from the score to the nearest range edge",
                  SIZE["legend"])
        )
    parts.append(_text(PAD, HEIGHT - 16, chart["source"], SIZE["source"], MUTED))
    parts.append("</svg>")
    return "".join(parts)


def markdown_image(svg: str, alt: str) -> str:
    """The picture as a markdown image Open WebUI shows in the reply. The alt text is escaped."""
    data = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    safe = alt.replace("[", "(").replace("]", ")").replace("\n", " ")
    return f"![{safe}](data:image/svg+xml;base64,{data})"


__all__ = ["trend_svg", "markdown_image", "quoteattr"]
