import json
import re

import pytest

from folio_engine.eta import EtaTracker
from folio_engine.formats.epub import EpubBook
from folio_engine.llm.client import Completion
from folio_engine.store import Store
from folio_engine.translate.pipeline import Pipeline, Settings

from .make_fixtures import make_epub

SEG = re.compile(r'<(seg|draft) id="(\d+)"[^>]*>(.*?)</\1>', re.S)

# Tiny "dictionary model": good enough to exercise every code path.
WORDS = {"the": "el", "river": "río", "lantern": "farol", "not": "no", "tonight": "esta noche",
         "late": "tarde", "map": "mapa", "marshes": "marismas", "chapter": "capítulo", "one": "uno"}


def fake_es(text: str) -> str:
    def repl(m):
        w = m.group(0)
        return WORDS.get(w.lower(), w)
    return "ES: " + re.sub(r"[A-Za-z]+", repl, text) + " y la de que"


class FakeClient:
    def __init__(self):
        self.calls = []

    async def chat(self, messages, **kw):
        user = messages[-1]["content"]
        self.calls.append(("review" if "<draft" in user else "translate", user))
        if "<draft" in user:
            drafts = {i: t for kind, i, t in SEG.findall(user) if kind == "draft"}
            out = "\n".join(f'<seg id="{i}">{t.replace("ES: ", "ES✓ ")}</seg>' for i, t in drafts.items())
        else:
            body = user.split("containing only the translation:")[-1]
            out = "\n".join(f'<seg id="{i}">{fake_es(t)}</seg>' for kind, i, t in SEG.findall(body) if kind == "seg")
        return Completion(out, 500, 120, 0.5, 3.0, 3.5)

    async def chat_json(self, messages, **kw):
        user = messages[-1]["content"]
        if "list NEW named characters" in user:
            data = {"characters": [{"source": "Mira", "gender": "female", "voice": "wry, guarded"},
                                   {"source": "Old Tobben", "gender": "male", "voice": "gruff boatman"}],
                    "terms": [{"source": "Vaelmoor", "note": "marsh region"}]}
        elif "Characterize this" in user:
            data = {"genre": "fantasy", "tone": "brooding", "narration": "third person past",
                    "register": "literary", "notes": ""}
        else:
            items = json.loads(user[user.rindex("["):])
            data = {"items": [{"source": i["source"], "target": i["source"]} for i in items]}
        return data, Completion(json.dumps(data), 800, 100, 1.0, 2.0, 3.0)


async def _noop(_):
    pass


@pytest.mark.asyncio
async def test_best_quality_pipeline_end_to_end(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    job = store.create_job(input_path="x.epub", format="epub", src="en", tgt="es", quality="best", model_id="m")
    book = EpubBook(make_epub(tmp_path / "in.epub"))
    segs = book.extract()
    store.add_segments(job["id"], segs)
    events = []

    async def emit(ev):
        events.append(ev)

    client = FakeClient()
    eta = EtaTracker()
    pipe = Pipeline(client, store, job["id"], Settings("en", "es", "best"), eta, emit, lambda: False)
    pipe.plan(segs, {})
    bible = await pipe.build_bible(segs)
    assert {c["source"] for c in bible["characters"]} == {"Mira", "Old Tobben"}
    assert bible["style"]["genre"] == "fantasy"

    await pipe.translate_all(segs, {})
    tr = store.translations(job["id"])
    assert len(tr) == len(segs)
    assert all(t.startswith("ES✓") for t in tr.values()), "review pass should produce finals"
    assert any(c[0] == "review" for c in client.calls)
    # The glossary reaches the translator only when the name appears in the batch.
    phases = {e["phase"] for e in events}
    assert {"analyzing", "translating", "reviewing"} <= phases
    assert events[-1]["progress"] > 0.9
    assert any("preview" in e for e in events)

    book.apply(tr, "es")
    book.write(tmp_path / "out.epub")


@pytest.mark.asyncio
async def test_resume_skips_finished_segments(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    job = store.create_job(input_path="x.epub", format="epub", src="en", tgt="es", quality="high", model_id="m")
    segs = EpubBook(make_epub(tmp_path / "in.epub")).extract()
    store.add_segments(job["id"], segs)
    client = FakeClient()
    pipe = Pipeline(client, store, job["id"], Settings("en", "es", "high"), EtaTracker(), _noop, lambda: False)
    pipe.bible = {"style": {}, "characters": [], "terms": []}
    await pipe.translate_all(segs, {})
    first_calls = len(client.calls)

    done = {r["seg_id"]: r for r in store.segments(job["id"])}
    client2 = FakeClient()
    pipe2 = Pipeline(client2, store, job["id"], Settings("en", "es", "high"), EtaTracker(), _noop, lambda: False)
    pipe2.bible = pipe.bible
    await pipe2.translate_all(segs, done)
    assert first_calls > 0 and len(client2.calls) == 0
