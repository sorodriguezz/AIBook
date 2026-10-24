"""The three-pass literary pipeline: analyze → translate → review."""

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .. import langs
from ..eta import EtaTracker, Work
from ..llm.client import Completion, LLMClient, LLMError
from ..store import Store
from . import placeholders, prompts, validators
from .segments import Segment, _is_cjk_char, estimate_tokens

log = logging.getLogger(__name__)

SEG_RE = re.compile(r'<seg\s+id="(\d+)"[^>]*>(.*?)</seg>', re.S)

# Budget per request, in estimated source tokens. Small enough that 8–14B models
# stay accurate, large enough to give them a paragraph-level view.
BATCH_TOKENS = 650
BATCH_MAX_SEGS = 12
CONTEXT_PAIRS = 3
BIBLE_WINDOW = 2600
SYSTEM_OVERHEAD = 900  # rules + style + glossary, estimated


class Stopped(Exception):
    pass


@dataclass
class Settings:
    src: str
    tgt: str
    quality: str  # "fast" | "high" | "best"
    honorifics: str = "adapt"
    book_type: str = "literary"  # "literary" | "technical"


Emit = Callable[[dict], Awaitable[None]]


def batches(segs: list[Segment]) -> list[list[Segment]]:
    out: list[list[Segment]] = []
    cur: list[Segment] = []
    cur_tok = 0
    for s in segs:
        t = estimate_tokens(s.source)
        new_chapter = cur and cur[-1].chapter != s.chapter
        if cur and (new_chapter or cur_tok + t > BATCH_TOKENS or len(cur) >= BATCH_MAX_SEGS):
            out.append(cur)
            cur, cur_tok = [], 0
        cur.append(s)
        cur_tok += t
    if cur:
        out.append(cur)
    return out


def parse_segs(text: str, n: int) -> list[str | None]:
    found: dict[int, str] = {}
    for m in SEG_RE.finditer(text):
        i = int(m.group(1))
        if 1 <= i <= n and i not in found:
            found[i] = m.group(2).strip()
    return [found.get(i) for i in range(1, n + 1)]


class Pipeline:
    def __init__(
        self,
        client: LLMClient,
        store: Store,
        job_id: str,
        settings: Settings,
        eta: EtaTracker,
        emit: Emit,
        should_stop: Callable[[], bool],
    ) -> None:
        self.client = client
        self.store = store
        self.job_id = job_id
        self.s = settings
        self.eta = eta
        self.emit = emit
        self.should_stop = should_stop
        self.bible: dict | None = None
        # Glossary carried over from another book (a saga): its names are kept as-is.
        self.seed: dict | None = None
        self.phase = ""
        self.detail = ""

    # ---- planning ----------------------------------------------------------
    def bible_windows(self, segs: list[Segment]) -> list[str]:
        windows, cur, cur_tok = [], [], 0
        for s in segs:
            if s.kind in ("attr", "meta"):
                continue
            p = s.plain.strip()
            if not p:
                continue
            cur.append(p)
            cur_tok += estimate_tokens(p)
            if cur_tok >= BIBLE_WINDOW:
                windows.append("\n".join(cur))
                cur, cur_tok = [], 0
        if cur:
            windows.append("\n".join(cur))
        if self.s.quality != "best" and len(windows) > 24:
            step = len(windows) / 24
            windows = [windows[int(i * step)] for i in range(24)]
        return windows

    def plan(self, segs: list[Segment], done: dict[str, dict]) -> None:
        ratio = langs.expansion_ratio(self.s.src, self.s.tgt)
        work = Work()
        if self.s.quality in ("high", "best") and self.bible is None:
            windows = self.bible_windows(segs)
            first = self._bible_checkpoint(len(windows))[0]
            for w in windows[first:]:
                work += Work(estimate_tokens(w) + 500, 350)
            work += Work(4000, 1200)  # style + rendering of names
        for b in batches(segs):
            src_tok = sum(estimate_tokens(x.source) for x in b)
            need_draft = any(not done.get(x.id, {}).get("draft") for x in b)
            need_final = self.s.quality == "best" and any(not done.get(x.id, {}).get("final") for x in b)
            if need_draft:
                work += Work(SYSTEM_OVERHEAD + 250 + src_tok, src_tok * ratio * 1.1)
            if need_final:
                work += Work(SYSTEM_OVERHEAD + 250 + src_tok * (1 + ratio), src_tok * ratio * 1.1)
        self.eta.plan(work)

    async def _report(self, preview: tuple[str, str] | None = None) -> None:
        event = {"type": "progress", "job_id": self.job_id, "phase": self.phase, "detail": self.detail,
                 **self.eta.snapshot()}
        if preview:
            event["preview"] = {"source": placeholders.strip(preview[0]), "target": placeholders.strip(preview[1])}
        self.store.update_job(self.job_id, phase=self.phase, progress=event["progress"])
        await self.emit(event)

    def _check_stop(self) -> None:
        if self.should_stop():
            raise Stopped()

    async def _call(self, messages: list[dict], planned: Work, **kw) -> Completion:
        self._check_stop()
        comp = await self.client.chat(messages, **kw)
        self.eta.record(planned, comp.prompt_tokens, comp.completion_tokens, comp.prompt_seconds, comp.gen_seconds)
        return comp

    # ---- pass A: book bible ------------------------------------------------
    def _bible_checkpoint(self, n_windows: int) -> tuple[int, dict]:
        """Where a paused analysis left off: (windows done, entries found so far)."""
        stats = (self.store.get_job(self.job_id) or {}).get("stats", {})
        cp = stats.get("bible_partial")
        if cp and cp.get("windows") == n_windows:
            return cp["done"], cp["known"]
        return 0, seeded_entries(self.seed)

    def _save_bible_checkpoint(self, done: int, n_windows: int, known: dict | None) -> None:
        stats = dict((self.store.get_job(self.job_id) or {}).get("stats", {}))
        if known is None:
            stats.pop("bible_partial", None)
        else:
            stats["bible_partial"] = {"done": done, "windows": n_windows, "known": known}
        self.store.update_job(self.job_id, stats=stats)

    async def build_bible(self, segs: list[Segment]) -> dict:
        self.phase = "analyzing"
        windows = self.bible_windows(segs)
        first, known = self._bible_checkpoint(len(windows))
        for i, w in enumerate(windows):
            if i < first:
                continue
            self.detail = f"{i + 1}/{len(windows)}"
            await self._report()
            planned = Work(estimate_tokens(w) + 500, 350)
            self._check_stop()
            try:
                data, comp = await self.client.chat_json(
                    prompts.bible_extract_messages(self.s.src, w, known, self.s.book_type), temperature=0.1, max_tokens=1500)
                self.eta.record(planned, comp.prompt_tokens, comp.completion_tokens, comp.prompt_seconds, comp.gen_seconds)
            except LLMError as e:
                log.warning("bible window %d failed: %s", i, e)
                self.eta.skip(planned)
                continue
            _merge(known, data)
            self._save_bible_checkpoint(i + 1, len(windows), known)

        sample = "\n".join(windows[:2])[:9000]
        self.detail = "style"
        await self._report()
        style: dict = {}
        try:
            style, comp = await self.client.chat_json(
                prompts.bible_style_messages(self.s.src, self.s.tgt, sample, known, self.s.book_type), temperature=0.2, max_tokens=800)
            self.eta.record(Work(estimate_tokens(sample) + 300, 300), comp.prompt_tokens,
                            comp.completion_tokens, comp.prompt_seconds, comp.gen_seconds)
        except LLMError as e:
            log.warning("style analysis failed: %s", e)

        for kind in ("characters", "terms"):
            entries = known[kind]
            entries = [e for e in entries if not e.get("locked")]
            for start in range(0, len(entries), 40):
                chunk = entries[start:start + 40]
                self._check_stop()
                try:
                    data, _ = await self.client.chat_json(
                        prompts.bible_render_messages(self.s.src, self.s.tgt, chunk, kind, style, self.s.book_type),
                        temperature=0.2, max_tokens=2000)
                except LLMError:
                    continue
                rendered = {it.get("source"): it.get("target") for it in data.get("items", []) if isinstance(it, dict)}
                for e in chunk:
                    target = rendered.get(e["source"])
                    if isinstance(target, str) and _script_ok(target, self.s.tgt):
                        e["target"] = target.strip()
        self.eta.skip(Work(4000, 1200))
        bible = {"style": style, "characters": known["characters"], "terms": known["terms"],
                 "honorifics": self.s.honorifics, "book_type": self.s.book_type}
        self.bible = bible
        self._save_bible_checkpoint(0, 0, None)
        return bible

    # ---- pass B/C: translate and review ------------------------------------
    async def translate_all(self, segs: list[Segment], done: dict[str, dict]) -> None:
        all_batches = batches(segs)
        context: list[tuple[str, str]] = []
        chapters = max((s.chapter for s in segs), default=0) + 1
        ratio = langs.expansion_ratio(self.s.src, self.s.tgt)
        for bi, batch in enumerate(all_batches):
            src_tok = sum(estimate_tokens(x.source) for x in batch)
            self.detail = f"{batch[0].chapter + 1}/{chapters}"
            drafts = [done.get(x.id, {}).get("draft") for x in batch]
            finals = [done.get(x.id, {}).get("final") for x in batch]

            draft_work = Work(SYSTEM_OVERHEAD + 250 + src_tok, src_tok * ratio * 1.1)
            if not all(drafts):
                self.phase = "translating"
                await self._report()
                drafts = await self._translate_batch(batch, context, all_batches, bi, draft_work)
                for seg, d in zip(batch, drafts):
                    if self.s.quality != "best":
                        self.store.save_final(self.job_id, seg.id, d)

            if self.s.quality == "best" and not all(finals):
                self.phase = "reviewing"
                await self._report()
                review_work = Work(SYSTEM_OVERHEAD + 250 + src_tok * (1 + ratio), src_tok * ratio * 1.1)
                finals = await self._review_batch(batch, drafts, context, review_work)
            elif self.s.quality != "best":
                finals = drafts

            context = (context + [(s.plain, placeholders.strip(f)) for s, f in zip(batch, finals)
                                  if s.kind not in ("attr", "meta")])[-CONTEXT_PAIRS:]
            last = next(((s.source, f) for s, f in reversed(list(zip(batch, finals))) if s.kind == "para"), None)
            await self._report(preview=last)

    def _system(self, text: str, review: bool) -> str:
        chars, terms = prompts.relevant_glossary(self.bible, text)
        fn = prompts.review_system if review else prompts.translator_system
        honor = (self.bible or {}).get("honorifics", self.s.honorifics)
        return fn(self.s.src, self.s.tgt, self.bible, chars, terms, honor, self.s.book_type)

    async def _translate_batch(self, batch, context, all_batches, bi, planned: Work) -> list[str]:
        joined = "\n".join(s.plain for s in batch)
        nxt = all_batches[bi + 1][0].plain[:300] if bi + 1 < len(all_batches) else ""
        msgs = [
            {"role": "system", "content": self._system(joined, review=False)},
            {"role": "user", "content": prompts.translate_user(batch, context, nxt, self.s.src, self.s.tgt)},
        ]
        max_tok = int(sum(estimate_tokens(s.source) for s in batch) * 3 + 400)
        try:
            comp = await self._call(msgs, planned, temperature=0.35, max_tokens=max_tok)
            outs = parse_segs(comp.text, len(batch))
        except LLMError as e:
            log.warning("batch failed, falling back to single segments: %s", e)
            self.eta.skip(planned)
            outs = [None] * len(batch)

        results = []
        for seg, out in zip(batch, outs):
            problems = validators.validate(seg.source, out or "", self.s.src, self.s.tgt) if out else ["missing"]
            if problems:
                out, problems = await self._retry_single(seg, context, out, problems)
            self.store.save_draft(self.job_id, seg.id, out, problems or None)
            results.append(out)
        return results

    async def _retry_single(self, seg: Segment, context, best: str | None, best_problems: list[str]):
        """Re-translate one segment alone, keeping the least problematic candidate."""
        for attempt, temp in enumerate((0.2, 0.6)):
            hint = ""
            if best_problems and best_problems != ["missing"]:
                hint = f"\n(A previous attempt had these problems: {'; '.join(best_problems)}. Avoid them.)"
            msgs = [
                {"role": "system", "content": self._system(seg.plain, review=False)},
                {"role": "user", "content": prompts.translate_user([seg], context, "", self.s.src, self.s.tgt) + hint},
            ]
            self._check_stop()
            try:
                comp = await self.client.chat(msgs, temperature=temp, max_tokens=estimate_tokens(seg.source) * 3 + 200)
            except LLMError:
                continue
            cand = parse_segs(comp.text, 1)[0] or comp.text.strip()
            probs = validators.validate(seg.source, cand, self.s.src, self.s.tgt)
            if best is None or len(probs) < len(best_problems):
                best, best_problems = cand, probs
            if not probs:
                break
        if best is None:
            best, best_problems = seg.source, ["translation failed; source kept"]
        elif placeholders.check(seg.source, best):
            # Last resort: keep the words, drop inline formatting for this segment.
            best = placeholders.strip(best)
            best_problems = [p for p in best_problems if "marker" not in p] + ["inline formatting dropped"]
        return best, best_problems

    async def _review_batch(self, batch, drafts, context, planned: Work) -> list[str]:
        joined = "\n".join(s.plain for s in batch)
        msgs = [
            {"role": "system", "content": self._system(joined, review=True)},
            {"role": "user", "content": prompts.review_user(batch, drafts, context, self.s.src, self.s.tgt)},
        ]
        max_tok = int(sum(estimate_tokens(d) for d in drafts) * 1.6 + 400)
        try:
            comp = await self._call(msgs, planned, temperature=0.2, max_tokens=max_tok)
            outs = parse_segs(comp.text, len(batch))
        except LLMError as e:
            log.warning("review failed, keeping drafts: %s", e)
            self.eta.skip(planned)
            outs = [None] * len(batch)
        finals = []
        for seg, draft, out in zip(batch, drafts, outs):
            final = draft
            if out and not validators.validate(seg.source, out, self.s.src, self.s.tgt):
                final = out
            self.store.save_final(self.job_id, seg.id, final)
            finals.append(final)
        return finals

    async def translate_texts(self, texts: list[str]) -> list[str]:
        """Translate a flat list of strings (used by the OCR path)."""
        segs = [Segment(id=str(i), chapter=0, kind="para", source=t) for i, t in enumerate(texts)]
        out: list[str] = []
        context: list[tuple[str, str]] = []
        for b in batches(segs):
            res = await self._translate_batch(b, context, [b], 0, Work())
            out.extend(res)
            context = (context + list(zip([s.plain for s in b], res)))[-CONTEXT_PAIRS:]
        return out


def seeded_entries(seed: dict | None) -> dict:
    """Characters and terms from an imported glossary, locked to their renderings."""
    out: dict = {"characters": [], "terms": []}
    for kind in out:
        for e in (seed or {}).get(kind) or []:
            if isinstance(e, dict) and str(e.get("source") or "").strip():
                out[kind].append({**e, "source": str(e["source"]).strip(), "locked": bool(e.get("target"))})
    return out


def _script_ok(text: str, tgt: str) -> bool:
    """Reject renderings in the wrong writing system (e.g. a Chinese name for a Spanish book)."""
    if not text.strip():
        return False
    has_cjk = any(_is_cjk_char(ch) for ch in text)
    return langs.is_cjk(tgt) or not has_cjk


def _merge(known: dict, data: dict) -> None:
    for kind in ("characters", "terms"):
        index = {e["source"].lower(): e for e in known[kind]}
        for item in data.get(kind) or []:
            if not isinstance(item, dict):
                continue
            src = str(item.get("source") or "").strip()
            if not src or len(src) > 60:
                continue
            existing = index.get(src.lower())
            if existing:
                for k, v in item.items():
                    if v and not existing.get(k):
                        existing[k] = v
            else:
                entry = {k: v for k, v in item.items() if v}
                entry["source"] = src
                known[kind].append(entry)
                index[src.lower()] = entry
