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

# Record, document and result numbers mean nothing to a person. The files they came from do.
NUMBERS = re.compile(r"\s*\[(?:A|D)-\d+[^\]]*\]|\s*\[[0-9a-f]{6,}[^\]]*\]")
RULE_MARKERS = re.compile(r"\s*\[[A-Z]{1,2}-\d[0-9A-Za-z.]*\]")  # [ST-3], [GM-1], [M-6.4]
EMPTY_SOURCES = re.compile(r"(?im)^[ \t]*[*_]*sources?[*_]*[ \t]*:[ \t]*\.?[ \t]*$\n?")
SOURCES_SHOWN = 12  # files named in one answer, then "and n more"
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
    """What the picture shows, and any subject that was not drawn, in words. The sources of the
    scores are named once, for the whole answer, by `sources_line`."""
    if not isinstance(result, dict):
        return ""
    lines = []
    if result.get("series"):
        names = ", ".join(line["name"] for line in result["series"])
        rules = " ".join(f"[{cid}]" for cid in result.get("clause_ids", []))
        lines.append(f"Chart of {names}, term by term, from the stored report-card scores.")
        if rules:
            lines.append(f"Rules: {rules}.")
    left = result.get("excluded") or []
    if left:
        lines.append("Not drawn: " + "; ".join(_sentence(item) for item in left) + ".")
    return "\n\n".join(lines)


def _plain_text(name: str) -> str:
    """A file name safe to put inside a line of markdown."""
    return re.sub(r"([\\`*_{}\[\]()#+!|<>~])", r"\\\1", name)


def add_sources(found: list, result) -> list:
    """`found` plus the report-card files named in a tool result, each file once."""
    seen = {item["document_id"] for item in found}
    for item in (result.get("sources") if isinstance(result, dict) else None) or []:
        if isinstance(item, dict) and item.get("file") and item.get("document_id") not in seen:
            seen.add(item["document_id"])
            found = [*found, item]
    return found


def add_rules(found: list, result) -> list:
    """`found` plus the rules named in a tool result (with the rulebook's wording), each once."""
    seen = {item["id"] for item in found}
    for item in (result.get("rules") if isinstance(result, dict) else None) or []:
        if isinstance(item, dict) and item.get("text") and item.get("id") not in seen:
            seen.add(item["id"])
            found = [*found, item]
    return found


def footer(found: list, rules: list | None = None) -> str:
    """What the answer rests on, set apart from it: a rule above, small italic lines under it.

    One line names the report cards; one gives the rulebook's own words for the rules applied. A
    file name or a rule with markdown characters in it is escaped, so it cannot change the line."""
    lines = []
    if found:
        ordered = sorted(found, key=lambda s: (s.get("academic_year", ""), s["file"]))
        names = [s["file"] for s in ordered[:SOURCES_SHOWN]]
        more = len(ordered) - len(names)
        tail = f"; and {more} more" if more > 0 else ""
        shown = "; ".join(_plain_text(name) for name in names)
        lines.append(f"*Sources (report cards): {shown}{tail}.*")
    if rules:
        said = "; ".join(
            f"{r['id']} — {_plain_text(r['text'].strip().rstrip('.'))}" for r in rules
        )
        lines.append(f"*Rules applied: {said}.*")
    return "---\n\n" + "\n\n".join(lines) if lines else ""


def sources_line(found: list) -> str:
    return footer(found)


def compose(
    text: str,
    results: list,
    sources: list | None = None,
    show_line: bool = True,
    rules: list | None = None,
) -> str:
    """The model's words, then each chart and its caption, then the files the answer rests on.

    Anything image-like in the words goes. When the tools named their source files, the record,
    document and result numbers the model wrote beside its sentences go too, and so do rule markers
    when the rulebook's wording came with them: a footer names the files and says what the rules
    say.
    Without them the words are left as written."""
    out = strip_images(text)
    if sources:
        out = NUMBERS.sub("", out)
    if rules:
        out = RULE_MARKERS.sub("", out)
    if sources or rules:
        out = EMPTY_SOURCES.sub("", out).rstrip()
    for result in results:
        image = picture(result)
        words = caption(result)
        if image:
            out += f"\n\n{image}"
        if words:
            out += f"\n\n{words}"
    line = footer(sources or [], rules) if show_line else ""
    return out + f"\n\n{line}" if line else out
