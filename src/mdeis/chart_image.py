"""Draws a progression chart as an SVG. A pure function: numbers in, text out, no database or model.

Every value on the chart is passed in (see `chartdata.chart_data` in `mdeis.core`). Nothing here
works out a score, a range or a gap, and no scale is fixed here: the top of the axis is `scale_max` from the rulebook.
The same input gives the same bytes. The picture holds no script and no outside address. Its legend
is drawn inside it, so the two cannot be separated, and it carries its own light background so it
reads in a dark theme too. Lines differ by colour, marker shape and dash, so they can be told apart
in greyscale.

`chart` is a dict:
    title, subtitle   text shown at the top
    scale_max         the top of the score axis (the rulebook's scale)
    terms             [{label, group, band}] left to right; band is [low, high] or None
    series            [{name, values}]; a value is a number, or None for a term with no score
    band_legend       the legend text for the shaded band (when any term has a band)
    gap               None, or {index, from_value, to_value, text}
    source            the line at the foot
"""

from __future__ import annotations

import base64
from xml.sax.saxutils import escape

WIDTH = 680
LEFT, RIGHT, TOP, PLOT_HEIGHT = 60, 100, 88, 210
INK, MUTED, GRID = "#1c2530", "#4d5864", "#dde2e7"
BAND, BAND_EDGE, GAP = "#cfe2f5", "#3f74ab", "#92400e"
COLOURS = ["#1f3a5f", "#b45309", "#0f766e", "#7c3aed", "#be123c", "#4d5864"]
DASHES = ["", "8 4", "2 4", "10 4 2 4", "5 5", "1 5"]
MARKERS = ["circle", "square", "triangle", "diamond", "cross", "plus"]
FONT = "system-ui, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif"
TICKS = 4
PAD = 24  # space at the left and right of the text blocks
SIZE = {"title": 21, "subtitle": 13, "tick": 12.5, "value": 14, "term": 13, "stage": 12,
        "legend": 13.5, "gap": 14, "source": 12, "end": 12.5}
ROW = 26  # distance between legend rows
LABEL_LIMIT = 8  # more terms than this: values are not printed on the line, only on the axis
END_GAP = 15  # least distance between two line names at the right edge
GAP_LEGEND = "Distance from the last score to the nearest range edge"


def _n(value) -> str:
    """A coordinate as the picture prints it: two places at most, no trailing zeros."""
    text = f"{float(value):.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _score(value) -> str:
    """A score as printed on the picture: always two places, as the tools give it."""
    return f"{float(value):.2f}"


def _text(x, y, content, size, fill=INK, anchor="start", weight="normal", halo=False) -> str:
    extra = ' stroke="#ffffff" stroke-width="3.5" paint-order="stroke"' if halo else ""
    return (
        f'<text x="{_n(x)}" y="{_n(y)}" font-size="{size}" fill="{fill}" text-anchor="{anchor}" '
        f'font-weight="{weight}"{extra}>{escape(str(content))}</text>'
    )


def _marker(shape: str, x: float, y: float, colour: str, size: float = 5.5) -> str:
    """One point. Closed shapes are white inside so the line behind does not show through."""
    cx, cy, r = _n(x), _n(y), size
    style = f'fill="#ffffff" stroke="{colour}" stroke-width="2.5" stroke-linejoin="round"'
    if shape == "circle":
        return f'<circle cx="{cx}" cy="{cy}" r="{_n(r)}" {style}/>'
    if shape == "square":
        return (f'<rect x="{_n(x - r)}" y="{_n(y - r)}" width="{_n(2 * r)}" height="{_n(2 * r)}" '
                f'{style}/>')
    if shape == "triangle":
        pts = f"{_n(x)},{_n(y - r - 1)} {_n(x + r + 1)},{_n(y + r)} {_n(x - r - 1)},{_n(y + r)}"
        return f'<polygon points="{pts}" {style}/>'
    if shape == "diamond":
        pts = f"{_n(x)},{_n(y - r - 2)} {_n(x + r + 2)},{_n(y)} {_n(x)},{_n(y + r + 2)} " \
              f"{_n(x - r - 2)},{_n(y)}"
        return f'<polygon points="{pts}" {style}/>'
    line = f'stroke="{colour}" stroke-width="2.75" stroke-linecap="round"'
    if shape == "cross":
        d = r - 0.5
        return (f'<path d="M{_n(x - d)},{_n(y - d)} L{_n(x + d)},{_n(y + d)} '
                f'M{_n(x - d)},{_n(y + d)} L{_n(x + d)},{_n(y - d)}" fill="none" {line}/>')
    d = r + 0.5
    return (f'<path d="M{_n(x - d)},{_n(y)} H{_n(x + d)} M{_n(x)},{_n(y - d)} V{_n(y + d)}" '
            f'fill="none" {line}/>')


def _segments(values: list) -> list:
    """Runs of consecutive terms that have a score: a missing term breaks the line."""
    runs, run = [], []
    for i, value in enumerate(values):
        if value is None:
            if run:
                runs.append(run)
            run = []
        else:
            run.append(i)
    if run:
        runs.append(run)
    return runs


def _end_labels(series, x_of, y_of) -> list:
    """Each line's name beside its last point, moved apart where two would touch."""
    wanted = []
    for k, line in enumerate(series):
        last = max((i for i, v in enumerate(line["values"]) if v is not None), default=None)
        if last is not None:
            wanted.append([y_of(line["values"][last]), k, x_of(last)])
    wanted.sort()
    for a, b in zip(wanted, wanted[1:], strict=False):
        if b[0] - a[0] < END_GAP:
            b[0] = a[0] + END_GAP
    return wanted


def trend_svg(chart: dict) -> str:
    terms, series, top = chart["terms"], chart["series"], float(chart["scale_max"])
    left, right = LEFT, WIDTH - RIGHT
    plot_top, plot_bottom = TOP, TOP + PLOT_HEIGHT
    span = plot_bottom - plot_top
    count = len(terms)
    step = (right - left) / max(count, 1)
    gap, many = chart.get("gap"), len(series) > 1
    bands = any(t.get("band") for t in terms)
    rows = len(series) + (1 if bands else 0) + (1 if gap else 0)
    height = int(plot_bottom + 62 + 26 + ROW * (rows - 1) + 40)

    def x_of(i):
        return left + step * (i + 0.5)

    def y_of(value):
        return plot_bottom - span * float(value) / top

    names = ", ".join(line["name"] for line in series)
    summary = "; ".join(
        f"{line['name']}: " + ", ".join(
            f"{terms[i]['group']} {terms[i]['label']} {_score(v)}"
            for i, v in enumerate(line["values"]) if v is not None)
        for line in series
    )
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'viewBox="0 0 {WIDTH} {height}" role="img" font-family="{FONT}">',
        f"<title>{escape(chart['title'])}</title>",
        f"<desc>{escape(names)}. {escape(summary)}. Scale 0 to {_n(top)}.</desc>",
        f'<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1}" rx="12" fill="#ffffff" '
        'stroke="#c9d0d8"/>',
        _text(PAD, 38, chart["title"], SIZE["title"], INK, weight="bold"),
        _text(PAD, 60, chart["subtitle"], SIZE["subtitle"], MUTED),
    ]

    for k in range(TICKS + 1):  # axis: grid lines and the scale's own numbers
        value = top * k / TICKS
        y = y_of(value)
        parts.append(f'<line x1="{left}" y1="{_n(y)}" x2="{right}" y2="{_n(y)}" stroke="{GRID}"/>')
        parts.append(_text(left - 10, y + 4.5, _n(value), SIZE["tick"], MUTED, "end"))

    for i, term in enumerate(terms):  # the expected range, one column per term
        if not term.get("band"):
            continue
        low, high = (float(v) for v in term["band"])
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

    for k, line in enumerate(series):  # the lines, broken where a term has no score
        colour, dash = COLOURS[k % len(COLOURS)], DASHES[k % len(DASHES)]
        dashed = f' stroke-dasharray="{dash}"' if dash else ""
        for run in _segments(line["values"]):
            if len(run) > 1:
                path = " ".join(f"{_n(x_of(i))},{_n(y_of(line['values'][i]))}" for i in run)
                parts.append(
                    f'<polyline points="{path}" fill="none" stroke="{colour}" stroke-width="2.75" '
                    f'stroke-linejoin="round"{dashed}/>'
                )

    if gap:  # a bracket from the last score to the nearest edge of the range, clear of its label
        x = x_of(gap["index"]) + 30
        y1, y2 = y_of(gap["from_value"]), y_of(gap["to_value"])
        parts.append(
            f'<path d="M{_n(x - 6)},{_n(y1)} H{_n(x)} V{_n(y2)} H{_n(x - 6)}" fill="none" '
            f'stroke="{GAP}" stroke-width="2.25"/>'
        )
        parts.append(_text(x + 8, (y1 + y2) / 2 + 5, gap["text"], SIZE["gap"], GAP, weight="bold"))

    for k, line in enumerate(series):  # the points, and their values when there is room
        colour, shape = COLOURS[k % len(COLOURS)], MARKERS[k % len(MARKERS)]
        for i, value in enumerate(line["values"]):
            if value is None:
                continue
            x, y = x_of(i), y_of(value)
            parts.append(_marker(shape, x, y, colour))
            if not many and count <= LABEL_LIMIT:
                parts.append(_text(x, y - 13, _score(value), SIZE["value"], INK, "middle", "bold"))
    if many:
        for y, k, x in _end_labels(series, x_of, y_of):
            parts.append(_text(x + 12, y + 4.5, series[k]["name"], SIZE["end"],
                               COLOURS[k % len(COLOURS)], weight="bold", halo=True))

    group_start = 0  # term labels, then the school year and stage under the terms they cover
    for i, term in enumerate(terms):
        parts.append(_text(x_of(i), plot_bottom + 22, term["label"], SIZE["term"], INK, "middle"))
        last_of_group = i == count - 1 or terms[i + 1]["group"] != term["group"]
        if last_of_group:
            middle = (x_of(group_start) + x_of(i)) / 2
            parts.append(_text(middle, plot_bottom + 42, term["group"], SIZE["stage"], MUTED,
                               "middle"))
            if i < count - 1:
                edge = left + step * (i + 1)
                parts.append(
                    f'<line x1="{_n(edge)}" y1="{plot_bottom + 30}" x2="{_n(edge)}" '
                    f'y2="{plot_bottom + 48}" stroke="{GRID}"/>'
                )
            group_start = i + 1

    parts.append(  # a rule between the chart and its legend
        f'<line x1="{PAD}" y1="{plot_bottom + 62}" x2="{WIDTH - PAD}" y2="{plot_bottom + 62}" '
        f'stroke="{GRID}"/>'
    )
    row, text_x = plot_bottom + 88, PAD + 44  # the centre line of the first legend row
    for k, line in enumerate(series):
        colour, dash = COLOURS[k % len(COLOURS)], DASHES[k % len(DASHES)]
        dashed = f' stroke-dasharray="{dash}"' if dash else ""
        parts.append(
            f'<line x1="{PAD}" y1="{row}" x2="{PAD + 32}" y2="{row}" stroke="{colour}" '
            f'stroke-width="2.75"{dashed}/>'
        )
        parts.append(_marker(MARKERS[k % len(MARKERS)], PAD + 16, row, colour, 5))
        label = line["name"] if many else "Score in each term, from the report cards"
        parts.append(_text(text_x, row + 5, label, SIZE["legend"]))
        row += ROW
    if bands:
        parts.append(
            f'<rect x="{PAD}" y="{row - 7}" width="32" height="14" fill="{BAND}" '
            f'fill-opacity="0.65" stroke="{BAND_EDGE}" stroke-dasharray="6 4"/>'
        )
        parts.append(_text(text_x, row + 5, chart["band_legend"], SIZE["legend"]))
        row += ROW
    if gap:
        parts.append(
            f'<path d="M{PAD + 10},{row - 8} H{PAD + 16} V{row + 8} H{PAD + 10}" fill="none" '
            f'stroke="{GAP}" stroke-width="2.25"/>'
        )
        parts.append(_text(text_x, row + 5, GAP_LEGEND, SIZE["legend"]))
    parts.append(_text(PAD, height - 16, chart["source"], SIZE["source"], MUTED))
    parts.append("</svg>")
    return "".join(parts)


def markdown_image(svg: str, alt: str) -> str:
    """The picture as a markdown image Open WebUI shows in the reply. The alt text is escaped."""
    data = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    safe = alt.replace("[", "(").replace("]", ")").replace("\n", " ")
    return f"![{safe}](data:image/svg+xml;base64,{data})"


__all__ = ["trend_svg", "markdown_image"]
