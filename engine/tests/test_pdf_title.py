from pathlib import Path

import pytest

pymupdf = pytest.importorskip("pymupdf")
from folio_engine.formats.pdf import _title  # noqa: E402


def _doc(*lines, title=""):
    doc = pymupdf.open()
    page = doc.new_page()
    for i, (text, size) in enumerate(lines):
        page.insert_text((72, 100 + i * 60), text, fontsize=size)
    doc.set_metadata({"title": title})
    return doc


def test_title_from_cover_when_metadata_missing():
    doc = _doc(("RYAN SINGER", 9), ("Shape Up", 36), ("Stop Running in Circles", 12))
    assert _title(doc, Path("shape-up.pdf")) == "Shape Up"


def test_metadata_wins_unless_junk():
    assert _title(_doc(("Big", 30), title="Nightblood"), Path("x.pdf")) == "Nightblood"
    assert _title(_doc(("Shape Up", 30), title="draft.indd"), Path("x.pdf")) == "Shape Up"


def test_falls_back_to_tidy_file_name():
    assert _title(_doc(("Tabla de contenidos", 24)), Path("shape-up.pdf")) == "Shape up"
    assert _title(_doc(), Path("my_book.pdf")) == "My book"
