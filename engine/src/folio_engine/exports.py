"""Rebuilding a book from stored translations, without the model.

Used after editing paragraphs (the output is regenerated in place) and to read
a book before it is finished (a separate "partial" file where untranslated
passages keep the original text).
"""

import asyncio
import logging
import shutil
import time
from pathlib import Path

from . import langs
from .errors import UserError
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
                    error: str | None = None, code: str | None = None) -> None:
        await self.publish({"type": "export", "job_id": job_id, "state": state, "progress": round(progress, 4),
                            "path": str(path), "error": error, "error_code": code, "at": time.time()})

    async def _run(self, job: dict, out: Path, partial: bool) -> None:
        job_id = job["id"]
        await self._emit(job_id, "running", 0.0, out)
        try:
            translations = self.store.translations(job_id)
            out.parent.mkdir(parents=True, exist_ok=True)
            if job["format"] == "epub":
                await asyncio.to_thread(_write_epub, job, translations, out)
            else:
                await self._write_pdf(job, translations, out)
            if not partial:
                stats = {**(self.store.get_job(job_id) or job)["stats"], "exported_at": time.time(), "dirty": False}
                self.store.update_job(job_id, output_path=str(out), stats=stats)
                if self.on_job_changed:
                    await self.on_job_changed(job_id)
            await self._emit(job_id, "done", 1.0, out)
        except PermissionError as e:  # Windows: the previous file is open in a reader
            await self._emit(job_id, "failed", 0.0, out, str(e), "export_locked")
        except UserError as e:
            await self._emit(job_id, "failed", 0.0, out, str(e), e.code)
        except Exception as e:
            log.exception("export of %s failed", job_id)
            await self._emit(job_id, "failed", 0.0, out, str(e) or type(e).__name__, "internal")

    async def _write_pdf(self, job: dict, translations: dict[str, str], out: Path) -> None:
        from .formats import pdf

        by_source = {row["source"]: translations[row["seg_id"]]
                     for row in self.store.segments(job["id"])
                     if row["seg_id"].startswith("pdf:") and row["seg_id"] in translations}
        if not by_source:
            raise UserError("export_no_paragraphs", "no paragraphs recorded for this PDF", 409)
        work = self.data_dir / "export" / job["id"]
        shutil.rmtree(work, ignore_errors=True)

        async def on_progress(stage: str, frac: float, *_):
            await self._emit(job["id"], "running", min(frac, 0.99), out)

        produced = await pdf.translate_digital(
            Path(job["input_path"]), work / "out", work / "tmp", job["src"], job["tgt"], job["bible"], None,
            on_progress, lambda: False, pdf.ParagraphHooks(replay=by_source.get),
            job["options"].get("book_type", "literary"))
        previous = job.get("output_path")
        scanned = (await asyncio.to_thread(pdf.read_info, Path(job["input_path"])))["scanned_pages"]
        if scanned and previous and Path(previous).exists():
            await asyncio.to_thread(_copy_pages, Path(previous), produced, scanned)
        tmp = out.with_suffix(".tmp.pdf")
        shutil.move(produced, tmp)
        tmp.replace(out)
        shutil.rmtree(work, ignore_errors=True)


def _write_epub(job: dict, translations: dict[str, str], out: Path) -> None:
    from .formats.epub import EpubBook

    book = EpubBook(Path(job["input_path"]), technical=job["options"].get("book_type") == "technical")
    book.extract()
    book.apply(translations, job["tgt"])
    tmp = out.with_suffix(".tmp.epub")
    book.write(tmp)
    tmp.replace(out)


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
