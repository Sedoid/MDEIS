"""Putting a chart into a reply. The model never draws one and never sees the picture.

The model calls `make_chart`; the tool returns the scores, the records and the rules (small, and
written to the diary). After the model has written its answer, this module draws the picture from
that result with plain code and adds it, with a caption of sources, under the text. Nothing the
model writes can become an image: any image or data address in its text is removed.

The conversation comes back to us every turn with the picture in it. `strip_images` takes it out
before the model sees the history, so a picture is never paid for twice and never quoted back.
"""

from __future__ import annotations

import re

from mdeis import chart_image

IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)|data:image/[A-Za-z+.-]+;base64,[A-Za-z0-9+/=]+")
SEEN = "[a chart was shown here]"
LEFT_OFF = {
    "blocked": "a question about its records has to be answered first",
    "not_enough_evidence": "there are too few scores",
}


def is_chart(tool: str, result) -> bool:
    """Is this the result of a `make_chart` call that has something to show or to explain?"""
    return tool == "make_chart" and isinstance(result, dict) and result.get("status") in (
        "ok", "not_drawn")


def strip_images(text: str) -> str:
    """The text with every markdown image, and every embedded picture, taken out."""
    return IMAGE.sub(SEEN, text) if isinstance(text, str) else text


def _sentence(item: dict) -> str:
    why = LEFT_OFF.get(item["reason"], item["reason"])
    if item["reason"] == "not_enough_evidence":
        why = (f"only {item['scores_available']} of the {item['scores_needed']} scores "
               "needed are on record")
    return f"{item['skill']} ({why})"


def picture(result: dict) -> str | None:
    """The markdown image for one `make_chart` result, or None when nothing could be drawn."""
    if not isinstance(result, dict) or result.get("status") != "ok" or not result.get("series"):
        return None
    chart = {
        "title": result["title"],
        "subtitle": result["subtitle"],
        "scale_max": result["scale_max"],
        "terms": result["terms"],
        "series": result["series"],
        "band_legend": result.get("band_legend", ""),
        "gap": result.get("gap"),
        "source": result["source"],
    }
    names = ", ".join(line["name"] for line in result["series"])
    return chart_image.markdown_image(chart_image.trend_svg(chart), f"Chart: {names}")


def caption(result: dict) -> str:
    """What the picture shows and where it came from, in words, with its record and rule markers."""
    if not isinstance(result, dict):
        return ""
    lines = []
    if result.get("series"):
        names = ", ".join(line["name"] for line in result["series"])
        records = " ".join(f"[{rid}]" for rid in result.get("record_ids", []))
        rules = " ".join(f"[{cid}]" for cid in result.get("clause_ids", []))
        lines.append(f"Chart of {names}, term by term, from the stored report-card scores.")
        lines.append(f"Records: {records}. Rules: {rules}.")
    left = result.get("excluded") or []
    if left:
        lines.append("Not drawn: " + "; ".join(_sentence(item) for item in left) + ".")
    return "\n\n".join(lines)


def compose(text: str, results: list) -> str:
    """The model's words, then each chart and its caption. Anything image-like in the words goes."""
    out = strip_images(text)
    for result in results:
        image = picture(result)
        words = caption(result)
        if image:
            out += f"\n\n{image}"
        if words:
            out += f"\n\n{words}"
    return out
