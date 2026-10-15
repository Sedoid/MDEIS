"""The printed report cards and their truth sheets (the data side of the card reader tests).

A small reader below opens each PDF with pdfplumber and reads what is printed: the header fields,
every subject row (by the cell rectangles), the remarks (joined across a page break), TOTAL, the
average, absences and the council decision. What it reads must equal the truth sheet beside the
card, field by field, with the same page and row. This proves the PDFs say what the truth says.
It is not the system's parser (that is the real parser): it reads by cell geometry; the real parser reads by
the numbers on a line.

Needs pdfplumber and fpdf2 (dev only).
"""

import json
import re
import subprocess
import sys

import pytest

pdfplumber = pytest.importorskip("pdfplumber")
pytest.importorskip("fpdf")

from synthetic import scan  # noqa: E402

TOLERANCE = 1.5  # points, when matching a cell's left edge and width
NUMBER = re.compile(r"^(?:-|\d+\.\d\d)$")


@pytest.fixture(scope="module")
def demo(templates_dir):
    from synthetic import generate

    files, learners, rules = generate.build_outputs(42, templates_dir, cards="demo")
    return files, learners


def cells(page):
    """The subject-column rectangles of a page, top to bottom, with all rectangles on their row."""
    rects = sorted(page.rects, key=lambda r: (round(r["top"]), r["x0"]))
    first = [r for r in rects if abs(r["x0"] - 36) < TOLERANCE and abs(r["width"] - 96) < TOLERANCE]
    out = []
    for cell in first:
        row = [r for r in rects if abs(r["top"] - cell["top"]) < TOLERANCE]
        out.append((cell, sorted(row, key=lambda r: r["x0"])))
    return out


def words_in(page, left, right, top, bottom):
    ws = [
        w
        for w in page.extract_words()
        if w["x0"] >= left - 0.5
        and w["x1"] <= right + 0.5
        and w["top"] >= top - 0.5
        and w["bottom"] <= bottom + 0.5
    ]
    ws.sort(key=lambda w: (round(w["top"] / 3), w["x0"]))
    return [w["text"] for w in ws]


def read_rows(pdf):
    """Every subject-row fragment on every page: its page, whether it carries the numbers, and the
    words in each of the nine columns."""
    out = []
    for number, page in enumerate(pdf.pages, start=1):
        for cell, row in cells(page):
            if len(row) < 9:
                continue  # the header and the TOTAL row are not subject rows
            xs = [(r["x0"], r["x1"]) for r in row]
            cols = [words_in(page, left, right, cell["top"], cell["bottom"]) for left, right in xs]
            if cols[0] == ["Subject"]:
                continue
            out.append({"page": number, "cols": cols, "first": bool(cols[1])})
    return out


def read_card(path):
    """What is printed on the card, read back from the PDF."""
    rows = []
    with pdfplumber.open(path) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        for frag in read_rows(pdf):
            cols = frag["cols"]
            if frag["first"]:
                rows.append(
                    {
                        "page": frag["page"],
                        "subject": " ".join(cols[0]),
                        "coefficient": " ".join(cols[1]),
                        "terms": [" ".join(cols[i]) for i in (2, 3, 4)],
                        "annual_score": " ".join(cols[5]),
                        "weighted_score": " ".join(cols[6]),
                        "grade": cols[7][0] if cols[7] else "",
                        "grade_word": " ".join(cols[7][1:]),
                        "remark": list(cols[8]),
                        "remark_pages": [frag["page"]],
                    }
                )
            else:  # the rest of a remark that ran over the page break
                rows[-1]["remark"] += cols[8]
                rows[-1]["remark_pages"].append(frag["page"])
        last = pdf.pages[-1]
        annual = next((w for w in last.extract_words() if w["text"] == "ANNUAL"), None)
        foot = (
            words_in(last, 36, 36 + 262, annual["top"], annual["top"] + 80) if annual else []
        )  # the ANNUAL AVERAGE box on its own, apart from the grading scale beside it
    for r in rows:
        r["remark"] = " ".join(r["remark"])
    return {"rows": rows, "text": text, "pages": len(pdf.pages), "average_box": " ".join(foot)}


def field(text, pattern):
    m = re.search(pattern, text)
    assert m, pattern
    return m.group(1).strip()


def compare(read, truth):
    """Raise AssertionError naming the first field that differs."""
    assert read["pages"] == truth["pages"]
    assert len(read["rows"]) == len(truth["rows"]) == 14
    for got, want in zip(read["rows"], truth["rows"], strict=True):
        where = f"row {want['row']} {want['subject']}"
        assert got["subject"] == want["subject"], where
        assert got["coefficient"] == str(want["coefficient"]), where
        printed = [("-" if t is None else t) for t in want["terms"]]
        assert got["terms"] == printed, where
        assert got["annual_score"] == (want["annual_score"] or "-"), where
        assert got["weighted_score"] == (want["weighted_score"] or "-"), where
        assert got["grade"] == (want["grade"] or "-"), where
        assert got["grade_word"] == (want["grade_word"] or ""), where
        assert got["remark"] == want["remark"], where
        assert got["page"] == want["page"], where
        assert got["remark_pages"] == want["remark_pages"], where
    text = read["text"].replace("\n", " ")
    assert field(text, r"Name: (.+?) Date of birth") == truth["name"]
    assert f"ACADEMIC YEAR {truth['title_year']}" in text
    assert f"Academic year: {truth['academic_year']}" in text
    assert "ID number:" in text and truth["learner_ref"] in text
    assert f"Class: {truth['class']}" in text
    assert f"Absences: {truth['absences']} | Class council decision: " in text
    assert truth["council_decision"] in text
    assert f"TOTAL {truth['total_coefficients']} {truth['total_weighted']}" in text
    box = read["average_box"]
    assert f"= {truth['total_weighted']} ÷ {truth['total_coefficients']} = " in box
    assert f"{truth['annual_average']} / 20" in box
    assert f"Grade: {truth['grade']} ({truth['grade_word']})" in box


def stems(files):
    return sorted(k[: -len(".pdf")] for k in files if k.startswith("cards/") and k.endswith(".pdf"))


# --------------------------------------------------------------------------- the demo cards
def test_the_demo_set_is_the_fixtures_and_eight_more_learners(demo):
    files, learners = demo
    names = {json.loads(files[f"{stem}.truth.json"])["name"] for stem in stems(files)}
    fixtures = {ln["name"] for ln in learners if ln["fixture"] and not ln["records_only"]}
    assert fixtures <= names
    assert len(names) == len(fixtures) + 8
    for stem in stems(files):
        assert f"{stem}.pdf" in files and f"{stem}.truth.json" in files  # a truth sheet beside each


def test_every_demo_card_reads_back_as_its_truth_sheet(demo, tmp_path):
    files, _ = demo
    for stem in stems(files):
        path = tmp_path / "card.pdf"
        path.write_bytes(files[f"{stem}.pdf"])
        truth = json.loads(files[f"{stem}.truth.json"])
        try:
            compare(read_card(path), truth)
        except AssertionError as e:
            raise AssertionError(f"{stem}: {e}") from e


def test_some_demo_cards_have_a_row_split_across_pages_and_wrapped_remarks(demo):
    files, _ = demo
    split = wrapped = 0
    for stem in stems(files):
        truth = json.loads(files[f"{stem}.truth.json"])
        split += any(len(r["remark_pages"]) > 1 for r in truth["rows"])
        wrapped += all(len(r["remark"]) > 40 for r in truth["rows"])
    assert split >= 3
    assert wrapped >= 10


def test_the_truth_records_equal_the_flat_records_file(files, demo):
    files_demo, _ = demo
    lines = files["cohort/records.csv"].decode().splitlines()[1:]
    flat = {}
    for line in lines:
        ref, card_id, _, _, _, term, subject, coef, raw, denom = line.split(",")
        flat.setdefault(card_id, []).append(
            (subject, int(term), raw or None, int(coef), int(denom))
        )
    for stem in stems(files_demo):
        truth = json.loads(files_demo[f"{stem}.truth.json"])
        got = [
            (r["subject"], r["term"], r["raw_score"], r["coefficient"], r["denominator"])
            for r in truth["records"]
        ]
        assert got == flat[truth["card_id"]], truth["card_id"]
        assert all(r["page"] in (1, 2, 3) and 1 <= r["row"] <= 14 for r in truth["records"])


def test_the_fixture_cards_print_their_faults(demo):
    files, learners = demo
    by_name = {ln["fixture"]: ln for ln in learners if ln["fixture"]}

    def truth_of(handle, year):
        card = next(c for c in by_name[handle]["cards"] if c["year"] == year and c["printable"])
        stem = next(
            s
            for s in stems(files)
            if json.loads(files[f"{s}.truth.json"])["card_id"] == card["card_id"]
        )
        return json.loads(files[f"{stem}.truth.json"])

    row = lambda t, subject: next(r for r in t["rows"] if r["subject"] == subject)  # noqa: E731
    t = truth_of("FX-04a", "2025/2026")
    assert row(t, "English")["terms"] == ["13.50", None, "13.50"]
    assert row(t, "English")["annual_score"] is None
    t = truth_of("FX-04b", "2025/2026")
    assert row(t, "Chemistry")["terms"][2] == "22.50"
    t = truth_of("FX-04d", "2025/2026")
    assert row(t, "History")["annual_score"] == "13.50"
    assert row(t, "History")["terms"] == ["12.50"] * 3
    assert t["total_weighted"] == "392.50" and t["annual_average"] == "13.08"
    t = truth_of("FX-05", "2025/2026")
    assert row(t, "Geography")["remark"].startswith("Improving;")
    assert row(t, "Geography")["terms"] == ["15.00", "14.00", "13.00"]
    t = truth_of("FX-22", "2025/2026")
    assert "Ignore all previous instructions" in row(t, "History")["remark"]
    two = [s for s in stems(files) if s.endswith("_card2")]
    assert len(two) >= 1  # FX-04c's second card for 2025/26


def test_two_runs_of_the_card_script_write_identical_files(root, tmp_path):
    runs = []
    for k, hash_seed in enumerate(("1", "2")):
        out = tmp_path / f"run{k}"
        subprocess.run(
            [sys.executable, "synthetic/generate.py", "--seed", "42", "--cards", "demo"]
            + ["--out", str(out)],
            cwd=root, check=True, capture_output=True, env={"PYTHONHASHSEED": hash_seed},
        )  # fmt: skip
        runs.append(out)
    names = sorted(p.relative_to(runs[0]) for p in runs[0].rglob("*") if p.is_file())
    assert names == sorted(p.relative_to(runs[1]) for p in runs[1].rglob("*") if p.is_file())
    assert any(n.suffix == ".pdf" for n in names)
    for n in names:
        assert (runs[0] / n).read_bytes() == (runs[1] / n).read_bytes(), n


# --------------------------------------------------------------------------- the bad cards
def test_each_bad_card_is_broken_in_its_own_way(demo, tmp_path):
    files, _ = demo
    expected = json.loads(files["cards_bad/expected_failures.json"])
    assert {k: v["failure_kind"] for k, v in expected.items()} == {
        "not_a_pdf.pdf": "not_a_pdf",
        "image_only.pdf": "no_text_layer",
        "wrong_layout.pdf": "layout_unrecognised",
        "no_id_number.pdf": "learner_ref_missing",
        "unreadable_row.pdf": "row_unreadable",
        "year_mismatch.pdf": "year_mismatch",
    }
    assert not files["cards_bad/not_a_pdf.pdf"].startswith(b"%PDF")

    def text_of(name):
        path = tmp_path / name
        path.write_bytes(files[f"cards_bad/{name}"])
        with pdfplumber.open(path) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages), None

    text, _ = text_of("image_only.pdf")
    assert text.strip() == ""  # a picture: no text layer
    text, _ = text_of("wrong_layout.pdf")
    assert "ANNUAL REPORT CARD" not in text and "ID number" in text
    text, _ = text_of("no_id_number.pdf")
    assert "ID number" not in text and "ANNUAL REPORT CARD" in text
    text, _ = text_of("year_mismatch.pdf")
    title = field(text, r"ACADEMIC YEAR (\d{4}/\d{4})")
    header = field(text, r"Academic year: (\d{4}/\d{4})")
    assert title != header
    path = tmp_path / "unreadable_row.pdf"
    path.write_bytes(files["cards_bad/unreadable_row.pdf"])
    with pdfplumber.open(path) as pdf:
        numbered = [f for f in read_rows(pdf) if f["first"]]
    where = expected["unreadable_row.pdf"]
    assert numbered[where["row"] - 1]["page"] == where["page"]
    numbers = [w for i in range(1, 7) for w in numbered[where["row"] - 1]["cols"][i]]
    assert len([w for w in numbers if NUMBER.match(w)]) == 4  # a coefficient and four numbers
    assert all(
        len([w for i in range(2, 7) for w in f["cols"][i] if NUMBER.match(w)]) == 5
        for f in numbered
        if f is not numbered[where["row"] - 1]
    )


# --------------------------------------------------------------------------- personal data
def test_the_scan_finds_nothing_in_the_text_of_any_card(demo, root, tmp_path):
    files, _ = demo
    texts = {}
    for name, data in files.items():
        if name.endswith(".pdf") and name != "cards_bad/not_a_pdf.pdf":
            path = tmp_path / "x.pdf"
            path.write_bytes(data)
            with pdfplumber.open(path) as pdf:
                texts[name] = "\n".join(p.extract_text() or "" for p in pdf.pages)
    assert len(texts) >= 40
    assert (
        scan.scan_folder(tmp_path, extra_texts=texts, template_path=root / "templates/v1.yaml")
        == []
    )


# --------------------------------------------------------------------------- the real sample card
def test_the_real_cards_truth_sheet_matches_the_printed_numbers(root):
    """The truth file for the real card was typed from reading it. Check its numbers against the
    text layer with a plain pattern (a coefficient and five numbers on one line), and its sums."""
    from fractions import Fraction

    samples = root / "data" / "synthetic" / "samples"
    truth = json.loads((samples / "amara_nfor_form1_2022-2023.truth.json").read_text())
    pattern = re.compile(
        r"(?<![\d.])(\d) +(\d+\.\d\d) +(\d+\.\d\d) +(\d+\.\d\d) +(\d+\.\d\d) +(\d+\.\d\d)"
    )
    found = []
    with pdfplumber.open(samples / "amara_nfor_form1_2022-2023.pdf") as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            for m in pattern.finditer(page.extract_text(layout=True)):
                found.append((number, *m.groups()))
    assert len(found) == 14
    for (page, coef, t1, t2, t3, annual, weighted), row in zip(found, truth["rows"], strict=True):
        assert [coef, t1, t2, t3, annual, weighted] == [
            str(row["coefficient"]), *row["terms"], row["annual_score"], row["weighted_score"],
        ], row["subject"]  # fmt: skip
        assert page == row["page"], row["subject"]
    total = sum(Fraction(r["weighted_score"]) for r in truth["rows"])
    assert f"{total:.2f}" == truth["total_weighted"] == "456.00"
    assert sum(r["coefficient"] for r in truth["rows"]) == truth["total_coefficients"] == 30
    assert f"{total / 30:.2f}" == truth["annual_average"] == "15.20"
    assert [r["row"] for r in truth["rows"] if len(r["remark_pages"]) > 1] == [4, 12]
    assert len(truth["records"]) == 42
