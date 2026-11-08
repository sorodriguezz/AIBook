"""Job queue: runs one translation at a time and broadcasts progress."""

import asyncio
import base64
import logging
import shutil
import tempfile
import time
import traceback
from pathlib import Path

import httpx

from . import langs
from .errors import UserError
from .eta import EtaTracker, ProgressRateEta
from .llm.client import LLMUnavailable
from .llm.server_manager import LlamaServerManager, ServerConfig, memory_failure
from .store import Store
from .translate.pipeline import Pipeline, Settings, Stopped, seeded_entries
from .translate.segments import Segment
from .translate import langdetect, validators

log = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    async def publish(self, event: dict) -> None:
        for q in list(self._subs):
            if q.full():
                q.get_nowait()  # drop the oldest; progress events are idempotent
            q.put_nowait(event)


def detect_format(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".epub":
        return "epub"
    if ext == ".pdf":
        return "pdf"
    raise UserError("unsupported_format", "Only PDF and EPUB files are supported")


def inspect(path: Path) -> dict:
    fmt = detect_format(path)
    if fmt == "epub":
        from .formats import epub

        info = epub.read_info(path)
        book = epub.EpubBook(path)
        cover = book.cover_bytes()
        if cover:
            info["cover"] = f"data:{cover[1]};base64,{base64.b64encode(cover[0]).decode()}"
    else:
        from .formats import pdf

        info = pdf.read_info(path)
        info["cover"] = _pdf_cover(path)
    detected = langdetect.detect(info.get("sample", ""))
    info["detected_language"] = info.get("language") if info.get("language") in langs.LANGUAGES else detected
    info["format"] = fmt
    info.pop("sample", None)
    return info


def _pdf_cover(path: Path) -> str | None:
    try:
        import pymupdf

        with pymupdf.open(path) as doc:
            pix = doc[0].get_pixmap(dpi=40)
            return "data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode()
    except Exception:
        return None


def output_path_for(job: dict) -> Path:
    src = Path(job["input_path"])
    out_dir = Path(job["options"].get("output_dir") or src.parent)
    name = langs.LANGUAGES[job["tgt"]].native
    return out_dir / f"{src.stem} ({name}){src.suffix}"


def require_source(job: dict) -> str:
    path = Path(job["input_path"])
    if not path.is_file():
        raise UserError("file_not_found", "source file not found", 404)
    st = path.stat()
    stamp = f"{st.st_size}:{st.st_mtime_ns}"
    if (previous := job["stats"].get("source_stamp")) and previous != stamp:
        raise UserError("input_changed", "source file changed after translation started", 409)
    return stamp


async def finish_file_operation(fn, *args):
    """Keep staged files open until a worker has stopped using them on pause."""
    task = asyncio.create_task(asyncio.to_thread(fn, *args))
    try:
        return await asyncio.shield(task)
    finally:
        await asyncio.gather(task, return_exceptions=True)


class JobRunner:
    def __init__(self, store: Store, llm: LlamaServerManager, bus: EventBus, data_dir: Path) -> None:
        self.store = store
        self.llm = llm
        self.bus = bus
        self.data_dir = data_dir
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.flags: dict[str, str] = {}  # job_id -> "pause" | "cancel"
        self.current: str | None = None
        self._task: asyncio.Task | None = None  # the running job
        self._worker: asyncio.Task | None = None

    def start(self) -> None:
        self._worker = asyncio.create_task(self._loop())

    async def enqueue(self, job_id: str) -> None:
        self.flags.pop(job_id, None)
        stats = (self.store.get_job(job_id) or {}).get("stats", {})
        stats.pop("error_code", None)
        stats.pop("error_params", None)
        self.store.update_job(job_id, status="queued", error=None, stats=stats)
        await self.queue.put(job_id)
        await self.publish_job(job_id)

    def pause(self, job_id: str) -> None:
        self.flags[job_id] = "pause"
        self._interrupt(job_id)

    def cancel(self, job_id: str) -> None:
        self.flags[job_id] = "cancel"
        self._interrupt(job_id)
        job = self.store.get_job(job_id)
        if job and job["status"] in ("queued", "paused", "awaiting_glossary"):
            self.store.update_job(job_id, status="cancelled")

    def _interrupt(self, job_id: str) -> None:
        """Stop right away instead of after the current model request.

        A single request can take minutes on slow hardware. Cancelling the task
        closes the HTTP connection, which makes llama-server abandon the
        generation; finished segments are already checkpointed.
        """
        if self.current == job_id and self._task and not self._task.done():
            self._task.cancel()

    async def publish_job(self, job_id: str) -> None:
        job = self.store.get_job(job_id)
        if job:
            job.pop("bible", None)
            await self.bus.publish({"type": "job", "job": job})

    async def _loop(self) -> None:
        while True:
            job_id = await self.queue.get()
            job = self.store.get_job(job_id)
            if not job or job["status"] != "queued":
                continue
            self.current = job_id
            self._task = asyncio.create_task(self._run(job))
            try:
                await self._task
            except _AwaitGlossary:
                pass  # status already set; the user resumes after editing the glossary
            except Stopped:
                state = "cancelled" if self.flags.get(job_id) == "cancel" else "paused"
                self.store.update_job(job_id, status=state)
            except asyncio.CancelledError:
                state = "cancelled" if self.flags.get(job_id) == "cancel" else "paused"
                self.store.update_job(job_id, status=state)
            except Exception as e:
                log.error("job %s failed: %s", job_id, traceback.format_exc())
                err = self._describe_failure(e)
                stats = {**(self.store.get_job(job_id) or {}).get("stats", {}),
                         "error_code": err.code, "error_params": err.params}
                self.store.update_job(job_id, status="failed", error=str(err), stats=stats)
            finally:
                self.current = None
                self._task = None
                self.flags.pop(job_id, None)
                await self.publish_job(job_id)

    def _describe_failure(self, e: Exception) -> UserError:
        """Turn a failure into something the user can act on.

        When llama-server dies mid-request (usually out of memory) the job only
        sees a dropped connection, often with an empty message.
        """
        if isinstance(e, UserError):
            return e
        if self.llm.config is not None and not self.llm.running:
            tail = "\n".join(self.llm.log_tail[-15:])
            log.error("llama-server output before it stopped:\n%s", "\n".join(self.llm.log_tail[-60:]))
            code = "model_memory" if memory_failure(tail) else "model_crashed"
            return UserError(code, f"llama-server stopped unexpectedly:\n{tail}", 500)
        if isinstance(e, httpx.TimeoutException):
            return UserError("model_request_timeout", f"the model took too long to answer ({type(e).__name__})", 504)
        if isinstance(e, LLMUnavailable):
            return UserError("model_unavailable", str(e), 503)
        if isinstance(e, httpx.TransportError):
            return UserError("model_crashed", f"lost the connection to llama-server ({type(e).__name__}: {e})", 500)
        return UserError("internal", str(e) or type(e).__name__, 500, detail=(str(e) or type(e).__name__)[:200])

    def _should_stop(self, job_id: str):
        return lambda: job_id in self.flags

    async def _run(self, job: dict) -> None:
        job_id = job["id"]
        opts = job["options"]
        stamp = require_source(job)
        job["stats"] = {**job["stats"], "source_stamp": stamp}
        self.store.update_job(job_id, status="running", phase="preparing", stats=job["stats"])
        await self.publish_job(job_id)
        eta = EtaTracker()
        pdf_info = None
        if opts.get("low_memory") and job["format"] == "pdf":
            from .formats import pdf

            path = Path(job["input_path"])
            await self.llm.stop()
            pdf_info = await asyncio.to_thread(pdf.read_info, path)
            scanned = pdf_info["scanned_pages"]
            if scanned:
                try:
                    for n, page in enumerate(scanned, 1):
                        await self._phase(job, "ocr", eta, detail=f"{n}/{len(scanned)}", kind="pages")
                        await pdf.recognize_page(path, page, job["src"], self.store, job_id)
                finally:
                    pdf.ocr.release_models()
        await self._phase(job, "loading_model", eta)
        config = ServerConfig.from_options(opts)
        client = await self.llm.ensure(config)
        bench = opts.get("benchmark") or {}
        eta.seed(bench.get("prompt_tps"), bench.get("gen_tps"))
        settings = Settings(job["src"], job["tgt"], job["quality"], opts.get("honorifics", "adapt"),
                            opts.get("book_type", "literary"), opts.get("review_scope", "full"),
                            config.ctx_size if config.ctx_size < 8192 else None)

        async def emit(ev: dict) -> None:
            await self.bus.publish(ev)

        pipe = Pipeline(client, self.store, job_id, settings, eta, emit, self._should_stop(job_id))
        pipe.bible = job["bible"]
        pipe.seed = opts.get("glossary_seed")
        out = output_path_for(job)

        if job["format"] == "epub":
            warnings = await self._run_epub(job, pipe, out)
        else:
            warnings = await self._run_pdf(job, pipe, out, pdf_info)

        stats = (self.store.get_job(job_id) or job)["stats"]
        self.store.update_job(job_id, status="done", phase="done", progress=1.0, output_path=str(out),
                              stats={**stats, "elapsed_seconds": round(eta.elapsed()), "layout_issues": warnings})
        await self.bus.publish({"type": "progress", "job_id": job_id, "phase": "done", "detail": "",
                                "progress": 1.0, "eta_seconds": 0, "elapsed_seconds": round(eta.elapsed())})

    async def _phase(self, job: dict, phase: str, eta: EtaTracker, detail: str = "", kind: str = "") -> None:
        progress = (self.store.get_job(job["id"]) or job)["progress"]
        self.store.update_job(job["id"], phase=phase)
        await self.bus.publish({"type": "progress", "job_id": job["id"], "phase": phase, "detail": detail,
                                "detail_kind": kind, "progress": progress, "eta_seconds": None,
                                "elapsed_seconds": round(eta.elapsed())})

    async def _maybe_bible(self, job: dict, pipe: Pipeline, segs: list[Segment]) -> None:
        """Build the book bible if needed; raises _AwaitGlossary to let the user edit it."""
        if pipe.bible is not None:
            return
        if job["quality"] == "fast":
            if pipe.seed:  # no analysis in this mode, but an imported glossary still applies
                pipe.bible = {**seeded_entries(pipe.seed), "style": pipe.seed.get("style") or {},
                              "honorifics": job["options"].get("honorifics", "adapt")}
                self.store.update_job(job["id"], bible=pipe.bible)
            return
        bible = await pipe.build_bible(segs)
        self.store.update_job(job["id"], bible=bible)
        await self.bus.publish({"type": "bible", "job_id": job["id"]})
        if job["options"].get("review_glossary"):
            self.store.update_job(job["id"], status="awaiting_glossary", phase="awaiting_glossary")
            raise _AwaitGlossary()

    async def _run_epub(self, job: dict, pipe: Pipeline, out: Path) -> list[dict]:
        from .formats import layout_check
        from .formats.epub import EpubBook

        source = Path(job["input_path"])
        book = EpubBook(source, technical=job["options"].get("book_type") == "technical")
        with book.zip:
            segs = book.extract()
            if not self.store.has_segments(job["id"]):
                self.store.add_segments(job["id"], segs)
            done = {r["seg_id"]: r for r in self.store.segments(job["id"])}
            pipe.plan(segs, done)
            await self._maybe_bible(job, pipe, segs)
            await pipe.translate_all(segs, done)
            await self._phase(job, "writing", pipe.eta)
            protected = book.apply(self.store.translations(job["id"]), job["tgt"])
            out.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".aibook-", dir=out.parent) as staging:
                target = Path(staging) / out.name
                await finish_file_operation(book.write, target)
                await self._phase(job, "checking_layout", pipe.eta)
                await finish_file_operation(layout_check.epub, source, target, {*book.docs, book.opf_path})
                require_source(job)
                target.replace(out)
        return [{"code": "protected_epub", "count": len(protected)}] if protected else []

    async def _run_pdf(self, job: dict, pipe: Pipeline, out: Path, info: dict | None = None) -> list[dict]:
        from .formats import layout_check, pdf

        path = Path(job["input_path"])
        info = info or await asyncio.to_thread(pdf.read_info, path)
        scanned, digital = info["scanned_pages"], info["digital_pages"]
        if not (scanned or digital):
            raise UserError("pdf_no_text", "no readable text in this PDF")
        stop = self._should_stop(job["id"])

        async def recognize(i: int):
            return await pdf.recognize_page(path, i, job["src"], self.store, job["id"])

        if pipe.bible is None and job["quality"] == "fast":
            await self._maybe_bible(job, pipe, [])
        if job["quality"] != "fast" and pipe.bible is None:
            scans = {}
            for n, page in enumerate(scanned, 1):
                if stop():
                    raise Stopped()
                pipe.phase, pipe.detail = "ocr", f"{n}/{len(scanned)}"
                if not job["options"].get("low_memory"):
                    await pipe._report()
                scans[page] = await recognize(page)
            pipe.phase, pipe.detail = "analyzing", ""
            await pipe._report()
            segs = await asyncio.to_thread(pdf.bible_segments, path, job["src"], scans)
            if not segs and not digital:
                raise UserError("pdf_no_text", "no readable text found by OCR")
            pipe.plan(segs, {})
            await self._maybe_bible(job, pipe, segs)

        rate = ProgressRateEta()
        digital_share = len(digital) / (len(digital) + len(scanned))

        translated = False

        async def on_progress(stage: str, frac: float, current: int | None = None, total_n: int | None = None,
                              base: float = 0.0, span: float = 1.0) -> None:
            # BabelDOC's stage names are internal English labels; map them to our phases.
            nonlocal translated
            total = base + span * frac
            rate.update(total)
            left = rate.seconds_left()
            detail, kind = "", ""
            if stage == "ocr":
                phase = "ocr"
                if current is not None and total_n:
                    detail, kind = f"{current}/{total_n}", "pages"
            elif stage == "checking_layout":
                phase = stage
            elif stage == "translate_scan":
                phase, kind = "translating", "pages"
                if current is not None and total_n:
                    detail = f"{current}/{total_n}"
            elif stage == pdf.TRANSLATE_STAGE:
                phase, translated = "translating", True
                if current is not None and total_n:
                    detail, kind = f"{current}/{total_n}", "paragraphs"
            else:
                phase = "writing" if translated else "preparing"
            event = {"type": "progress", "job_id": job["id"], "phase": phase,
                     "detail": detail, "detail_kind": kind, "progress": round(total, 4),
                     "eta_seconds": round(left) if left is not None else None,
                     "elapsed_seconds": round(time.monotonic() - rate.started_at),
                     "gen_tps": await self.llm.generation_rate()}
            if event["gen_tps"]:
                event["gen_tps"] = round(event["gen_tps"], 1)
            self.store.update_job(job["id"], phase=phase, progress=event["progress"])
            await self.bus.publish(event)

        work = self.data_dir / "work" / job["id"]
        out.parent.mkdir(parents=True, exist_ok=True)

        def record(source: str, translation: str, page: int | None) -> None:
            self.store.record_paragraph(job["id"], pdf.paragraph_id(source), page, source, translation)

        async def translate_scan(page: int, texts: list[str]) -> list[str | None]:
            ids = [pdf.ocr_paragraph_id(page, i, text) for i, text in enumerate(texts)]
            missing = [i for i, key in enumerate(ids) if key not in saved]
            if missing:
                translations = await pipe.translate_texts([texts[i] for i in missing])
                for i, translation in zip(missing, translations):
                    issues = validators.validate(texts[i], translation, job["src"], job["tgt"])
                    self.store.record_paragraph(job["id"], ids[i], page, texts[i], translation, flags=issues)
                    saved[ids[i]] = translation
            return [saved.get(key) for key in ids]

        saved = self.store.translations(job["id"])
        ocr_overflow: set[int] = set()
        try:
            with tempfile.TemporaryDirectory(prefix=".aibook-", dir=out.parent) as staging:
                target = Path(staging) / out.name
                if digital:
                    produced = await pdf.translate_digital(
                        path, work / "out", work / "tmp", job["src"], job["tgt"], pipe.bible, self.llm.base_url,
                        lambda s, f, c=None, n=None: on_progress(s, f, c, n, 0.0, digital_share), stop,
                        pdf.ParagraphHooks(record=record), job["options"].get("book_type", "literary"), pages=digital)
                    if not self.llm.running:
                        raise RuntimeError("model stopped while translating the PDF")
                    shutil.move(produced, target)
                else:
                    shutil.copyfile(path, target)
                if scanned:
                    changed = await pdf.translate_scanned_pages(
                        target, scanned, job["src"], job["tgt"], translate_scan,
                        lambda s, f, c=None, n=None: on_progress(s, f, c, n, digital_share, 1 - digital_share),
                        stop, recognize, ocr_overflow.add)
                    if not changed and not digital and not ocr_overflow:
                        raise UserError("pdf_no_text", "no readable text found by OCR")
                if job["options"].get("low_memory"):
                    await self.llm.stop()
                await on_progress("checking_layout", 0.99)
                issues = await finish_file_operation(layout_check.pdf, path, target, digital, stop)
                if stop():
                    raise Stopped()
                require_source(job)
                target.replace(out)
                return issues + [{"code": "ocr_fit", "page": page} for page in sorted(ocr_overflow)]
        finally:
            shutil.rmtree(work, ignore_errors=True)


class _AwaitGlossary(Exception):
    pass
