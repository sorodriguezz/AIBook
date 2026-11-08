from zipfile import ZipFile

import pytest

from folio_engine import sample
from folio_engine.errors import UserError
from folio_engine.eta import EtaTracker
from folio_engine.formats import layout_check
from folio_engine.formats.epub import EpubBook
from folio_engine.llm.server_manager import ServerConfig
from folio_engine.store import Store
from folio_engine.translate.pipeline import Pipeline, Settings
from folio_engine.translate.segments import Segment

from .make_fixtures import make_epub
from .test_pipeline import FakeClient, _noop


def test_bad_model_markup_keeps_original_link_and_validates_assets(tmp_path):
    src = make_epub(tmp_path / "original.epub")
    book = EpubBook(src)
    with book.zip:
        seg = next(s for s in book.extract() if "gripping" in s.source)
        assert book.apply({seg.id: "Una traducción que perdió el enlace."}, "es") == [seg.id]
        target = tmp_path / "translated.epub"
        book.write(target)
        edited = {*book.docs, book.opf_path}
    layout_check.epub(src, target, edited)
    chapter = "OEBPS/text/ch1.xhtml"
    broken = tmp_path / "broken.epub"
    with ZipFile(target) as original, ZipFile(broken, "w") as out:
        assert b'href="#n1"' in original.read(chapter)
        for name in original.namelist():
            data = original.read(name)
            out.writestr(name, data.replace(b' href="#n1"', b"") if name == chapter else data)
    with pytest.raises(UserError, match="protected markup"):
        layout_check.epub(src, broken, edited)


def test_pdf_table_warning_uses_page_number(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    source, target = tmp_path / "original.pdf", tmp_path / "translated.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page()
        for x in (50, 150, 250, 350):
            page.draw_line((x, 50), (x, 140))
        for y in (50, 80, 110, 140):
            page.draw_line((50, y), (350, y))
        for y in (70, 100, 130):
            for x in (60, 160, 260):
                page.insert_text((x, y), "Cell")
        doc.save(source)
        page.insert_text((130, 76), "Extendedword", fontsize=9)
        doc.save(target)
    assert {"code": "table_overflow", "page": 1} in layout_check.pdf(source, target, [0], lambda: False)


@pytest.mark.asyncio
async def test_short_context_splits_a_batch_without_losing_paragraphs(tmp_path):
    class BudgetModel(FakeClient):
        async def tokenize_count(self, _text):
            return 1500

    store = Store(tmp_path / "data.db")
    segs = [Segment(str(i), 0, "para", "The lantern shines brightly over the calm river this evening.") for i in range(2)]
    store.add_segments("test", segs)
    client = BudgetModel()
    pipe = Pipeline(client, store, "test", Settings("en", "es", "fast", context_limit=2048), EtaTracker(), _noop, lambda: False)
    await pipe.translate_all(segs, {})
    assert len(store.translations("test")) == 2
    assert len(client.calls) >= 2


@pytest.mark.asyncio
async def test_real_book_sample_uses_selected_profile(tmp_path):
    src = make_epub(tmp_path / "sample.epub")
    config = ServerConfig.from_options({"model_path": "small.gguf", "low_memory": True})

    class Manager:
        async def ensure(self, selected):
            assert selected == config
            assert selected.cache_ram_mb == 128 and selected.micro_batch_size == 64
            return FakeClient()

    preview = await sample.run(src, Manager(), config, "en", "es", "best", "technical", "adapt", None, "full", True)
    assert preview["kind"] == "epub" and preview["source_tokens"] > 0
    assert any("Mira" in row["source"] and row["target"] for row in preview["items"])
