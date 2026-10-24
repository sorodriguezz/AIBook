"""Glossary files: a book's characters and terms, reusable across a saga."""

import json
from pathlib import Path

from .errors import UserError

FORMAT = "aibook-glossary"


def export(job: dict, path: Path) -> None:
    bible = job.get("bible") or {}
    data = {
        "format": FORMAT, "version": 1, "title": job.get("title"), "src": job["src"], "tgt": job["tgt"],
        "style": bible.get("style") or {}, "honorifics": bible.get("honorifics"),
        "characters": [_clean(e) for e in bible.get("characters") or []],
        "terms": [_clean(e) for e in bible.get("terms") or []],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise UserError("glossary_unreadable", f"could not read the glossary: {e}", 422) from e
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise UserError("glossary_unreadable", "not an AIBook glossary file", 422)
    return {
        "title": data.get("title"), "src": data.get("src"), "tgt": data.get("tgt"),
        "style": data.get("style") if isinstance(data.get("style"), dict) else {},
        "characters": [_clean(e) for e in data.get("characters") or [] if _valid(e)],
        "terms": [_clean(e) for e in data.get("terms") or [] if _valid(e)],
    }


def check_pair(glossary: dict, src: str, tgt: str) -> None:
    """Names are keyed by the source language and rendered for the target: both must match."""
    if glossary.get("src") not in (None, src) or glossary.get("tgt") not in (None, tgt):
        raise UserError("glossary_pair", "the glossary is for another language pair", 422,
                        src=glossary.get("src") or "", tgt=glossary.get("tgt") or "")


def _valid(e) -> bool:
    return isinstance(e, dict) and bool(str(e.get("source") or "").strip())


def _clean(e: dict) -> dict:
    return {k: v for k, v in e.items() if k != "locked" and v not in (None, "", [])}
