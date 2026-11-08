"""Translate a few real passages with the selected settings, without creating a job."""

import asyncio
import time
from contextlib import closing
from pathlib import Path

from .errors import UserError
from .eta import EtaTracker
from .formats import epub, pdf
from .llm.client import LLMUnavailable
from .llm.server_manager import LlamaServerManager, ServerConfig
from .store import Store
from .translate import placeholders
from .translate.pipeline import Pipeline, Settings, seeded_entries
from .translate.segments import Segment, estimate_tokens


def passages(path: Path, src: str, technical: bool) -> tuple[list[Segment], str]:
    if not path.is_file():
        raise UserError("file_not_found", "source file not found", 404)
    ext = path.suffix.lower()
    if ext == ".epub":
        book = epub.EpubBook(path, technical=technical)
        with book.zip:
            content = [s for s in book.extract() if s.kind in ("para", "cell", "caption")]
        # Keep nearby prose for context; add a representative cell for technical books.
        candidates = [s for s in content if s.kind == "para" and 65 <= len(s.source) <= 2000][:3]
        if technical:
            candidates += [s for s in content if s.kind == "cell" and 8 <= len(s.source) <= 500][:1]
        return candidates or [s for s in content if 5 < len(s.source) <= 2000][:2], "epub"
    if ext != ".pdf":
        raise UserError("unsupported_format", "sample requires an EPUB or PDF")

    import pymupdf

    with pymupdf.open(path) as doc:
        # Try early content after the cover, then spread across the book.
        first = range(1, min(6, doc.page_count))
        pages = dict.fromkeys([*first, *pdf.pdf_layout.spread_pages(doc.page_count, 9)])
        for i in pages:
            page = doc[i]
            scanned = pdf.ocr.is_scanned(page, page.get_text())
            if scanned:
                texts = [block.text(src in ("zh", "ja")) for block in pdf.scan_blocks(page, src)]
            else:
                texts = [" ".join(b[4].split()) for b in page.get_text("blocks", sort=True) if b[6] == 0]
            chosen = [text for text in texts if 50 <= len(text) <= 2000][:3]
            if chosen:
                return [Segment(f"sample-{n}", i, "para", text) for n, text in enumerate(chosen)], "ocr" if scanned else "pdf"
    return [], "pdf"


async def run(path: Path, llm: LlamaServerManager, config: ServerConfig, src: str, tgt: str, quality: str,
              book_type: str, honorifics: str, glossary: dict | None, review_scope: str, low_memory: bool) -> dict:
    if low_memory and path.suffix.lower() == ".pdf" and llm.running:
        await llm.stop()
    try:
        segments, source_kind = await asyncio.to_thread(passages, path, src, book_type == "technical")
    finally:
        if low_memory and path.suffix.lower() == ".pdf":
            pdf.ocr.release_models()
    if not segments:
        raise UserError("sample_no_text", "no representative passage found")
    client = await llm.ensure(config)

    async def ignore(_):
        pass

    with closing(Store(Path(":memory:"))) as store:
        store.add_segments("sample", segments)
        eta = EtaTracker()
        settings = Settings(src, tgt, quality, honorifics, book_type, review_scope,
                            config.ctx_size if config.ctx_size < 8192 else None)
        pipe = Pipeline(client, store, "sample", settings, eta, ignore, lambda: False)
        pipe.bible = {**seeded_entries(glossary), "style": (glossary or {}).get("style") or {}}
        started = time.monotonic()
        try:
            await pipe.translate_all(segments, {})
        except LLMUnavailable as exc:
            raise UserError("model_unavailable", str(exc), 503) from exc
        elapsed = time.monotonic() - started
        rows = store.segments("sample")
    return {"kind": source_kind, "seconds": round(elapsed, 1),
            "source_tokens": sum(estimate_tokens(s.source) for s in segments),
            "items": [{"source": placeholders.strip(r["source"]),
                       "target": placeholders.strip(r["final"] or r["draft"] or ""),
                       "chapter": r["chapter"], "flagged": bool(r["flags"])} for r in rows]}
