"""Rebuilding a book from stored translations, without the model.

Used after editing paragraphs (the output is regenerated in place) and to read
a book before it is finished (a separate "partial" file where untranslated
passages keep the original text).
"""

import asyncio
import logging
import shutil
import tempfile
import time
from pathlib import Path

from . import langs
from .errors import UserError
from .jobs import finish_file_operation, require_source
from .store import Store

log = logging.getLogger(__name__)


def output_path(job: dict, partial_label: str | None) -> Path:
    """The regular output, or a sibling named with `partial_label` (in the UI's language)."""
    src = Path(job["input_path"])
    out_dir = Path(job["options"].get("output_dir") or src.parent)
    name = langs.LANGUAGES[job["tgt"]].native
    suffix = f" — {partial_label}" if partial_label else ""
    return out_dir / f"{src.stem} ({name}){suffix}{src.suffix}"


class Exporter:
    def __init__(self, store: Store, publish, data_dir: Path, on_job_changed=None) -> None:
        self.store = store
        self.publish = publish
        self.on_job_changed = on_job_changed
        self.data_dir = data_dir
        self.tasks: dict[str, asyncio.Task] = {}

    def busy(self, job_id: str) -> bool:
        task = self.tasks.get(job_id)
        return bool(task and not task.done())

    async def start(self, job: dict, partial_label: str | None) -> Path:
        if self.busy(job["id"]):
            raise UserError("export_running", "this book is already being exported", 409)
        out = output_path(job, partial_label)
        self.tasks[job["id"]] = asyncio.create_task(self._run(job, out, bool(partial_label)))
        return out

    async def _emit(self, job_id: str, state: str, progress: float, path: Path,
                    error: str | None = None, code: str | None = None, issues: list[dict] | None = None) -> None:
        await self.publish({"type": "export", "job_id": job_id, "state": state, "progress": round(progress, 4),
                            "path": str(path), "error": error, "error_code": code, "at": time.time(),
                            "layout_issues": issues or []})

    async def _run(self, job: dict, out: Path, partial: bool) -> None:
        job_id = job["id"]
        await self._emit(job_id, "running", 0.0, out)
        try:
            require_source(job)
            revision = (self.store.get_job(job_id) or job)["stats"].get("edit_revision", 0)
            translations = self.store.translations(job_id)
            out.parent.mkdir(parents=True, exist_ok=True)
            if job["format"] == "epub":
                issues = await finish_file_operation(_write_epub, job, translations, out)
            else:
                issues = await self._write_pdf(job, translations, out)
            if not partial:
                stats = (self.store.get_job(job_id) or job)["stats"]
                stats = {**stats, "exported_at": time.time(), "dirty": stats.get("edit_revision", 0) != revision,
                         "layout_issues": issues}
                self.store.update_job(job_id, output_path=str(out), stats=stats)
                if self.on_job_changed:
                    await self.on_job_changed(job_id)
            await self._emit(job_id, "done", 1.0, out, issues=issues)
        except PermissionError as e:  # Windows: the previous file is open in a reader
            await self._emit(job_id, "failed", 0.0, out, str(e), "export_locked")
        except UserError as e:
            await self._emit(job_id, "failed", 0.0, out, str(e), e.code)
        except Exception as e:
            log.exception("export of %s failed", job_id)
            await self._emit(job_id, "failed", 0.0, out, str(e) or type(e).__name__, "internal")

    async def _write_pdf(self, job: dict, translations: dict[str, str], out: Path) -> list[dict]:
        from .formats import layout_check, pdf

        rows = self.store.segments(job["id"])
        by_source = {row["source"]: translations[row["seg_id"]] for row in rows
                     if row["seg_id"].startswith("pdf:") and row["seg_id"] in translations}
        ocr_pages = {row["chapter"] for row in rows
                     if row["seg_id"].startswith("ocr:") and row["seg_id"] in translations}
        if not (by_source or ocr_pages):
            raise UserError("export_no_paragraphs", "no paragraphs recorded for this PDF", 409)
        work = self.data_dir / "export" / job["id"]
        path = Path(job["input_path"])
        info = await asyncio.to_thread(pdf.read_info, path)

        async def on_progress(stage: str, frac: float, *_):
            await self._emit(job["id"], "running", min(frac, 0.99), out)

        async def replay_scan(page: int, texts: list[str]) -> list[str | None]:
            return [translations.get(pdf.ocr_paragraph_id(page, i, text)) for i, text in enumerate(texts)]

        try:
            overflow: set[int] = set()
            with tempfile.TemporaryDirectory(prefix=".aibook-", dir=out.parent) as staging:
                target = Path(staging) / out.name
                if by_source and info["digital_pages"]:
                    produced = await pdf.translate_digital(
                        path, work / "out", work / "tmp", job["src"], job["tgt"], job["bible"], None,
                        on_progress, lambda: False, pdf.ParagraphHooks(replay=by_source.get),
                        job["options"].get("book_type", "literary"), pages=info["digital_pages"])
                    shutil.move(produced, target)
                else:
                    shutil.copyfile(path, target)
                previous = job.get("output_path")
                legacy = [i for i in info["scanned_pages"] if i not in ocr_pages]
                if legacy and previous and Path(previous).is_file():
                    await asyncio.to_thread(_copy_pages, Path(previous), target, legacy)
                if ocr_pages:
                    async def recognize(page: int):
                        return await pdf.recognize_page(path, page, job["src"], self.store, job["id"])

                    await pdf.translate_scanned_pages(
                        target, sorted(ocr_pages), job["src"], job["tgt"], replay_scan, on_progress,
                        lambda: False, recognize, overflow.add)
                issues = await finish_file_operation(layout_check.pdf, path, target, info["digital_pages"], lambda: False)
                require_source(job)
                target.replace(out)
                return issues + [{"code": "ocr_fit", "page": page} for page in sorted(overflow)]
        finally:
            shutil.rmtree(work, ignore_errors=True)


def _write_epub(job: dict, translations: dict[str, str], out: Path) -> list[dict]:
    from .formats import layout_check
    from .formats.epub import EpubBook

    source = Path(job["input_path"])
    book = EpubBook(source, technical=job["options"].get("book_type") == "technical")
    with book.zip, tempfile.TemporaryDirectory(prefix=".aibook-", dir=out.parent) as staging:
        book.extract()
        protected = book.apply(translations, job["tgt"])
        target = Path(staging) / out.name
        book.write(target)
        layout_check.epub(source, target, {*book.docs, book.opf_path})
        require_source(job)
        target.replace(out)
    return [{"code": "protected_epub", "count": len(protected)}] if protected else []


def _copy_pages(source: Path, target: Path, pages: list[int]) -> None:
    """Scanned pages are translated by OCR, not BabelDOC: carry them over from the last output."""
    import pymupdf

    with pymupdf.open(source) as src, pymupdf.open(target) as dst:
        for i in pages:
            if i < dst.page_count and i < src.page_count:
                dst.delete_page(i)
                dst.insert_pdf(src, from_page=i, to_page=i, start_at=i)
        tmp = target.with_suffix(".pages.pdf")
        dst.save(tmp, garbage=3, deflate=True)
    tmp.replace(target)
