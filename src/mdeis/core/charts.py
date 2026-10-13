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

WIDTH, HEIGHT = 680, 400
LEFT, RIGHT, TOP, BOTTOM = 58, 84, 70, 128  # BOTTOM leaves room for the axis labels and legend
INK, MUTED, GRID = "#1c2530", "#5b6672", "#dde2e7"
LINE, BAND, BAND_EDGE, GAP = "#1f3a5f", "#cfe2f5", "#4a7fb5", "#b45309"
FONT = "system-ui, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif"
TICKS = 4


def _n(value) -> str:
    """A number as the picture prints it: two places at most, no trailing zeros."""
    text = f"{float(value):.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _text(x, y, content, size=12, fill=INK, anchor="start", weight="normal") -> str:
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
        f'<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{HEIGHT - 1}" rx="10" fill="#ffffff" '
        'stroke="#c9d0d8"/>'
    )
    parts.append(_text(24, 30, chart["title"], 17, INK, weight="bold"))
    parts.append(_text(24, 50, chart["subtitle"], 12, MUTED))

    for k in range(TICKS + 1):  # axis: grid lines and the scale's own numbers
        value = top * k / TICKS
        y = y_of(value)
        parts.append(
            f'<line x1="{left}" y1="{_n(y)}" x2="{right}" y2="{_n(y)}" stroke="{GRID}" '
            'stroke-width="1"/>'
        )
        parts.append(_text(left - 8, y + 4, _n(value), 11, MUTED, "end"))

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
                f'y2="{_n(y_of(edge))}" stroke="{BAND_EDGE}" stroke-width="1.2" '
                'stroke-dasharray="5 4"/>'
            )

    path = " ".join(f"{_n(x_of(i))},{_n(y_of(p['value']))}" for i, p in enumerate(points))
    if count > 1:
        parts.append(
            f'<polyline points="{path}" fill="none" stroke="{LINE}" stroke-width="2.5" '
            'stroke-linejoin="round"/>'
        )

    gap = chart.get("gap")
    if gap:  # a bracket from the last score to the nearest edge of the range
        x = x_of(gap["index"]) + 16
        y1, y2 = y_of(gap["from_value"]), y_of(gap["to_value"])
        parts.append(
            f'<path d="M{_n(x - 5)},{_n(y1)} H{_n(x)} V{_n(y2)} H{_n(x - 5)}" fill="none" '
            f'stroke="{GAP}" stroke-width="2"/>'
        )
        parts.append(_text(x + 6, (y1 + y2) / 2 + 4, gap["text"], 12, GAP, weight="bold"))

    for i, point in enumerate(points):  # score markers, their values and the term labels
        x, y = x_of(i), y_of(point["value"])
        parts.append(
            f'<circle cx="{_n(x)}" cy="{_n(y)}" r="5" fill="#ffffff" stroke="{LINE}" '
            'stroke-width="2.5"/>'
        )
        parts.append(_text(x, y - 11, _n(point["value"]), 12, INK, "middle", "bold"))
        parts.append(_text(x, plot_bottom + 18, point["label"], 11, INK, "middle"))
        if point.get("sub"):
            parts.append(_text(x, plot_bottom + 32, point["sub"], 10, MUTED, "middle"))

    ly = plot_bottom + 56  # legend, drawn inside the picture
    parts.append(
        f'<line x1="24" y1="{ly}" x2="52" y2="{ly}" stroke="{LINE}" stroke-width="2.5"/>'
        f'<circle cx="38" cy="{ly}" r="4.5" fill="#ffffff" stroke="{LINE}" stroke-width="2.5"/>'
    )
    parts.append(_text(60, ly + 4, "Score in each term, from the report cards", 12))
    parts.append(
        f'<rect x="24" y="{ly + 14}" width="28" height="12" fill="{BAND}" fill-opacity="0.65" '
        f'stroke="{BAND_EDGE}" stroke-dasharray="5 4"/>'
    )
    parts.append(_text(60, ly + 24, chart["band_legend"], 12))
    if gap:
        parts.append(
            f'<path d="M30,{ly + 34} H36 V{ly + 48} H30" fill="none" stroke="{GAP}" '
            'stroke-width="2"/>'
        )
        parts.append(_text(60, ly + 45, "Distance from the score to the nearest range edge", 12))
    parts.append(_text(24, HEIGHT - 10, chart["source"], 10.5, MUTED))
    parts.append("</svg>")
    return "".join(parts)


def markdown_image(svg: str, alt: str) -> str:
    """The picture as a markdown image Open WebUI shows in the reply. The alt text is escaped."""
    data = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    safe = alt.replace("[", "(").replace("]", ")").replace("\n", " ")
    return f"![{safe}](data:image/svg+xml;base64,{data})"


__all__ = ["trend_svg", "markdown_image", "quoteattr"]
