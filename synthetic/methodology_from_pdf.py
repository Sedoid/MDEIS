"""One-off tool: turn the real methodology PDF into Markdown with its headings kept.

    python3 -m synthetic.methodology_from_pdf data/synthetic/samples/Methodology_of_mdeis.pdf \
        data/synthetic/methodology/methodology-v2.0.md

Headings are found from the font, not from the words: Georgia Bold 17 pt starting with a number is
a section (`## 6. Title`), Georgia Bold 13 pt is a sub-section or a `Step n:` (`### 6.4 Title`),
bold 11 pt starting with `A.` to `F.` is a lettered measure (`#### A. Title`). Numbered list items
in 3.2 and 10 stay plain text (they are not bold). Needs pdfplumber (dev only). The result is a
committed input: the generator reads the Markdown, not the PDF.
"""

import re
import sys

import pdfplumber

BOLD_SECTION, BOLD_SUB = 17.0, 13.0
TITLE_SIZE = 23.0
BODY_GAP = 1.6  # a gap above this many line heights starts a new paragraph
ZERO_WIDTH = "​"


def lines_of(page):
    """Words grouped into lines, each with its font, size, left edge and top."""
    words = page.extract_words(extra_attrs=["fontname", "size"], use_text_flow=True)
    groups = {}
    for w in words:
        groups.setdefault(round(w["top"] / 2), []).append(w)
    out = []
    for key in sorted(groups):
        ws = sorted(groups[key], key=lambda w: w["x0"])
        out.append(
            {
                "text": " ".join(w["text"] for w in ws).replace(ZERO_WIDTH, ""),
                "bold": "Bold" in ws[0]["fontname"],
                "size": round(ws[0]["size"], 1),
                "x0": ws[0]["x0"],
                "top": ws[0]["top"],
                "bottom": max(w["bottom"] for w in ws),
            }
        )
    return out


def table_rows(table):
    return [[(c or "").replace("\n", " ").strip() for c in row] for row in table]


def table_markdown(rows):
    head, *body = rows
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(out)


def convert(path):
    blocks = []  # (kind, text)
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            tables = page.find_tables()
            boxes = [t.bbox for t in tables]
            for line in lines_of(page):
                inside = [b for b in boxes if b[1] - 1 <= line["top"] <= b[3] + 1]
                if inside:
                    if inside[0] not in [b for k, b in blocks if k == "tablebox"]:
                        idx = boxes.index(inside[0])
                        blocks.append(("tablebox", inside[0]))
                        blocks.append(("table", table_rows(tables[idx].extract())))
                    continue
                blocks.append(("line", line))
    return render(blocks)


def is_item(text):
    return text.startswith("- ") or bool(re.match(r"\d+\. ", text))


def render(blocks):
    out, para, last = [], [], None

    def flush():
        nonlocal para
        if para:
            out.append(" ".join(para))
            para = []

    for kind, item in blocks:
        if kind == "tablebox":
            continue
        if kind == "table":
            flush()
            if out and isinstance(out[-1], list):  # the same table continuing on the next page
                out[-1] += item
            else:
                out.append(item)
            last = None
            continue
        line = item
        text = line["text"].strip()
        if not text:
            continue
        gap = (line["top"] - last["bottom"]) if last else 0
        new_block = last is None or gap > BODY_GAP * (last["bottom"] - last["top"]) * 0.35
        heading = None
        if line["bold"] and line["size"] >= TITLE_SIZE:
            heading = "# "
        elif line["bold"] and line["size"] == BOLD_SECTION:
            heading = "## " if re.match(r"\d+\. ", text) else "# "
        elif line["bold"] and line["size"] == BOLD_SUB:
            heading = "### "
        elif line["bold"] and re.match(r"[A-F]\. ", text):
            heading = "#### "
        if heading:
            wraps = (
                last
                and last.get("heading") == heading
                and gap < 6
                and not re.match(r"(\d+\.\d* |Step \d)", text)
            )
            if wraps:
                para.append(text)  # a heading that wraps onto a second line
            else:
                flush()
                para = [heading + text]
            line = {**line, "heading": heading}
        elif text.startswith("●"):
            flush()
            para = ["- " + text.lstrip("● ").strip()]
        elif re.match(r"\d+\. ", text):  # a numbered list item (not bold: not a heading)
            flush()
            para = [text]
        elif new_block or (line["x0"] != last["x0"] and not para):
            flush()
            para = [text]
        elif last.get("heading"):
            flush()
            para = [text]
        elif para and is_item(para[0]) and not new_block:
            para[0] += " " + text  # a list item that wraps
        else:
            para.append(text)
        last = line
    flush()
    # bullets of one list sit together; everything else is separated by a blank line
    text, previous = "", None
    for block in out:
        block = table_markdown(block) if isinstance(block, list) else block
        item = is_item(block)
        glue = "" if previous is None else "\n" if item and previous else "\n\n"
        text += glue + block
        previous = item
    return text.replace("-  ", "- ") + "\n"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    pdf_path, md_path = argv
    md = convert(pdf_path)
    with open(md_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(md)
    print(f"{md_path}: {md.count(chr(10))} lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
