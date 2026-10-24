import zipfile

import pytest

pymupdf = pytest.importorskip("pymupdf")

from folio_engine.formats import convert  # noqa: E402
from folio_engine.formats.epub import EpubBook  # noqa: E402

from .make_fixtures import _png, make_epub  # noqa: E402


def make_pdf(path):
    """Two chapters, running header/footer, a paragraph split by a page break, a table and an image."""
    doc = pymupdf.open()
    body = ("The river carried the boat past the last houses of Hollin, where the lamps were already "
            "lit and the dogs barked at nothing. Mira counted the bridges as they slid overhead.")
    for n in range(1, 5):
        page = doc.new_page(width=420, height=595)
        page.insert_text((150, 30), "THE LANTERN OF VAELMOOR", fontsize=8)  # running header
        page.insert_text((205, 575), str(n), fontsize=8)  # page number
        y = 70
        if n in (1, 3):
            page.insert_text((40, y + 20), f"Chapter {1 if n == 1 else 2}", fontsize=20)
            y += 50
        for _ in range(3):
            rect = pymupdf.Rect(40, y, 380, y + 70)
            page.insert_textbox(rect, body, fontsize=10.5, fontname="times-roman")
            y += 80
        if n == 1:
            # a sentence that continues on the next page
            page.insert_textbox(pymupdf.Rect(40, 500, 380, 550), "She held her breath and waited for the", fontsize=10.5,
                                fontname="times-roman")
        if n == 2:
            page.insert_textbox(pymupdf.Rect(40, 48, 380, 70), "signal from the far bank of the river.", fontsize=10.5,
                                fontname="times-roman")
            page.insert_image(pymupdf.Rect(140, 330, 280, 430), stream=_png(64, 64))
        if n == 4:
            tbl_y = 330
            for r, row in enumerate([("Item", "Price"), ("Lantern oil", "3 coppers"), ("Rope", "5 coppers")]):
                for c, cell in enumerate(row):
                    rect = pymupdf.Rect(60 + c * 140, tbl_y + r * 22, 200 + c * 140, tbl_y + (r + 1) * 22)
                    page.draw_rect(rect, color=(0, 0, 0), width=0.6)
                    page.insert_text((rect.x0 + 5, rect.y1 - 7), cell, fontsize=10)
    doc.set_metadata({"title": "The Lantern of Vaelmoor", "author": "A. Writer"})
    doc.save(path)
    return path


def test_pdf_to_epub_reflow(tmp_path):
    src = make_pdf(tmp_path / "book.pdf")
    out = convert.pdf_to_epub_reflow(src, tmp_path / "book.epub", lang="en")
    with zipfile.ZipFile(out) as z:
        assert z.namelist()[0] == "mimetype"
        chapters = sorted(n for n in z.namelist() if n.startswith("OEBPS/text/"))
        text = "".join(z.read(n).decode() for n in chapters)
        opf = z.read("OEBPS/content.opf").decode()
        assert any(n.startswith("OEBPS/images/i") for n in z.namelist())
    assert len(chapters) == 2
    assert "<h1>Chapter 1</h1>" in text and "<h1>Chapter 2</h1>" in text
    assert "THE LANTERN OF VAELMOOR" not in text  # running header removed
    assert "waited for the signal from the far bank" in text  # rejoined across pages
    assert "<table>" in text and "Lantern oil" in text
    assert "<dc:creator>A. Writer</dc:creator>" in opf
    # Our own EPUB reader (used by the translator) can read the result.
    assert len(EpubBook(out).extract()) > 5


def test_pdf_to_epub_fixed(tmp_path):
    src = make_pdf(tmp_path / "book.pdf")
    out = convert.pdf_to_epub_fixed(src, tmp_path / "fixed.epub", dpi=60)
    with zipfile.ZipFile(out) as z:
        opf = z.read("OEBPS/content.opf").decode()
        pages = [n for n in z.namelist() if n.startswith("OEBPS/text/")]
    assert "pre-paginated" in opf and len(pages) == 4


def test_epub_to_pdf(tmp_path):
    src = make_epub(tmp_path / "in.epub")
    seen = []
    out = convert.epub_to_pdf(src, tmp_path / "out.pdf", page="6x9", on_progress=lambda f, d: seen.append(f))
    doc = pymupdf.open(out)
    assert doc.page_count >= 1
    assert round(doc[0].rect.width) == 432
    text = "".join(p.get_text() for p in doc)
    assert "Old Tobben" in text and "Lantern oil" in text
    assert len(doc[0].get_images()) == 1
    assert doc.get_toc()[0][1].startswith("Chapter One")
    assert seen[-1] == 1.0


def test_cancel(tmp_path):
    src = make_pdf(tmp_path / "book.pdf")
    with pytest.raises(convert.Cancelled):
        convert.pdf_to_epub_fixed(src, tmp_path / "x.epub", dpi=40, should_stop=lambda: True)
