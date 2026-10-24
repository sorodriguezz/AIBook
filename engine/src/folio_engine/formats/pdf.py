"""PDF translation.

Digital pages go through BabelDOC, which rebuilds each page from an
intermediate representation so text lands in the original boxes while images,
vector graphics, tables and formulas are untouched. Our book bible is injected
as BabelDOC's system prompt and glossary, so PDFs get the same literary
treatment as EPUBs. Scanned pages are handled afterwards by the OCR path.
"""

import asyncio
import hashlib
import inspect
import logging
import shutil
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from ..translate import prompts
from ..translate.segments import Segment
from . import ocr

log = logging.getLogger(__name__)

TRANSLATE_STAGE = "Translate Paragraphs"  # BabelDOC stage that calls the model
BABELDOC_LANG = {"zh": "zh-CN", "ja": "ja", "en": "en", "es": "es", "fr": "fr", "de": "de", "it": "it"}


def read_info(path: Path) -> dict:
    import pymupdf

    with pymupdf.open(path) as doc:
        from ..translate.segments import estimate_tokens

        scanned = [i for i, page in enumerate(doc) if ocr.is_scanned(page)]
        texts = [page.get_text("text") for page in doc]
        tokens = sum(estimate_tokens(t) for t in texts)
        if scanned:  # assume scanned pages are as dense as the digital ones
            digital = max(1, doc.page_count - len(scanned))
            tokens += int(tokens / digital * len(scanned)) if tokens else 450 * len(scanned)
        title = (doc.metadata or {}).get("title") or path.stem
        return {"title": title, "pages": doc.page_count, "scanned_pages": scanned,
                "sample": " ".join(texts)[:4000], "language": None, "tokens": tokens}


def bible_segments(path: Path, src: str, scanned: list[int]) -> list[Segment]:
    """Plain-text segments used only to analyse the book before translation."""
    import pymupdf

    segs: list[Segment] = []
    with pymupdf.open(path) as doc:
        for i, page in enumerate(doc):
            if i in scanned:
                blocks = ocr.group_blocks(ocr.ocr_page(page, src))
                texts = [b.text(src in ("ja", "zh")) for b in blocks]
            else:
                texts = [b[4] for b in page.get_text("blocks") if b[6] == 0]
            for j, t in enumerate(texts):
                t = " ".join(t.split())
                if len(t) > 3:
                    segs.append(Segment(f"p{i}b{j}", i // 15, "para", t))
    return segs


def _custom_prompt(src: str, tgt: str, bible: dict | None) -> str:
    chars = (bible or {}).get("characters", [])[:60]
    terms = (bible or {}).get("terms", [])[:80]
    honor = (bible or {}).get("honorifics", "adapt")
    base = prompts.translator_system(src, tgt, bible, chars, terms, honor)
    # BabelDOC uses its own markup and output format; drop our marker/format rules.
    return "\n".join(l for l in base.splitlines() if "⟦" not in l and "requested format" not in l)


def _glossary(bible: dict | None, tgt: str):
    from babeldoc.glossary import Glossary, GlossaryEntry

    entries = [
        GlossaryEntry(e["source"], e["target"], BABELDOC_LANG[tgt])
        for e in (bible or {}).get("characters", []) + (bible or {}).get("terms", [])
        if e.get("source") and e.get("target")
    ]
    return [Glossary("folio-bible", entries)] if entries else []


ProgressCb = Callable[..., Awaitable[None]]  # (stage, fraction, current=None, total=None)


@dataclass
class ParagraphHooks:
    """Per-run taps into BabelDOC's paragraph translation.

    `record(source, translation, page)` sees every translated paragraph, so the
    text can be reviewed and edited later. `replay(source)` supplies stored
    translations instead of calling the model, which rebuilds the PDF after
    edits (or from a half-translated book) in minutes.
    """
    record: Callable[[str, str, int | None], None] | None = None
    replay: Callable[[str], str | None] | None = None


def paragraph_id(text: str) -> str:
    """Stable id for a BabelDOC paragraph: the same text maps to the same translation."""
    return "pdf:" + hashlib.sha1(text.encode()).hexdigest()[:20]


# Keyed by id(TranslationConfig): BabelDOC's classes are patched once, and each
# run (a translation and an export may overlap) finds its own hooks.
_HOOKS: dict[int, ParagraphHooks] = {}
_local = threading.local()
_installed = False


def _install_hooks() -> None:
    global _installed
    if _installed:
        return
    from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
    from babeldoc.format.pdf.document_il.midend.il_translator_llm_only import ILTranslatorLLMOnly

    orig_post = ILTranslator.post_translate_paragraph
    orig_batch = ILTranslatorLLMOnly.translate_paragraph
    batch_sig = inspect.signature(orig_batch)

    def post_translate_paragraph(self, paragraph, tracker, translate_input, translated_text):
        hooks = _HOOKS.get(id(self.translation_config))
        if hooks and hooks.record and translate_input is not None and translated_text:
            try:
                hooks.record(translate_input.unicode, translated_text, getattr(_local, "page", None))
            except Exception:
                log.exception("could not record a translated paragraph")
        return orig_post(self, paragraph, tracker, translate_input, translated_text)

    def translate_paragraph(self, *args, **kwargs):
        bound = batch_sig.bind(self, *args, **kwargs)
        batch = bound.arguments["batch_paragraph"]
        pages = [p.page_number for p in batch.pages if p.page_number is not None]
        _local.page = pages[0] if pages else None
        hooks = _HOOKS.get(id(self.translation_config))
        if not (hooks and hooks.replay):
            return orig_batch(self, *args, **kwargs)
        pbar = bound.arguments.get("pbar")
        fonts, xfonts = bound.arguments.get("page_font_map"), bound.arguments.get("xobj_font_map")
        for paragraph, tracker in zip(batch.paragraphs, batch.trackers):
            text, translate_input = self.il_translator.pre_translate_paragraph(paragraph, tracker, fonts, xfonts)
            stored = hooks.replay(text) if text is not None else None
            if stored:  # untranslated paragraphs keep the original text
                self.il_translator.post_translate_paragraph(paragraph, tracker, translate_input, stored)
            if pbar:
                pbar.advance(1)

    ILTranslator.post_translate_paragraph = post_translate_paragraph
    ILTranslatorLLMOnly.translate_paragraph = translate_paragraph
    _installed = True


async def translate_digital(
    path: Path,
    out_dir: Path,
    work_dir: Path,
    src: str,
    tgt: str,
    bible: dict | None,
    llm_base_url: str | None,
    on_progress: ProgressCb,
    should_stop: Callable[[], bool],
    hooks: ParagraphHooks | None = None,
) -> Path:
    from babeldoc.docvision.doclayout import DocLayoutModel
    from babeldoc.format.pdf import high_level
    from babeldoc.format.pdf.translation_config import TranslationConfig, WatermarkOutputMode
    from babeldoc.translator.translator import OpenAITranslator

    high_level.init()
    _install_hooks()
    replaying = bool(hooks and hooks.replay)
    # Replays never reach the model, which may not even be loaded.
    llm_base_url = llm_base_url or "http://127.0.0.1:9"
    await on_progress("preparing", 0.0)
    layout = await asyncio.to_thread(DocLayoutModel.load_onnx)
    translator = OpenAITranslator(
        lang_in=BABELDOC_LANG[src], lang_out=BABELDOC_LANG[tgt], model="local",
        base_url=f"{llm_base_url}/v1", api_key="local", ignore_cache=False,
        # Disable "thinking" on reasoning models, as in our own client.
        send_temperature=True,
    )
    translator.extra_body["chat_template_kwargs"] = {"enable_thinking": False}
    translator.options = {"temperature": 0.2}
    work_dir.mkdir(parents=True, exist_ok=True)
    config = TranslationConfig(
        translator=translator,
        input_file=str(path),
        lang_in=BABELDOC_LANG[src],
        lang_out=BABELDOC_LANG[tgt],
        doc_layout_model=layout,
        output_dir=str(out_dir),
        working_dir=str(work_dir),
        no_dual=True,
        watermark_output_mode=WatermarkOutputMode.NoWatermark,
        qps=1,
        pool_max_workers=1,  # one local model, one request at a time
        custom_system_prompt=_custom_prompt(src, tgt, bible),
        glossaries=_glossary(bible, tgt),
        auto_extract_glossary=bible is None and not replaying,
        use_rich_pbar=False,
        skip_scanned_detection=False,
    )
    result = None
    if hooks:
        _HOOKS[id(config)] = hooks
    try:
        result = await _run_babeldoc(high_level, config, on_progress, should_stop)
    finally:
        _HOOKS.pop(id(config), None)
    if result is None or not result.mono_pdf_path:
        raise RuntimeError("BabelDOC finished without producing a PDF")
    return Path(result.mono_pdf_path)


async def _run_babeldoc(high_level, config, on_progress: ProgressCb, should_stop: Callable[[], bool]):
    result = None
    async for event in high_level.async_translate(config):
        if should_stop():
            raise asyncio.CancelledError()
        etype = event.get("type")
        if etype in ("progress_update", "progress_end", "progress_start"):
            await on_progress(event.get("stage", ""), float(event.get("overall_progress", 0.0)) / 100,
                              event.get("stage_current"), event.get("stage_total"))
        elif etype == "error":
            raise RuntimeError(str(event.get("error")))
        elif etype == "finish":
            # BabelDOC's event stream does not end on its own after "finish".
            result = event["translate_result"]
            break
    return result


async def translate_scanned_pages(
    pdf_path: Path,
    pages: list[int],
    src: str,
    tgt: str,
    translate_texts: Callable[[list[str]], Awaitable[list[str]]],
    on_progress: ProgressCb,
    should_stop: Callable[[], bool],
) -> None:
    """OCR, translate and repaint the given pages of `pdf_path` in place."""
    import pymupdf

    cjk_src = src in ("ja", "zh")
    doc = pymupdf.open(pdf_path)
    try:
        for n, i in enumerate(pages):
            if should_stop():
                raise asyncio.CancelledError()
            await on_progress("ocr", n / max(1, len(pages)))
            page = doc[i]
            lines = await asyncio.to_thread(ocr.ocr_page, page, src)
            blocks = [b for b in ocr.group_blocks(lines) if len(b.text(cjk_src)) > 1]
            if not ocr.is_prose_page(blocks, cjk_src):
                log.info("page %d: artwork or title page, left untouched", i)
                continue
            translated = await translate_texts([b.text(cjk_src) for b in blocks])
            for b, t in zip(blocks, translated):
                ocr.repaint(page, b, t, tgt)
        tmp = pdf_path.with_suffix(".ocr.pdf")
        doc.save(tmp, garbage=3, deflate=True)
    finally:
        doc.close()
    shutil.move(tmp, pdf_path)
    await on_progress("ocr", 1.0)
