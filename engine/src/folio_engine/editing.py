"""Reviewing a finished (or half-finished) translation paragraph by paragraph."""

import re
from collections import Counter

from .errors import UserError
from .eta import EtaTracker
from .llm.server_manager import LlamaServerManager, ServerConfig
from .store import Store
from .translate import placeholders, prompts, validators
from .translate.pipeline import Pipeline, Settings, parse_segs
from .translate.segments import Segment, estimate_tokens

# BabelDOC's inline markup in PDF paragraphs: <style id='1'>…</style>, {v1}, <b1>…
_PDF_MARKUP_RE = re.compile(r"</?[A-Za-z][^<>]{0,40}>|\{v?\d+\}")
CONTEXT = 3


def check_markup(source: str, text: str) -> str | None:
    """Inline formatting must survive an edit, or the book can't be rebuilt."""
    if not placeholders.has_text(text):
        return "empty"
    err = placeholders.check(source, text)
    if err:
        return err
    if Counter(_PDF_MARKUP_RE.findall(source)) != Counter(_PDF_MARKUP_RE.findall(text)):
        return f"markup differs: expected {_PDF_MARKUP_RE.findall(source)}"
    return None


def save(store: Store, job: dict, seg_id: str, text: str) -> dict:
    seg = store.segment(job["id"], seg_id)
    if not seg:
        raise UserError("segment_not_found", "paragraph not found", 404)
    text = text.strip()
    err = check_markup(seg["source"], text)
    if err:
        raise UserError("edit_markup", err, 422)
    store.save_edit(job["id"], seg_id, text)
    return store.segment(job["id"], seg_id)


async def retranslate(store: Store, llm: LlamaServerManager, current_job: dict | None,
                      job: dict, seg_id: str, instruction: str) -> dict:
    """Propose a new translation for one paragraph; nothing is saved until the user accepts."""
    seg = store.segment(job["id"], seg_id)
    if not seg:
        raise UserError("segment_not_found", "paragraph not found", 404)
    opts = job["options"]
    config = ServerConfig(model_path=opts["model_path"], ctx_size=int(opts.get("ctx_size", 16384)),
                          gpu_layers=int(opts.get("gpu_layers", 999)))
    # Another book is being translated with a different model: switching models would kill it.
    if current_job and current_job["id"] != job["id"] and llm.running and llm.config != config:
        raise UserError("model_busy", "another translation is using a different model", 409)
    client = await llm.ensure(config)

    rows = store.segments(job["id"])
    pos = next(i for i, r in enumerate(rows) if r["seg_id"] == seg_id)
    before = [r for r in rows[:pos] if r["kind"] not in ("attr", "meta") and (r["final"] or r["draft"])][-CONTEXT:]
    after = next((r for r in rows[pos + 1:] if r["kind"] not in ("attr", "meta")), None)
    context = [(placeholders.strip(r["source"]), placeholders.strip(r["final"] or r["draft"])) for r in before]

    settings = Settings(job["src"], job["tgt"], job["quality"], opts.get("honorifics", "adapt"))
    pipe = Pipeline(client, store, job["id"], settings, EtaTracker(), _noop, lambda: False)
    pipe.bible = job["bible"]
    segment = Segment(seg_id, seg["chapter"], seg["kind"], seg["source"])
    user = prompts.translate_user([segment], context, placeholders.strip(after["source"])[:300] if after else "",
                                  job["src"], job["tgt"])
    if _PDF_MARKUP_RE.search(seg["source"]):
        user += "\nKeep every markup tag such as <style id='1'>…</style> or {v1} exactly as in the source."
    current = seg["final"] or seg["draft"]
    if current:
        user += f"\n\nCurrent translation, which the reader wants improved:\n{current}"
    if instruction.strip():
        user += f"\n\nThe reader's instruction for this passage (follow it): {instruction.strip()}"
    messages = [{"role": "system", "content": pipe._system(segment.plain, review=False)},
                {"role": "user", "content": user}]
    comp = await client.chat(messages, temperature=0.4, max_tokens=estimate_tokens(seg["source"]) * 3 + 300)
    text = (parse_segs(comp.text, 1)[0] or comp.text).strip()
    problems = validators.validate(seg["source"], text, job["src"], job["tgt"])
    if err := check_markup(seg["source"], text):
        problems.append(err)
    return {"text": text, "problems": problems}


async def _noop(_: dict) -> None:
    pass
