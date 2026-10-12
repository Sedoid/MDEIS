"""Changed copies of the methodology document, and exactly what differs (E-15).

The base is `methodology-v2.0.md`, the real v2.0 PDF turned into Markdown with its headings kept
(see methodology_from_pdf.py). From it:

    methodology-v2.1-small.md   one section reworded: 6.4 Trend labels, one sentence
    methodology-v2.1-full.md    that, plus a section added (5.6 Small groups) and a section
                                removed (6.6 Example)
    methodology-free-form.md    the same text with the numbering taken off the headings

`methodology-changes.json` says, for each copy, which sections were changed, added and removed, and
which template rules rest on a changed or removed section (the template's own `method_ref` values,
read straight from the YAML). `*.sections.json` is the list of sections a correct reader finds in
each file: id, title, level. Nothing here reads src/mdeis.
"""

import re
from pathlib import Path

import yaml

BASE_NAME = "methodology-v2.0.md"

OLD_SENTENCE = (
    "MDEIS must not label a learner as improving or declining based only on two results from "
    "assessments that cannot reasonably be compared."
)
NEW_SENTENCE = (
    "MDEIS must not label a learner as improving or declining based only on two results, or on "
    "results from assessments that cannot reasonably be compared."
)
NEW_SECTION = """### 5.6 Small groups

MDEIS should not report results for a group so small that an individual learner could be
identified from the figures. The configuration must state the smallest group size that may be
reported. Where a group is smaller than that, MDEIS should say that the result is withheld and why.

"""
HEADING = re.compile(r"^(#{1,4}) (.+)$", re.M)


def sections(md):
    """The sections a correct reader finds: id, title, level. Uses the document's own numbering.

    A top-level heading `n. Title` is accepted only if n is the next number in sequence; a
    sub-section is `n.m Title`; a step is `Step n: Title` inside section 4 (id `4.Sn`); a lettered
    measure `A. Title` belongs to the sub-section above it (id `6.3.B`).
    """
    out, top, sub = [], 0, None
    for match in HEADING.finditer(md):
        level, text = len(match.group(1)), match.group(2).strip()
        m = re.match(r"(\d+)\. (.+)", text)
        if level == 2 and m and int(m.group(1)) == top + 1:
            top = int(m.group(1))
            sub = None
            out.append({"id": str(top), "title": m.group(2), "level": 1})
            continue
        m = re.match(r"(\d+)\.(\d+) (.+)", text)
        if level == 3 and m and int(m.group(1)) == top:
            sub = f"{m.group(1)}.{m.group(2)}"
            out.append({"id": sub, "title": m.group(3), "level": 2})
            continue
        m = re.match(r"Step (\d+): (.+)", text)
        if level == 3 and m and top == 4:
            out.append({"id": f"4.S{m.group(1)}", "title": m.group(2), "level": 2})
            continue
        m = re.match(r"([A-F])\. (.+)", text)
        if level == 4 and m and sub:
            out.append({"id": f"{sub}.{m.group(1)}", "title": m.group(2), "level": 3})
    return out


def section_span(md, section_id):
    """Start and end offsets of a section (a top-level `n` or a sub-section `n.m`): from its
    heading to the next heading of the same or a higher level."""
    if "." in section_id:
        level, pattern = 3, rf"^### {re.escape(section_id)} .+$"
    else:
        level, pattern = 2, rf"^## {re.escape(section_id)}\. .+$"
    start = re.search(pattern, md, re.M)
    if not start:
        raise KeyError(section_id)
    nxt = re.compile(rf"^#{{1,{level}}} ", re.M).search(md, start.end())
    return start.start(), nxt.start() if nxt else len(md)


def rule_refs(template_path):
    """{rule id: the methodology section it rests on}, from the template's method_ref values."""
    d = yaml.safe_load(Path(template_path).read_text(encoding="utf-8"))
    refs = {}
    for value in d.values():
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and "method_ref" in item:
                    refs[item["id"]] = str(item["method_ref"])
    return refs


def resting_on(section_ids, refs):
    """Template rules whose method_ref is one of these sections or sits inside one."""
    out = {}
    for sid in section_ids:
        rules = sorted(r for r, ref in refs.items() if ref == sid or ref.startswith(sid + "."))
        out[sid] = rules
    return out


def reword(md):
    assert md.count(OLD_SENTENCE) == 1, "the sentence to reword is not in the base document"
    return md.replace(OLD_SENTENCE, NEW_SENTENCE)


def add_small_groups(md):
    """Insert 5.6 at the end of section 5, just before section 6."""
    marker = "## 6. Evaluation type B: Longitudinal analysis"
    assert md.count(marker) == 1
    return md.replace(marker, NEW_SECTION + marker)


def remove_example(md):
    start, end = section_span(md, "6.6")
    return md[:start] + md[end:]


def free_form(md):
    """The same text with the numbering taken off every heading."""
    out = []
    for line in md.splitlines():
        m = HEADING.match(line)
        if m:
            text = re.sub(r"^(Step \d+: |\d+(\.\d+)*\.? |[A-F]\. )", "", m.group(2))
            line = f"{m.group(1)} {text}"
        out.append(line)
    return "\n".join(out) + "\n"


def build(base_md, template_path):
    """Return {file name: text} for the copies, the changes file's content and the section lists."""
    refs = rule_refs(template_path)
    small = reword(base_md)
    full = remove_example(add_small_groups(small))
    base_sections = {s["id"]: s for s in sections(base_md)}
    full_sections = {s["id"]: s for s in sections(full)}
    texts = {
        BASE_NAME: base_md,
        "methodology-v2.1-small.md": small,
        "methodology-v2.1-full.md": full,
        "methodology-free-form.md": free_form(base_md),
    }
    changed = [
        {
            "section": "6.4",
            "title": base_sections["6.4"]["title"],
            "was": OLD_SENTENCE,
            "now": NEW_SENTENCE,
        }
    ]
    added = [{"section": "5.6", "title": full_sections["5.6"]["title"]}]
    removed = [{"section": "6.6", "title": base_sections["6.6"]["title"]}]
    changes = {
        "base": BASE_NAME,
        "copies": {
            "methodology-v2.1-small.md": {
                "changed": changed,
                "added": [],
                "removed": [],
                "template_rules_resting_on_changed_or_removed_sections": resting_on(["6.4"], refs),
            },
            "methodology-v2.1-full.md": {
                "changed": changed,
                "added": added,
                "removed": removed,
                "template_rules_resting_on_changed_or_removed_sections": resting_on(
                    ["6.4", "6.6"], refs
                ),
            },
        },
        "note": "Section ids use the document's own numbering. A rule 'rests on' a section when "
        "its method_ref in the template is that section or a part of it.",
    }
    section_lists = {name: sections(text) for name, text in texts.items()}
    return texts, changes, section_lists
