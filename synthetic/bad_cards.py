"""Cards that must be refused, one file per kind of failure (E-13), with what each should return.

Built from one clean card (FX-01's latest). Each file is broken in exactly one way, so a parser
that reports the right failure kind and place has found the actual fault.

    not_a_pdf.pdf       text with a .pdf name
    image_only.pdf      the card drawn as a picture: no text layer
    wrong_layout.pdf    the title line is missing
    no_id_number.pdf    the ID number field is missing
    unreadable_row.pdf  one subject row prints four numbers instead of five
    year_mismatch.pdf   the title says one school year, the Academic year field another
"""

from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont

from .cards import FIXED_DATE, SCHOOL_TITLE, UNREADABLE_ROW, render_card
from .maths import two_places

NOT_A_PDF = b"This is plain text with a .pdf name. It is not a PDF.\n"


def image_only_pdf(card):
    """The card as a picture on one page. No text layer, so nothing can be read without OCR."""
    img = Image.new("L", (850, 1100), 255)
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    lines = [
        SCHOOL_TITLE,
        f"ANNUAL REPORT CARD - {card['stage'].upper()} - ACADEMIC YEAR {card['year']}",
        f"Name: {card['name']}    ID number: {card['learner_id']}",
        "",
    ]
    for s in card["subjects"]:
        terms = "  ".join("-" if t is None else two_places(t) for t in s["terms"])
        lines.append(f"{s['name']:<24}{int(s['coef'])}   {terms}")
    for k, line in enumerate(lines):
        d.text((60, 60 + k * 22), line, fill=0, font=font)
    pdf = FPDF(unit="pt", format="Letter")
    pdf.set_compression(False)
    pdf.set_creation_date(FIXED_DATE)
    pdf.set_producer("MDEIS synthetic card generator")
    pdf.add_page()
    pdf.image(img, x=0, y=0, w=612)
    return bytes(pdf.output())


def bad_cards(card, rules):
    """Return {file name: (bytes, what a parser must report)}."""
    out = {"not_a_pdf.pdf": (NOT_A_PDF, {"failure_kind": "not_a_pdf", "page": None, "row": None})}
    out["image_only.pdf"] = (
        image_only_pdf(card),
        {"failure_kind": "no_text_layer", "page": 1, "row": None},
    )
    data, _ = render_card(card, rules, "wrong_layout")
    out["wrong_layout.pdf"] = (
        data,
        {"failure_kind": "layout_unrecognised", "page": 1, "row": None},
    )
    data, _ = render_card(card, rules, "no_id_number")
    out["no_id_number.pdf"] = (
        data,
        {"failure_kind": "learner_ref_missing", "page": 1, "row": None},
    )
    data, truth = render_card(card, rules, "unreadable_row")
    row = truth["rows"][UNREADABLE_ROW - 1]
    out["unreadable_row.pdf"] = (
        data,
        {"failure_kind": "row_unreadable", "page": row["page"], "row": UNREADABLE_ROW},
    )
    data, _ = render_card(card, rules, "year_mismatch")
    out["year_mismatch.pdf"] = (data, {"failure_kind": "year_mismatch", "page": 1, "row": None})
    return out
