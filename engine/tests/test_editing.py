import asyncio
import json
import zipfile

import pytest

from folio_engine import editing, glossary
from folio_engine.errors import UserError
from folio_engine.exports import Exporter, output_path
from folio_engine.formats.epub import EpubBook
from folio_engine.store import Store
from folio_engine.translate.pipeline import seeded_entries

from .make_fixtures import make_epub
from .test_epub import fake_translate


def _job(store: Store, path, **kw) -> dict:
    return store.create_job(input_path=str(path), format="epub", title="T", src="en", tgt="es",
                            quality="best", model_id="m", options={"output_dir": str(path.parent)}, **kw)


def _translated(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    src = make_epub(tmp_path / "in.epub")
    job = _job(store, src)
    segs = EpubBook(src).extract()
    store.add_segments(job["id"], segs)
    for s in segs:
        store.save_final(job["id"], s.id, fake_translate(s.source))
    return store, job, segs


def test_edit_keeps_markers_and_survives_pipeline(tmp_path):
    store, job, segs = _translated(tmp_path)
    seg = next(s for s in segs if "Mira" in s.source)
    with pytest.raises(UserError) as e:
        editing.save(store, job, seg.id, "Sin marcadores")
    assert e.value.code == "edit_markup"

    fixed = fake_translate(seg.source).replace("«", "").replace("»", "")
    row = editing.save(store, job, seg.id, fixed)
    assert row["edited"] == 1 and row["final"] == fixed and row["flags"] is None
    # A later pipeline pass (resume, review) must not overwrite the user's choice.
    store.save_final(job["id"], seg.id, "otra cosa")
    store.save_draft(job["id"], seg.id, "otra cosa", ["x"])
    assert store.segment(job["id"], seg.id)["final"] == fixed


def test_pdf_markup_is_checked():
    assert editing.check_markup("Hello <style id='1'>world</style> {v1}", "Hola <style id='1'>mundo</style> {v1}") is None
    assert editing.check_markup("Hello <style id='1'>world</style>", "Hola mundo") is not None


def test_find_and_count_segments(tmp_path):
    store, job, segs = _translated(tmp_path)
    store.save_draft(job["id"], segs[3].id, "x", ["too short"])
    rows, total = store.find_segments(job["id"], flagged=True)
    assert total == 1 and rows[0]["seg_id"] == segs[3].id
    rows, total = store.find_segments(job["id"], query="Mira")
    assert total >= 1
    assert store.counts(job["id"])["flagged"] == 1


def test_record_paragraph_keeps_edits(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.record_paragraph("j", "pdf:a", 3, "Hello", "Hola")
    store.record_paragraph("j", "pdf:b", 3, "Bye", "Adiós")
    store.save_edit("j", "pdf:a", "¡Hola!")
    store.record_paragraph("j", "pdf:a", 3, "Hello", "Hola otra vez")  # BabelDOC resumes
    assert store.translations("j") == {"pdf:a": "¡Hola!", "pdf:b": "Adiós"}
    assert [r["idx"] for r in store.segments("j")] == [0, 1]


def test_partial_epub_export(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    src = make_epub(tmp_path / "in.epub")
    job = _job(store, src)
    segs = EpubBook(src).extract()
    store.add_segments(job["id"], segs)
    first = next(s for s in segs if "Mira" in s.source)
    store.save_final(job["id"], first.id, fake_translate(first.source))
    events = []

    async def publish(ev):
        events.append(ev)

    async def run():
        exp = Exporter(store, publish, tmp_path)
        out = await exp.start(store.get_job(job["id"]), "parcial")
        await exp.tasks[job["id"]]
        return out

    out = asyncio.run(run())
    assert out == output_path(job, "parcial") and out.name == "in (Español) — parcial.epub"
    assert events[-1]["state"] == "done", events[-1]
    text = "".join(zipfile.ZipFile(out).read(n).decode("utf-8", "ignore") for n in zipfile.ZipFile(out).namelist())
    assert "«MIRA" in text.upper() or "MIRA" in text  # translated paragraph
    assert "Loose text inside a container" in text  # untranslated one kept as original
    assert store.get_job(job["id"])["output_path"] is None  # a partial never replaces the output


def test_glossary_roundtrip_and_seed(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    job = _job(store, tmp_path / "x.epub", bible={
        "style": {"tone": "dark"}, "characters": [{"source": "Ruby", "target": "Rubí", "locked": True}],
        "terms": [{"source": "Frostblood", "target": "Sangre de Escarcha"}]})
    path = tmp_path / "g.json"
    glossary.export(job, path)
    data = json.loads(path.read_text())
    assert data["format"] == "aibook-glossary" and "locked" not in data["characters"][0]
    g = glossary.read(path)
    glossary.check_pair(g, "en", "es")
    with pytest.raises(UserError):
        glossary.check_pair(g, "en", "fr")
    seeded = seeded_entries(g)
    assert seeded["characters"][0]["locked"] and seeded["terms"][0]["target"] == "Sangre de Escarcha"
    path.write_text("{}")
    with pytest.raises(UserError):
        glossary.read(path)


class _FakeClient:
    def __init__(self):
        self.messages = None

    async def chat(self, messages, **kw):
        from types import SimpleNamespace
        self.messages = messages
        return SimpleNamespace(text='<seg id="1">⟦1⟧No esta noche⟦/1⟧, dijo Mira junto a la ⟦2⟧linterna⟦/2⟧.</seg>')


class _FakeLLM:
    running, config = False, None

    def __init__(self):
        self.client = _FakeClient()

    async def ensure(self, config):
        return self.client


def test_retranslate_uses_context_and_instruction(tmp_path):
    store, job, segs = _translated(tmp_path)
    job = store.get_job(job["id"])
    job["options"]["model_path"] = "m.gguf"
    seg = next(s for s in segs if "Mira" in s.source)
    llm = _FakeLLM()
    out = asyncio.run(editing.retranslate(store, llm, None, job, seg.id, "más coloquial"))
    assert out["text"].startswith("⟦1⟧No esta noche")
    user = llm.client.messages[-1]["content"]
    assert "más coloquial" in user and "Current translation" in user
    assert store.segment(job["id"], seg.id)["edited"] == 0  # only a proposal


def test_retranslate_refuses_other_model_while_busy(tmp_path):
    store, job, segs = _translated(tmp_path)
    job = store.get_job(job["id"])
    job["options"]["model_path"] = "m.gguf"
    llm = _FakeLLM()
    llm.running, llm.config = True, object()
    with pytest.raises(UserError) as e:
        asyncio.run(editing.retranslate(store, llm, {"id": "other"}, job, segs[0].id, ""))
    assert e.value.code == "model_busy"
