"""Queue for EPUB ⇄ PDF conversions, with progress, ETA and cancellation."""

import asyncio
import json
import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .errors import UserError
from .eta import ProgressRateEta
from .formats import convert

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversions (
    id TEXT PRIMARY KEY, input_path TEXT, output_path TEXT, target TEXT, options TEXT,
    status TEXT, progress REAL, error TEXT, error_code TEXT, title TEXT, created_at REAL, updated_at REAL
)"""
FIELDS = ("id", "input_path", "output_path", "target", "options", "status", "progress", "error", "error_code",
          "title", "created_at", "updated_at")


class Conversions:
    def __init__(self, data_dir: Path, publish) -> None:
        self._db = sqlite3.connect(data_dir / "folio.db", check_same_thread=False, isolation_level=None)
        self._db.execute(SCHEMA)
        self._db.execute("UPDATE conversions SET status='failed', error_code='interrupted' "
                         "WHERE status IN ('queued','running')")
        self._lock = threading.Lock()
        self._publish = publish
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._cancel: set[str] = set()
        self.current: str | None = None

    def start(self) -> None:
        asyncio.create_task(self._loop())

    # ---- persistence ----
    def _get(self, cid: str) -> dict | None:
        r = self._db.execute(f"SELECT {','.join(FIELDS)} FROM conversions WHERE id=?", (cid,)).fetchone()
        if not r:
            return None
        d = dict(zip(FIELDS, r))
        d["options"] = json.loads(d["options"] or "{}")
        return d

    def list(self) -> list[dict]:
        rows = self._db.execute(f"SELECT id FROM conversions ORDER BY created_at DESC LIMIT 100").fetchall()
        return [self._get(r[0]) for r in rows]

    def _update(self, cid: str, **f) -> None:
        f["updated_at"] = time.time()
        with self._lock:
            self._db.execute(f"UPDATE conversions SET {','.join(k + '=?' for k in f)} WHERE id=?",
                             [*f.values(), cid])

    def delete(self, cid: str) -> None:
        if self.current == cid:
            raise UserError("job_running", "conversion is running", 409)
        with self._lock:
            self._db.execute("DELETE FROM conversions WHERE id=?", (cid,))

    # ---- api ----
    async def create(self, path: Path, target: str, options: dict, out_dir: Path | None, title: str) -> dict:
        src = path.suffix.lower()[1:]
        if src not in ("pdf", "epub") or target not in ("pdf", "epub") or src == target:
            raise UserError("unsupported_format", "convert between PDF and EPUB only")
        if not path.is_file():
            raise UserError("file_not_found", "file not found", 404)
        now = time.time()
        cid = uuid.uuid4().hex[:12]
        out = convert.output_name(path, target, out_dir or path.parent)
        with self._lock:
            self._db.execute(f"INSERT INTO conversions ({','.join(FIELDS)}) VALUES ({','.join('?' * len(FIELDS))})",
                             (cid, str(path), str(out), target, json.dumps(options), "queued", 0.0, None, None,
                              title, now, now))
        await self._queue.put(cid)
        await self._emit(cid)
        return self._get(cid)

    def cancel(self, cid: str) -> None:
        self._cancel.add(cid)
        job = self._get(cid)
        if job and job["status"] == "queued":
            self._update(cid, status="cancelled")

    async def _emit(self, cid: str, eta: float | None = None) -> None:
        job = self._get(cid)
        if job:
            await self._publish({"type": "conversion", "conversion": {**job, "eta_seconds": eta}})

    # ---- worker ----
    async def _loop(self) -> None:
        while True:
            cid = await self._queue.get()
            job = self._get(cid)
            if not job or job["status"] != "queued":
                continue
            self.current = cid
            try:
                await self._run(job)
            except convert.Cancelled:
                self._update(cid, status="cancelled")
                Path(job["output_path"]).unlink(missing_ok=True)
            except Exception as e:
                log.exception("conversion %s failed", cid)
                code = e.code if isinstance(e, UserError) else "convert_failed"
                self._update(cid, status="failed", error=str(e), error_code=code)
            finally:
                self.current = None
                self._cancel.discard(cid)
                await self._emit(cid)

    async def _run(self, job: dict) -> None:
        cid = job["id"]
        self._update(cid, status="running", progress=0.0)
        await self._emit(cid)
        loop = asyncio.get_running_loop()
        rate = ProgressRateEta()
        last = [0.0]

        def progress(frac: float, _detail: str) -> None:
            now = time.monotonic()
            if now - last[0] < 0.3 and frac < 1:
                return
            last[0] = now
            rate.update(frac)
            self._update(cid, progress=frac)
            loop.call_soon_threadsafe(asyncio.ensure_future, self._emit(cid, rate.seconds_left()))

        stop = lambda: cid in self._cancel  # noqa: E731
        src, out, opts = Path(job["input_path"]), Path(job["output_path"]), job["options"]
        if job["target"] == "pdf":
            fn = lambda: convert.epub_to_pdf(src, out, page=opts.get("page", "a5"),  # noqa: E731
                                             font_size=float(opts.get("font_size", 11)),
                                             margin_mm=float(opts.get("margin_mm", 16)),
                                             on_progress=progress, should_stop=stop)
        elif opts.get("mode") == "fixed":
            fn = lambda: convert.pdf_to_epub_fixed(src, out, on_progress=progress, should_stop=stop)  # noqa: E731
        else:
            fn = lambda: convert.pdf_to_epub_reflow(src, out, lang=opts.get("lang"),  # noqa: E731
                                                    on_progress=progress, should_stop=stop)
        await asyncio.to_thread(fn)
        self._update(cid, status="done", progress=1.0)
