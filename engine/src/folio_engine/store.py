"""SQLite persistence for jobs and per-segment checkpoints."""

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .translate.segments import Segment

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    input_path TEXT NOT NULL,
    output_path TEXT,
    format TEXT NOT NULL,
    title TEXT,
    src TEXT NOT NULL,
    tgt TEXT NOT NULL,
    quality TEXT NOT NULL,
    model_id TEXT NOT NULL,
    options TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    phase TEXT,
    progress REAL NOT NULL DEFAULT 0,
    bible TEXT,
    error TEXT,
    stats TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS segments (
    job_id TEXT NOT NULL,
    seg_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    chapter INTEGER NOT NULL,
    chapter_title TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    extra TEXT NOT NULL DEFAULT '{}',
    draft TEXT,
    final TEXT,
    flags TEXT,
    PRIMARY KEY (job_id, seg_id)
);
CREATE INDEX IF NOT EXISTS segments_job_idx ON segments(job_id, idx);
"""

JOB_FIELDS = (
    "id", "input_path", "output_path", "format", "title", "src", "tgt", "quality", "model_id",
    "options", "status", "phase", "progress", "bible", "error", "stats", "created_at", "updated_at",
)


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()
        # Jobs that were running when the app quit are resumable, not failed.
        self._db.execute("UPDATE jobs SET status='paused' WHERE status IN ('running','queued')")

    # ---- jobs ----
    def create_job(self, **fields) -> dict:
        now = time.time()
        job = {
            "id": uuid.uuid4().hex[:12],
            "output_path": None,
            "title": None,
            "options": {},
            "status": "queued",
            "phase": None,
            "progress": 0.0,
            "bible": None,
            "error": None,
            "stats": {},
            "created_at": now,
            "updated_at": now,
            **fields,
        }
        row = {k: _enc(job[k]) for k in JOB_FIELDS}
        with self._lock:
            self._db.execute(
                f"INSERT INTO jobs ({','.join(JOB_FIELDS)}) VALUES ({','.join('?' * len(JOB_FIELDS))})",
                [row[k] for k in JOB_FIELDS],
            )
        return job

    def update_job(self, job_id: str, **fields) -> None:
        fields["updated_at"] = time.time()
        sets = ",".join(f"{k}=?" for k in fields)
        with self._lock:
            self._db.execute(f"UPDATE jobs SET {sets} WHERE id=?", [*(_enc(v) for v in fields.values()), job_id])

    def get_job(self, job_id: str) -> dict | None:
        r = self._db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return _job(r) if r else None

    def list_jobs(self) -> list[dict]:
        return [_job(r) for r in self._db.execute("SELECT * FROM jobs ORDER BY created_at DESC")]

    def delete_job(self, job_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM segments WHERE job_id=?", (job_id,))
            self._db.execute("DELETE FROM jobs WHERE id=?", (job_id,))

    # ---- segments ----
    def has_segments(self, job_id: str) -> bool:
        return self._db.execute("SELECT 1 FROM segments WHERE job_id=? LIMIT 1", (job_id,)).fetchone() is not None

    def add_segments(self, job_id: str, segs: list[Segment]) -> None:
        with self._lock:
            self._db.execute("BEGIN")
            self._db.executemany(
                "INSERT OR IGNORE INTO segments (job_id, seg_id, idx, chapter, chapter_title, kind, source, extra)"
                " VALUES (?,?,?,?,?,?,?,?)",
                [(job_id, s.id, i, s.chapter, s.chapter_title, s.kind, s.source, json.dumps(s.extra))
                 for i, s in enumerate(segs)],
            )
            self._db.execute("COMMIT")

    def segments(self, job_id: str) -> list[dict]:
        return [dict(r) for r in self._db.execute(
            "SELECT * FROM segments WHERE job_id=? ORDER BY idx", (job_id,))]

    def save_draft(self, job_id: str, seg_id: str, draft: str, flags: list[str] | None = None) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE segments SET draft=?, flags=? WHERE job_id=? AND seg_id=?",
                (draft, json.dumps(flags) if flags else None, job_id, seg_id),
            )

    def save_final(self, job_id: str, seg_id: str, final: str, flags: list[str] | None = None) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE segments SET final=?, flags=COALESCE(?, flags) WHERE job_id=? AND seg_id=?",
                (final, json.dumps(flags) if flags else None, job_id, seg_id),
            )

    def translations(self, job_id: str) -> dict[str, str]:
        return {
            r["seg_id"]: (r["final"] or r["draft"])
            for r in self._db.execute("SELECT seg_id, draft, final FROM segments WHERE job_id=?", (job_id,))
            if (r["final"] or r["draft"])
        }


def _enc(v):
    return json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v


def _job(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("options", "stats"):
        d[k] = json.loads(d[k] or "{}")
    d["bible"] = json.loads(d["bible"]) if d["bible"] else None
    return d
