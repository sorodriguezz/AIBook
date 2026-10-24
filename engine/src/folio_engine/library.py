"""The user's book library: a folder of PDFs and EPUBs.

Metadata and cover thumbnails are cached by (path, mtime, size) so rescanning
a large library is instant; only new or changed files are opened.
"""

import hashlib
import json
import logging
import os
import posixpath
import shutil
import sqlite3
import threading
import zipfile
from pathlib import Path

from lxml import etree

from .translate import langdetect

log = logging.getLogger(__name__)

BOOK_EXTS = {".pdf", ".epub"}
MAX_DEPTH = 3
COVER_WIDTH = 360


class Library:
    def __init__(self, data_dir: Path) -> None:
        self.covers = data_dir / "covers"
        self.covers.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(data_dir / "library.db", check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS books (path TEXT PRIMARY KEY, mtime REAL, size INTEGER, meta TEXT)")
        self._lock = threading.Lock()

    def scan(self, root: Path) -> list[dict]:
        root.mkdir(parents=True, exist_ok=True)
        books = []
        for path in _walk(root):
            try:
                st = path.stat()
                books.append(self._meta(path, st.st_mtime, st.st_size))
            except Exception as e:  # one broken file must not hide the rest
                log.warning("skipping %s: %s", path, e)
        return sorted(books, key=lambda b: b["added"], reverse=True)

    def _meta(self, path: Path, mtime: float, size: int) -> dict:
        key = str(path)
        row = self._db.execute("SELECT mtime, size, meta FROM books WHERE path=?", (key,)).fetchone()
        if row and row[0] == mtime and row[1] == size:
            return json.loads(row[2])
        meta = read_meta(path)
        meta.update({"path": key, "size": size, "added": mtime, "format": path.suffix.lower()[1:],
                     "cover_id": _cover_id(path, mtime)})
        cover = self.covers / f"{meta['cover_id']}.jpg"
        if not cover.exists():
            data = render_cover(path)
            if data:
                cover.write_bytes(data)
        meta["has_cover"] = cover.exists()
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO books VALUES (?,?,?,?)", (key, mtime, size, json.dumps(meta)))
        return meta

    def cover_path(self, cover_id: str) -> Path | None:
        if not cover_id.isalnum():
            return None
        p = self.covers / f"{cover_id}.jpg"
        return p if p.exists() else None


def import_books(paths: list[str], root: Path) -> list[str]:
    """Copy books into the library without overwriting anything."""
    root.mkdir(parents=True, exist_ok=True)
    added = []
    for p in map(Path, paths):
        if p.suffix.lower() not in BOOK_EXTS or not p.is_file():
            continue
        if p.resolve().parent == root.resolve():
            added.append(str(p))
            continue
        target = root / p.name
        n = 2
        while target.exists():
            target = root / f"{p.stem} ({n}){p.suffix}"
            n += 1
        shutil.copy2(p, target)
        added.append(str(target))
    return added


def _walk(root: Path):
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if len(Path(dirpath).parts) - root_depth >= MAX_DEPTH:
            dirnames[:] = []
        for f in filenames:
            p = Path(dirpath) / f
            if not f.startswith(".") and p.suffix.lower() in BOOK_EXTS and ".part." not in f:
                yield p


def _cover_id(path: Path, mtime: float) -> str:
    return hashlib.sha1(f"{path}|{mtime}".encode()).hexdigest()[:20]


# ---- metadata -------------------------------------------------------------------

def read_meta(path: Path) -> dict:
    if path.suffix.lower() == ".epub":
        return _epub_meta(path)
    return _pdf_meta(path)


def _pdf_meta(path: Path) -> dict:
    import pymupdf

    with pymupdf.open(path) as doc:
        meta = doc.metadata or {}
        sample = " ".join(doc[i].get_text() for i in range(min(doc.page_count, 12)))[:6000]
        return {"title": (meta.get("title") or "").strip() or path.stem,
                "author": (meta.get("author") or "").strip(),
                "language": langdetect.detect(sample), "pages": doc.page_count}


def _epub_meta(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        container = etree.fromstring(z.read("META-INF/container.xml"))
        opf_path = container.find(".//{*}rootfile").get("full-path")
        opf = etree.fromstring(z.read(opf_path))
        dc = "{http://purl.org/dc/elements/1.1/}"
        title = (opf.findtext(f".//{dc}title") or "").strip() or path.stem
        author = (opf.findtext(f".//{dc}creator") or "").strip()
        lang = (opf.findtext(f".//{dc}language") or "").strip().lower()[:2] or None
        if lang not in ("en", "es", "fr", "de", "it", "ja", "zh"):
            lang = _epub_detect(z, opf, opf_path)
        chapters = len(opf.findall(".//{*}itemref"))
        return {"title": title, "author": author, "language": lang, "chapters": chapters}


def _epub_detect(z: zipfile.ZipFile, opf, opf_path: str) -> str | None:
    base = posixpath.dirname(opf_path)
    items = {i.get("id"): i.get("href") for i in opf.iter("{*}item")}
    text = ""
    for ref in opf.iter("{*}itemref"):
        href = items.get(ref.get("idref"))
        if not href:
            continue
        try:
            doc = etree.fromstring(z.read(posixpath.join(base, href) if base else href),
                                   etree.XMLParser(recover=True))
            text += " ".join(doc.itertext())
        except Exception:
            continue
        if len(text) > 6000:
            break
    return langdetect.detect(text[:6000])


def render_cover(path: Path) -> bytes | None:
    try:
        import pymupdf

        if path.suffix.lower() == ".epub":
            data = _epub_cover_bytes(path)
            if data:
                img = pymupdf.Pixmap(data)
                if img.alpha:
                    img = pymupdf.Pixmap(img, 0)
                if img.width > COVER_WIDTH:
                    img = pymupdf.Pixmap(img, COVER_WIDTH, int(img.height * COVER_WIDTH / img.width))
                return img.tobytes("jpeg", jpg_quality=82)
        with pymupdf.open(path) as doc:
            page = doc[0]
            zoom = COVER_WIDTH / max(1, page.rect.width)
            return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("jpeg", jpg_quality=82)
    except Exception as e:
        log.info("no cover for %s: %s", path, e)
        return None


def _epub_cover_bytes(path: Path) -> bytes | None:
    from .formats.epub import EpubBook

    cover = EpubBook(path).cover_bytes()
    return cover[0] if cover else None
