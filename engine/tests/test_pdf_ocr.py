from types import SimpleNamespace

import pytest

pymupdf = pytest.importorskip("pymupdf")
np = pytest.importorskip("numpy")

from folio_engine.exports import Exporter  # noqa: E402
from folio_engine.formats import ocr, pdf  # noqa: E402
from folio_engine.jobs import EventBus, JobRunner  # noqa: E402
from folio_engine.store import Store  # noqa: E402

from .make_fixtures import _png  # noqa: E402


@pytest.mark.asyncio
async def test_scan_with_blank_page_uses_ocr_and_supports_edited_export(tmp_path, monkeypatch):
    source, out, rebuilt = (tmp_path / name for name in ("in.pdf", "out.pdf", "rebuilt.pdf"))
    with pymupdf.open() as doc:
        scan = doc.new_page()
        # A scan with large margins still has no digital text to send to BabelDOC.
        scan.insert_image(pymupdf.Rect(100, 100, 450, 300), stream=_png(40, 20))
        doc.new_page()
        doc.save(source)

    text = "A scanned page with several words to translate."
    boxes = np.array([[(120, y), (420, y), (420, y + 11), (120, y + 11)]
                      for y in (120, 133, 146)], dtype=np.float32) * (ocr.OCR_DPI / 72)
    recognized = SimpleNamespace(boxes=boxes, txts=[text] * 3, scores=[0.9] * 3)
    monkeypatch.setattr(ocr, "_engine", lambda _: lambda _: recognized)

    async def unexpected(*args, **kwargs):
        pytest.fail("BabelDOC must not receive a PDF with no digital text")

    async def translate(texts):
        return ["Este es el texto traducido."] * len(texts)

    async def noop(*_):
        pass

    monkeypatch.setattr(pdf, "translate_digital", unexpected)
    info = pdf.read_info(source)
    assert info["scanned_pages"] == [0]
    assert info["digital_pages"] == []
    store = Store(tmp_path / "folio.db")
    job = store.create_job(format="pdf", input_path=str(source), src="en", tgt="es",
                           quality="fast", bible={}, model_id="test")
    runner = JobRunner(store, SimpleNamespace(generation_rate=noop), EventBus(), tmp_path)
    await runner._run_pdf(job, SimpleNamespace(bible={}, translate_texts=translate), out)
    with pymupdf.open(out) as doc:
        assert len(doc) == 2 and "texto traducido" in doc[0].get_text()
        assert not doc[1].get_text().strip()
    rows = store.segments(job["id"])
    assert len(rows) == 1
    store.save_edit(job["id"], rows[0]["seg_id"], "Traducción corregida.")

    def no_ocr(*_):
        pytest.fail("resuming and exporting must reuse the saved OCR page")

    monkeypatch.setattr(ocr, "ocr_page", no_ocr)
    store = Store(tmp_path / "folio.db")
    runner = JobRunner(store, SimpleNamespace(generation_rate=noop), EventBus(), tmp_path)
    await runner._run_pdf(job, SimpleNamespace(bible={}, translate_texts=unexpected), out)
    assert store.recent_translations(job["id"])[0]["final"] == "Traducción corregida."
    await Exporter(store, noop, tmp_path)._write_pdf(job, store.translations(job["id"]), rebuilt)
    with pymupdf.open(rebuilt) as doc:
        assert "Traducción corregida." in doc[0].get_text()
