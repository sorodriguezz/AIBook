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
    edited INTEGER NOT NULL DEFAULT 0,
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
        cols = {r["name"] for r in self._db.execute("PRAGMA table_info(segments)")}
        if "edited" not in cols:  # databases from before paragraph editing
            self._db.execute("ALTER TABLE segments ADD COLUMN edited INTEGER NOT NULL DEFAULT 0")
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

    def find_segments(self, job_id: str, flagged: bool = False, query: str = "",
                      offset: int = 0, limit: int = 200) -> tuple[list[dict], int]:
        """A page of segments for review, plus the total that match."""
        where, args = "job_id=? AND kind NOT IN ('attr','meta')", [job_id]
        if flagged:
            where += " AND flags IS NOT NULL AND edited=0"
        if query:
            where += " AND (source LIKE ? OR COALESCE(final, draft, '') LIKE ?)"
            args += [f"%{query}%"] * 2
        total = self._db.execute(f"SELECT COUNT(*) FROM segments WHERE {where}", args).fetchone()[0]
        rows = self._db.execute(f"SELECT * FROM segments WHERE {where} ORDER BY idx LIMIT ? OFFSET ?",
                                [*args, limit, offset])
        return [dict(r) for r in rows], total

    def segment(self, job_id: str, seg_id: str) -> dict | None:
        r = self._db.execute("SELECT * FROM segments WHERE job_id=? AND seg_id=?", (job_id, seg_id)).fetchone()
        return dict(r) if r else None

    def save_edit(self, job_id: str, seg_id: str, text: str) -> None:
        """A translation chosen by the user: it wins over drafts and clears warnings."""
        with self._lock:
            self._db.execute(
                "UPDATE segments SET final=?, flags=NULL, edited=1 WHERE job_id=? AND seg_id=?",
                (text, job_id, seg_id),
            )

    def record_paragraph(self, job_id: str, seg_id: str, page: int | None, source: str, translation: str) -> None:
        """Store a paragraph translated outside our pipeline (BabelDOC); user edits are kept."""
        with self._lock:
            self._db.execute(
                "INSERT INTO segments (job_id, seg_id, idx, chapter, kind, source, draft)"
                " VALUES (?, ?, (SELECT COALESCE(MAX(idx), -1) + 1 FROM segments WHERE job_id=?), ?, 'para', ?, ?)"
                " ON CONFLICT(job_id, seg_id) DO UPDATE SET draft=excluded.draft",
                (job_id, seg_id, job_id, page or 0, source, translation),
            )

    def counts(self, job_id: str) -> dict:
        r = self._db.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(CASE WHEN COALESCE(final, draft) IS NOT NULL THEN 1 ELSE 0 END) AS translated,"
            " SUM(CASE WHEN flags IS NOT NULL AND edited=0 THEN 1 ELSE 0 END) AS flagged,"
            " SUM(edited) AS edited"
            " FROM segments WHERE job_id=? AND kind NOT IN ('attr','meta')", (job_id,)).fetchone()
        return {k: r[k] or 0 for k in ("total", "translated", "flagged", "edited")}

    def save_draft(self, job_id: str, seg_id: str, draft: str, flags: list[str] | None = None) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE segments SET draft=?, flags=? WHERE job_id=? AND seg_id=? AND edited=0",
                (draft, json.dumps(flags) if flags else None, job_id, seg_id),
            )

    def save_final(self, job_id: str, seg_id: str, final: str, flags: list[str] | None = None) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE segments SET final=?, flags=COALESCE(?, flags) WHERE job_id=? AND seg_id=? AND edited=0",
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
