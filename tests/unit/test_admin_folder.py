"""Which files `ingest-dir` reads: PDFs, in name order, with or without the sub-folders."""

from pathlib import Path

from mdeis import admin


def make(tmp_path: Path):
    for name in ("b.pdf", "a.PDF", "note.html", ".hidden.pdf", "sub one /c.pdf", "sub one /d.txt"):
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"x")


def names(paths, root):
    return [str(p.relative_to(root)) for p in paths]


def test_only_the_top_folder_unless_asked_for_the_sub_folders(tmp_path):
    make(tmp_path)
    pdfs, others = admin.find_cards(tmp_path)
    assert names(pdfs, tmp_path) == ["a.PDF", "b.pdf"]
    assert names(others, tmp_path) == ["note.html"]


def test_sub_folders_with_odd_names_are_read_in_name_order(tmp_path):
    make(tmp_path)
    pdfs, others = admin.find_cards(tmp_path, recursive=True)
    assert names(pdfs, tmp_path) == ["a.PDF", "b.pdf", "sub one /c.pdf"]
    assert names(others, tmp_path) == ["note.html", "sub one /d.txt"]


def test_a_missing_folder_has_nothing_in_it(tmp_path):
    assert admin.find_cards(tmp_path / "nowhere") == ([], [])
