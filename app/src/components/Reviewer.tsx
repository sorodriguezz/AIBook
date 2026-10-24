import { useEffect, useRef, useState } from "react";
import { engine } from "../lib/api";
import { friendlyError } from "../lib/errors";
import { t } from "../lib/i18n";
import type { Job, Segment, SegmentCounts } from "../lib/types";
import { Button } from "./ui";

const PAGE = 40;
// ⟦n⟧ markers (EPUB) and BabelDOC's <style id='1'>/{v1} markup (PDF) are hidden when reading.
const MARKUP = /⟦\/?\d+\/?⟧|<\/?[A-Za-z][^<>]{0,40}>|\{v\d+\}/g;
export const plain = (s: string | null) => (s ?? "").replace(MARKUP, "");

/** Paragraph-by-paragraph review: filter, search, edit, ask the model for another version. */
export default function Reviewer({ job, onEdited }: { job: Job; onEdited?: () => void }) {
  const [filter, setFilter] = useState<"all" | "flagged">("all");
  const [query, setQuery] = useState("");
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Segment[]>([]);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState<SegmentCounts | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    const h = setTimeout(() => setQ(query.trim()), 300);
    return () => clearTimeout(h);
  }, [query]);
  useEffect(() => setOffset(0), [filter, q]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    engine.segments(job.id, { offset, limit: PAGE, flagged: filter === "flagged", q }).then((page) => {
      if (!alive) return;
      setItems((prev) => (offset === 0 ? page.items : [...prev, ...page.items]));
      setTotal(page.total);
      setCounts(page.counts);
      setLoading(false);
    });
    return () => {
      alive = false;
    };
  }, [job.id, offset, filter, q]);

  const replace = (s: Segment) => {
    setItems((prev) => prev.map((x) => (x.seg_id === s.seg_id ? s : x)));
    setCounts((c) => c && { ...c, edited: c.edited + (items.find((x) => x.seg_id === s.seg_id)?.edited ? 0 : 1) });
    onEdited?.();
  };

  if (counts && counts.total === 0 && !loading) {
    return <p className="max-w-[560px] text-[13px] text-ink-3">{job.format === "pdf" ? t("rv.noPdfText") : t("rv.empty")}</p>;
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex rounded-full bg-paper-2 p-1">
          {(["all", "flagged"] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`rounded-full px-3.5 py-1.5 text-[12.5px] transition-colors ${filter === f ? "bg-card text-ink shadow-[0_1px_2px_rgba(0,0,0,.08)]" : "text-ink-2"}`}
            >
              {f === "all" ? t("rv.all") : t("rv.flagged", { n: counts?.flagged ?? 0 })}
            </button>
          ))}
        </div>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("rv.search")}
          className="h-9 min-w-[200px] flex-1 rounded-full border border-rule bg-card px-4 text-[13px] outline-none transition-colors placeholder:text-ink-3 focus:border-ink-3"
        />
        {counts && counts.edited > 0 && <span className="text-[12px] text-ink-3">{t("rv.edited", { n: counts.edited })}</span>}
      </div>
      <p className="mb-3 text-[12px] text-ink-3">{t("rv.hint")}</p>

      <div className="border-t border-rule">
        {items.length === 0 && !loading && <p className="py-6 text-[13px] text-ink-3">{t("rv.empty")}</p>}
        {items.map((s) =>
          open === s.seg_id ? (
            <Editor key={s.seg_id} job={job} seg={s} onClose={() => setOpen(null)} onSaved={(n) => { replace(n); setOpen(null); }} />
          ) : (
            <Row key={s.seg_id} job={job} s={s} onOpen={() => setOpen(s.seg_id)} />
          ),
        )}
      </div>
      {items.length < total && (
        <Button className="mt-6" disabled={loading} onClick={() => setOffset(items.length)}>{t("rv.more")}</Button>
      )}
    </div>
  );
}

function Row({ job, s, onOpen }: { job: Job; s: Segment; onOpen: () => void }) {
  const heading = s.kind === "heading" || s.kind === "toc";
  const cls = heading ? "display text-[19px]" : "reading";
  const translation = s.final ?? s.draft;
  return (
    <div className="grid grid-cols-2 gap-x-10 border-b border-rule/60 py-3">
      <div>
        {job.format === "pdf" && <div className="mb-1 text-[11px] text-ink-3">{t("rv.page", { n: s.chapter + 1 })}</div>}
        <p className={`${cls} text-ink-3`}>{plain(s.source)}</p>
      </div>
      <button
        onClick={onOpen}
        disabled={!translation}
        className="group -mx-2 rounded-lg px-2 text-left transition-colors hover:bg-card disabled:cursor-default disabled:hover:bg-transparent"
        title={s.flags ? t("rv.problems", { p: flagText(s.flags) }) : undefined}
      >
        {job.format === "pdf" && <div className="mb-1 h-[14px]" />}
        <p className={`${cls} ${s.flags && !s.edited ? "underline decoration-amber decoration-dotted underline-offset-4" : ""}`}>
          {translation ? plain(translation) : <span className="text-ink-3">…</span>}
        </p>
        {s.edited ? <span className="mt-1 inline-block text-[11px] text-ribbon">{t("rv.editedTag")}</span> : null}
      </button>
    </div>
  );
}

function Editor({ job, seg, onClose, onSaved }: { job: Job; seg: Segment; onClose: () => void; onSaved: (s: Segment) => void }) {
  const [text, setText] = useState(seg.final ?? seg.draft ?? "");
  const [instruction, setInstruction] = useState("");
  const [proposal, setProposal] = useState<{ text: string; problems: string[] } | null>(null);
  const [busy, setBusy] = useState<"save" | "model" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const area = useRef<HTMLTextAreaElement>(null);
  useEffect(() => area.current?.focus(), []);

  const save = async () => {
    setBusy("save");
    setError(null);
    try {
      onSaved(await engine.editSegment(job.id, seg.seg_id, text));
    } catch (e) {
      setError(friendlyError(e));
      setBusy(null);
    }
  };
  const retranslate = async () => {
    setBusy("model");
    setError(null);
    try {
      setProposal(await engine.retranslate(job.id, seg.seg_id, instruction));
    } catch (e) {
      setError(friendlyError(e));
    }
    setBusy(null);
  };

  return (
    <div className="grid grid-cols-2 gap-x-10 border-b border-rule bg-card/60 py-4">
      <div>
        {job.format === "pdf" && <div className="mb-1 text-[11px] text-ink-3">{t("rv.page", { n: seg.chapter + 1 })}</div>}
        <p className="reading text-ink-2">{plain(seg.source)}</p>
        {seg.flags && <p className="mt-3 text-[12px] text-amber">{t("rv.problems", { p: flagText(seg.flags) })}</p>}
      </div>
      <div>
        <textarea
          ref={area}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") onClose();
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) save();
          }}
          rows={Math.min(14, Math.max(4, Math.ceil(text.length / 70)))}
          className="reading w-full resize-y rounded-xl border border-rule bg-paper px-3 py-2 outline-none focus:border-ink-3"
        />
        <div className="mt-2 flex gap-2">
          <input
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && retranslate()}
            placeholder={t("rv.instruction")}
            className="h-9 flex-1 rounded-full border border-rule bg-paper px-4 text-[12.5px] outline-none placeholder:text-ink-3 focus:border-ink-3"
          />
          <Button disabled={!!busy} onClick={retranslate}>
            {busy === "model" ? <span className="breathe">{t("rv.retranslating")}</span> : t("rv.retranslate")}
          </Button>
        </div>
        {proposal && (
          <div className="rise mt-3 rounded-xl border border-rule bg-paper px-3 py-2">
            <div className="eyebrow mb-1">{t("rv.proposal")}</div>
            <p className="reading">{plain(proposal.text)}</p>
            {proposal.problems.length > 0 && (
              <p className="mt-1 text-[12px] text-amber">{t("rv.problems", { p: proposal.problems.join("; ") })}</p>
            )}
            <Button variant="quiet" className="-ml-2 mt-1" onClick={() => { setText(proposal.text); setProposal(null); }}>
              {t("rv.use")} ↑
            </Button>
          </div>
        )}
        {error && <div className="mt-3 rounded-xl bg-ribbon-soft px-3 py-2 text-[12.5px]">{error}</div>}
        <div className="mt-3 flex justify-end gap-2">
          <Button variant="quiet" onClick={onClose}>{t("rv.cancel")}</Button>
          <Button variant="primary" disabled={!!busy || !text.trim()} onClick={save}>{t("rv.save")}</Button>
        </div>
      </div>
    </div>
  );
}

function flagText(flags: string): string {
  try {
    const list = JSON.parse(flags);
    return Array.isArray(list) ? list.join("; ") : String(list);
  } catch {
    return flags;
  }
}
