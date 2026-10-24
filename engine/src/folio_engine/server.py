"""HTTP + WebSocket API consumed by the Tauri app."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from . import benchmark, editing, glossary, jobs, langs
from .exports import Exporter
from .conversions import Conversions
from .library import Library, import_books
from .errors import UserError
from .llm.server_manager import LlamaServerManager, ServerConfig
from .store import Store

log = logging.getLogger(__name__)

TOKEN = os.environ.get("FOLIO_TOKEN", "")
# Free the model's memory after this long without use (and no queued work).
IDLE_UNLOAD_SECONDS = float(os.environ.get("FOLIO_IDLE_UNLOAD", "300"))
DATA_DIR = Path(os.environ.get("FOLIO_DATA_DIR", Path.home() / ".folio"))


class State:
    store: Store
    llm: LlamaServerManager
    bus: jobs.EventBus
    runner: jobs.JobRunner
    library: Library
    conversions: Conversions
    exporter: Exporter


state = State()


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.store = Store(DATA_DIR / "folio.db")
    state.llm = LlamaServerManager()
    state.bus = jobs.EventBus()
    state.runner = jobs.JobRunner(state.store, state.llm, state.bus, DATA_DIR)
    state.runner.start()
    state.library = Library(DATA_DIR)
    state.conversions = Conversions(DATA_DIR, state.bus.publish)
    state.conversions.start()
    state.exporter = Exporter(state.store, state.bus.publish, DATA_DIR, state.runner.publish_job)
    watchdog = asyncio.create_task(_exit_with_parent())
    unloader = asyncio.create_task(_unload_when_idle())
    yield
    watchdog.cancel()
    unloader.cancel()
    await state.llm.stop()


def _llm_busy() -> bool:
    # BabelDOC talks to llama-server directly, so a running job counts as busy
    # even between our own tracked requests.
    return bool(state.runner.current) or not state.runner.queue.empty() or state.llm.active_requests > 0


async def _unload_when_idle() -> None:
    while True:
        await asyncio.sleep(15)
        if state.llm.running and not _llm_busy() and state.llm.idle_seconds() >= IDLE_UNLOAD_SECONDS:
            log.info("model idle for %.0fs; freeing its memory", state.llm.idle_seconds())
            await state.llm.stop()
            await state.bus.publish({"type": "llm", **_llm_status()})


def _llm_status() -> dict:
    llm = state.llm
    cfg = llm.config
    idle = llm.idle_seconds() if llm.running else 0
    return {
        "running": llm.running,
        "model_path": cfg.model_path if cfg and llm.running else None,
        "ctx_size": cfg.ctx_size if cfg and llm.running else None,
        "busy": _llm_busy(),
        "idle_seconds": round(idle),
        "unload_in": None if not llm.running or _llm_busy() else max(0, round(IDLE_UNLOAD_SECONDS - idle)),
    }


async def _exit_with_parent() -> None:
    """Exit (stopping llama-server) if the app that launched us disappears.

    Covers crashes and force-quits, where the app never gets to ask us to stop.
    """
    parent = int(os.environ.get("FOLIO_PARENT_PID", "0"))
    if not parent:
        return
    while True:
        await asyncio.sleep(2)
        if not _pid_alive(parent):
            log.warning("parent process %d is gone; shutting down", parent)
            await state.llm.stop()
            os._exit(0)


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        SYNCHRONIZE, WAIT_TIMEOUT = 0x00100000, 0x102
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


app = FastAPI(title="Folio engine", lifespan=lifespan)
# The Tauri webview calls the engine directly; the token is the real guard.
# WebView2 (Windows) sends a Private Network Access preflight before reaching
# 127.0.0.1; without allow_private_network it gets a 400 and fetch fails.
app.add_middleware(
    CORSMiddleware,
    allow_private_network=True,
    allow_origins=["tauri://localhost", "http://tauri.localhost", "https://tauri.localhost",
                   "http://localhost:1420"],
    allow_methods=["*"],
    allow_headers=["*"],
)



@app.exception_handler(UserError)
async def user_error(_, exc: UserError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"detail": exc.to_dict()})


def auth(x_folio_token: str = Header(default="")) -> None:
    if TOKEN and x_folio_token != TOKEN:
        raise HTTPException(401, "bad token")


class ModelSpec(BaseModel):
    model_path: str
    ctx_size: int = 16384
    gpu_layers: int = 999


class InspectReq(BaseModel):
    path: str


class CreateJob(BaseModel):
    path: str
    src: str
    tgt: str
    quality: Literal["fast", "high", "best"] = "best"
    model_id: str
    model: ModelSpec
    honorifics: Literal["adapt", "keep"] = "adapt"
    review_glossary: bool = False
    output_dir: str | None = None
    benchmark: dict | None = None
    glossary_seed: dict | None = None  # from glossary.read or another job's bible
    book_type: Literal["literary", "technical"] = "literary"


class SegmentEdit(BaseModel):
    seg_id: str
    text: str


class Retranslate(BaseModel):
    seg_id: str
    instruction: str = ""


class ExportReq(BaseModel):
    partial_label: str | None = None  # set → a separate "partial" file


class PathReq(BaseModel):
    path: str


class GlossaryExport(BaseModel):
    path: str
    bible: dict | None = None  # unsaved edits from the glossary screen


class BenchReq(BaseModel):
    model: ModelSpec
    tgt: str = Field(default="es")


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "llm_running": state.llm.running, "current_job": state.runner.current}


@app.get("/languages", dependencies=[Depends(auth)])
async def languages() -> list[dict]:
    return [{"code": l.code, "name": l.name, "native": l.native} for l in langs.LANGUAGES.values()]


@app.post("/inspect", dependencies=[Depends(auth)])
async def inspect(req: InspectReq) -> dict:
    p = Path(req.path)
    if not p.is_file():
        raise UserError("file_not_found", "file not found", 404)
    try:
        return await asyncio.to_thread(jobs.inspect, p)
    except UserError:
        raise
    except Exception as e:
        log.exception("inspect failed")
        raise UserError("unreadable_book", f"could not read this book: {e}", 422) from e


@app.post("/jobs", dependencies=[Depends(auth)])
async def create_job(req: CreateJob) -> dict:
    langs.validate_pair(req.src, req.tgt)
    if req.glossary_seed:
        glossary.check_pair(req.glossary_seed, req.src, req.tgt)
    if not Path(req.path).is_file():
        raise UserError("file_not_found", "file not found", 404)
    fmt = jobs.detect_format(Path(req.path))
    info = await asyncio.to_thread(jobs.inspect, Path(req.path))
    job = state.store.create_job(
        input_path=req.path, format=fmt, title=info.get("title"), src=req.src, tgt=req.tgt,
        quality=req.quality, model_id=req.model_id,
        options={
            "model_path": req.model.model_path, "ctx_size": req.model.ctx_size,
            "gpu_layers": req.model.gpu_layers, "honorifics": req.honorifics,
            "review_glossary": req.review_glossary, "output_dir": req.output_dir,
            "benchmark": req.benchmark, "cover": info.get("cover"),
            "glossary_seed": req.glossary_seed, "book_type": req.book_type,
        },
    )
    await state.runner.enqueue(job["id"])
    return _public(state.store.get_job(job["id"]))


@app.get("/jobs", dependencies=[Depends(auth)])
async def list_jobs() -> list[dict]:
    return [_public(j) for j in state.store.list_jobs()]


@app.get("/jobs/{job_id}", dependencies=[Depends(auth)])
async def get_job(job_id: str) -> dict:
    return _public(_job_or_404(job_id), with_bible=True)


@app.post("/jobs/{job_id}/pause", dependencies=[Depends(auth)])
async def pause(job_id: str) -> dict:
    _job_or_404(job_id)
    state.runner.pause(job_id)
    return {"ok": True}


@app.post("/jobs/{job_id}/resume", dependencies=[Depends(auth)])
async def resume(job_id: str, req: ModelSpec | None = None) -> dict:
    job = _job_or_404(job_id)
    if job["status"] in ("running", "queued"):
        return {"ok": True}
    if req:  # the model file may have moved or the user picked another GPU split
        state.store.update_job(job_id, options={**job["options"], **req.model_dump()})
    await state.runner.enqueue(job_id)
    return {"ok": True}


@app.post("/jobs/{job_id}/cancel", dependencies=[Depends(auth)])
async def cancel(job_id: str) -> dict:
    _job_or_404(job_id)
    state.runner.cancel(job_id)
    return {"ok": True}


@app.delete("/jobs/{job_id}", dependencies=[Depends(auth)])
async def delete(job_id: str) -> dict:
    _job_or_404(job_id)
    if state.runner.current == job_id:
        raise UserError("job_running", "job is running", 409)
    state.store.delete_job(job_id)
    return {"ok": True}


@app.get("/jobs/{job_id}/bible", dependencies=[Depends(auth)])
async def get_bible(job_id: str) -> dict:
    return _job_or_404(job_id)["bible"] or {}


@app.put("/jobs/{job_id}/bible", dependencies=[Depends(auth)])
async def put_bible(job_id: str, bible: dict) -> dict:
    job = _job_or_404(job_id)
    if state.runner.current == job_id:
        raise UserError("glossary_locked", "cannot edit the glossary while translating; pause first", 409)
    state.store.update_job(job_id, bible=bible)
    if job["status"] == "awaiting_glossary":
        await state.runner.enqueue(job_id)
    return {"ok": True}


@app.get("/jobs/{job_id}/segments", dependencies=[Depends(auth)])
async def segments(job_id: str, offset: int = 0, limit: int = 200, flagged: bool = False, q: str = "") -> dict:
    _job_or_404(job_id)
    rows, total = state.store.find_segments(job_id, flagged, q.strip(), offset, limit)
    keys = ("seg_id", "kind", "chapter", "source", "draft", "final", "flags", "edited")
    return {"items": [{k: r[k] for k in keys} for r in rows], "total": total,
            "counts": state.store.counts(job_id)}


@app.put("/jobs/{job_id}/segment", dependencies=[Depends(auth)])
async def edit_segment(job_id: str, req: SegmentEdit) -> dict:
    job = _job_or_404(job_id)
    row = editing.save(state.store, job, req.seg_id, req.text)
    if not job["stats"].get("dirty"):  # the book on disk no longer matches: offer to rebuild it
        state.store.update_job(job_id, stats={**job["stats"], "dirty": True})
        await state.runner.publish_job(job_id)
    return {k: row[k] for k in ("seg_id", "kind", "chapter", "source", "draft", "final", "flags", "edited")}


@app.post("/jobs/{job_id}/segment/retranslate", dependencies=[Depends(auth)])
async def retranslate_segment(job_id: str, req: Retranslate) -> dict:
    job = _job_or_404(job_id)
    current = state.store.get_job(state.runner.current) if state.runner.current else None
    return await editing.retranslate(state.store, state.llm, current, job, req.seg_id, req.instruction)


@app.post("/jobs/{job_id}/export", dependencies=[Depends(auth)])
async def export(job_id: str, req: ExportReq) -> dict:
    job = _job_or_404(job_id)
    if not req.partial_label and job["status"] != "done":
        raise UserError("export_unfinished", "the translation is not finished", 409)
    return {"path": str(await state.exporter.start(job, req.partial_label))}


@app.post("/jobs/{job_id}/glossary/export", dependencies=[Depends(auth)])
async def export_glossary(job_id: str, req: GlossaryExport) -> dict:
    job = _job_or_404(job_id)
    if req.bible is not None:
        job = {**job, "bible": req.bible}
    if not job["bible"]:
        raise UserError("glossary_empty", "this translation has no glossary", 409)
    await asyncio.to_thread(glossary.export, job, Path(req.path))
    return {"path": req.path}


@app.post("/glossary/read", dependencies=[Depends(auth)])
async def read_glossary(req: PathReq) -> dict:
    return await asyncio.to_thread(glossary.read, Path(req.path))


@app.get("/jobs/{job_id}/page")
async def page_image(job_id: str, n: int, which: Literal["source", "output"], token: str = Query(default="")) -> Response:
    """PNG of one PDF page, for the side-by-side comparison (token in query: used by <img>)."""
    if TOKEN and token != TOKEN:
        raise HTTPException(401, "bad token")
    job = _job_or_404(job_id)
    path = job["input_path"] if which == "source" else job["output_path"]
    if not path or job["format"] != "pdf":
        raise HTTPException(404, "no page")

    def render() -> bytes:
        import pymupdf

        with pymupdf.open(path) as doc:
            if not 0 <= n < doc.page_count:
                raise HTTPException(404, "page out of range")
            return doc[n].get_pixmap(dpi=110).tobytes("png")

    return Response(await asyncio.to_thread(render), media_type="image/png")


# ---- library -------------------------------------------------------------------

class ImportReq(BaseModel):
    dir: str
    paths: list[str]


@app.get("/library", dependencies=[Depends(auth)])
async def library(dir: str) -> list[dict]:
    return await asyncio.to_thread(state.library.scan, Path(dir).expanduser())


@app.get("/library/cover/{cover_id}")
async def library_cover(cover_id: str, token: str = Query(default="")) -> Response:
    if TOKEN and token != TOKEN:
        raise HTTPException(401, "bad token")
    path = state.library.cover_path(cover_id)
    if not path:
        raise HTTPException(404, "no cover")
    return Response(path.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "max-age=31536000"})


@app.post("/library/import", dependencies=[Depends(auth)])
async def library_import(req: ImportReq) -> list[str]:
    return await asyncio.to_thread(import_books, req.paths, Path(req.dir).expanduser())


# ---- conversions ------------------------------------------------------------------

class ConvertReq(BaseModel):
    path: str
    target: Literal["pdf", "epub"]
    options: dict = Field(default_factory=dict)
    out_dir: str | None = None
    title: str | None = None


@app.post("/conversions", dependencies=[Depends(auth)])
async def create_conversion(req: ConvertReq) -> dict:
    p = Path(req.path)
    return await state.conversions.create(p, req.target, req.options,
                                          Path(req.out_dir).expanduser() if req.out_dir else None,
                                          req.title or p.stem)


@app.get("/conversions", dependencies=[Depends(auth)])
async def list_conversions() -> list[dict]:
    return state.conversions.list()


@app.post("/conversions/{cid}/cancel", dependencies=[Depends(auth)])
async def cancel_conversion(cid: str) -> dict:
    state.conversions.cancel(cid)
    return {"ok": True}


@app.delete("/conversions/{cid}", dependencies=[Depends(auth)])
async def delete_conversion(cid: str) -> dict:
    state.conversions.delete(cid)
    return {"ok": True}


@app.post("/benchmark", dependencies=[Depends(auth)])
async def run_benchmark(req: BenchReq) -> dict:
    if state.runner.current:
        raise UserError("translation_running", "a translation is running; pause it to benchmark", 409)
    client = await state.llm.ensure(ServerConfig(**req.model.model_dump()))
    return await benchmark.run(client, req.tgt)


@app.get("/llm/status", dependencies=[Depends(auth)])
async def llm_status() -> dict:
    return _llm_status()


@app.post("/llm/stop", dependencies=[Depends(auth)])
async def llm_stop() -> dict:
    if _llm_busy():
        raise UserError("translation_running", "a translation is running", 409)
    await state.llm.stop()
    status = _llm_status()
    await state.bus.publish({"type": "llm", **status})
    return status


@app.post("/shutdown", dependencies=[Depends(auth)])
async def shutdown() -> dict:
    """Stop llama-server and exit. Used before app exit and before installing updates,
    so no orphaned model server keeps files locked."""
    await state.llm.stop()

    async def _exit() -> None:
        await asyncio.sleep(0.2)  # let the response go out first
        os._exit(0)

    asyncio.create_task(_exit())
    return {"ok": True}


@app.get("/llm/log", dependencies=[Depends(auth)])
async def llm_log() -> list[str]:
    return state.llm.log_tail[-100:]


@app.websocket("/events")
async def events(ws: WebSocket, token: str = Query(default="")) -> None:
    if TOKEN and token != TOKEN:
        await ws.close(code=4401)
        return
    await ws.accept()
    q = state.bus.subscribe()
    try:
        while True:
            ev = await q.get()
            await ws.send_json(ev)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        state.bus.unsubscribe(q)


def _job_or_404(job_id: str) -> dict:
    job = state.store.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


def _public(job: dict, with_bible: bool = False) -> dict:
    out = dict(job)
    if not with_bible:
        out.pop("bible", None)
    return out
