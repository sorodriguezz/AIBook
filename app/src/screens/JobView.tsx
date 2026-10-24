import { openPath, revealItemInDir } from "@tauri-apps/plugin-opener";
import { useEffect, useState } from "react";
import { Bar, Button, Cover, LangPair } from "../components/ui";
import { engine } from "../lib/api";
import { friendlyError } from "../lib/errors";
import { clock, duration, finishTime } from "../lib/format";
import { t, type Key } from "../lib/i18n";
import { modelSpec, useApp } from "../lib/store";
import type { Job, Segment } from "../lib/types";
import Glossary from "./Glossary";

const strip = (s: string | null) => (s ?? "").replace(/⟦\/?\d+\/?⟧/g, "");

export default function JobView({ id }: { id: string }) {
  const job = useApp((s) => s.jobs[id]);
  const progress = useApp((s) => s.progress[id]);
  const go = useApp((s) => s.go);
  const [lastPreview, setLastPreview] = useState<{ source: string; target: string } | null>(null);
  // Immediate feedback while the engine stops; cleared when the status changes.
  const [stopping, setStopping] = useState<"pause" | "cancel" | null>(null);
  useEffect(() => setStopping(null), [job?.status]);

  useEffect(() => {
    if (progress?.preview) setLastPreview(progress.preview);
  }, [progress?.preview]);

  // Elapsed time keeps ticking between engine events.
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (job?.status !== "running") return;
    const h = setInterval(() => setTick((x) => x + 1), 1000);
    return () => clearInterval(h);
  }, [job?.status]);
  const [eventAt, setEventAt] = useState(Date.now());
  useEffect(() => setEventAt(Date.now()), [progress]);
  const sinceEvent = (Date.now() - eventAt) / 1000;
  void tick;

  if (!job) return null;
  const running = job.status === "running" || job.status === "queued";
  const value = job.status === "done" ? 1 : progress?.progress ?? job.progress;
  const eta = progress?.eta_seconds != null ? Math.max(0, progress.eta_seconds - sinceEvent) : null;
  const elapsed = (progress?.elapsed_seconds ?? 0) + (running ? sinceEvent : 0);
  const phase = job.status === "awaiting_glossary" ? "awaiting_glossary" : progress?.phase ?? job.phase ?? "loading_model";

  const resume = async () => {
    const spec = modelSpec(job.model_id);
    await engine.resume(job.id, spec?.spec);
  };

  return (
    <div className="relative mx-auto max-w-[920px] px-12 pb-20">
      <Button variant="quiet" className="-ml-2 mb-6 mt-2" onClick={() => go({ name: "translate" })}>
        ← {t("job.back")}
      </Button>

      <div className="flex items-start gap-8">
        <div className="relative shrink-0">
          <Cover src={job.options.cover} title={job.title} className="h-[150px] w-[100px]" />
          {/* The bookmark ribbon descends as the book is translated. */}
          <div
            className="absolute -top-2 right-4 w-[12px] bg-ribbon transition-[height] duration-1000 ease-out"
            style={{ height: `${18 + value * 150}px`, clipPath: "polygon(0 0,100% 0,100% 100%,50% calc(100% - 7px),0 100%)" }}
          />
        </div>
        <div className="min-w-0 flex-1 pt-1">
          <h1 className="display text-[32px] font-light leading-tight">{job.title}</h1>
          <div className="mt-2 flex items-center gap-3 text-[12.5px] text-ink-3">
            <LangPair src={job.src} tgt={job.tgt} />
            <span>· {t(`cfg.quality.${job.quality}` as Key)}</span>
            <span>· {useApp.getState().catalog.find((m) => m.id === job.model_id)?.name ?? job.model_id}</span>
          </div>
        </div>
      </div>

      {job.status === "awaiting_glossary" ? (
        <Glossary job={job} />
      ) : job.status === "done" ? (
        <Result job={job} />
      ) : (
        <section className="rise mt-12">
          <div className="flex items-end justify-between gap-6">
            <div>
              <div className="eyebrow">{t(`phase.${phase}` as Key)}{progress?.detail && phase === "translating" ? ` · ${t("job.chapter", { d: progress.detail })}` : ""}</div>
              <div className="display tabular mt-3 text-[64px] font-extralight leading-none">
                {job.status === "running" ? (eta != null ? duration(eta) : <span className="breathe text-[40px] text-ink-3">{t("job.calculating")}</span>) : t(`status.${job.status}` as Key)}
              </div>
              {job.status === "running" && (
                <div className="tabular mt-3 flex gap-5 text-[12.5px] text-ink-3">
                  <span>{t("job.remaining")}</span>
                  <span>{clock(elapsed)} {t("job.elapsed")}</span>
                  {finishTime(eta) && <span>{t("job.finish", { t: finishTime(eta)! })}</span>}
                  {progress?.gen_tps ? <span>{t("job.speed", { n: progress.gen_tps })}</span> : null}
                </div>
              )}
            </div>
            <div className="display tabular text-[28px] font-light text-ink-2">{Math.floor(value * 100)}%</div>
          </div>
          <Bar value={value} active={job.status === "running"} className="mt-6" />

          {job.status === "failed" && (
            <div className="mt-6 rounded-xl bg-ribbon-soft px-4 py-3 text-[13px]">
              <div className="font-medium">{t("job.failed")}</div>
              <div className="mt-1 text-ink-2">{friendlyError(new Error(job.error ?? ""), job.stats?.error_code, job.stats?.error_params)}</div>
            </div>
          )}

          <div className="mt-6 flex gap-2">
            {job.status === "running" && (
              <Button disabled={!!stopping} onClick={() => { setStopping("pause"); engine.pause(job.id).catch(() => setStopping(null)); }}>
                {stopping === "pause" ? <span className="breathe">{t("job.pausing")}</span> : t("job.pause")}
              </Button>
            )}
            {(job.status === "paused" || job.status === "failed" || job.status === "cancelled") && (
              <Button variant="primary" onClick={resume}>{job.status === "failed" ? t("job.retry") : t("job.resume")}</Button>
            )}
            {running && (
              <Button variant="danger" disabled={!!stopping} onClick={() => { setStopping("cancel"); engine.cancel(job.id).catch(() => setStopping(null)); }}>
                {stopping === "cancel" ? <span className="breathe">{t("job.cancelling")}</span> : t("job.cancel")}
              </Button>
            )}
            {!running && (
              <Button variant="danger" onClick={async () => { await engine.remove(job.id); useApp.setState((s) => { const j = { ...s.jobs }; delete j[job.id]; return { jobs: j }; }); go({ name: "translate" }); }}>
                {t("job.delete")}
              </Button>
            )}
          </div>

          {lastPreview && (
            <div className="rise mt-12">
              <div className="eyebrow mb-4">{t("job.livePreview")}</div>
              <div className="grid grid-cols-2 gap-10 border-t border-rule pt-5">
                <p className="reading text-ink-3">{lastPreview.source}</p>
                <p key={lastPreview.target} className="reading rise">{lastPreview.target}</p>
              </div>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function Result({ job }: { job: Job }) {
  const elapsed = job.stats?.elapsed_seconds;
  return (
    <section className="rise mt-10">
      <div className="flex items-center gap-3">
        <span className="display text-[22px]">{t("phase.done")}</span>
        {elapsed != null && <span className="tabular text-[13px] text-ink-3">· {duration(elapsed)}</span>}
      </div>
      <div className="mt-5 flex gap-2">
        <Button variant="primary" onClick={() => job.output_path && openPath(job.output_path)}>{t("job.open")}</Button>
        <Button onClick={() => job.output_path && revealItemInDir(job.output_path)}>{t("job.reveal")}</Button>
      </div>
      <div className="mt-12">
        <div className="eyebrow mb-4">{t("job.compare")}</div>
        {job.format === "pdf" ? <PdfCompare job={job} /> : <TextCompare job={job} />}
      </div>
    </section>
  );
}

function TextCompare({ job }: { job: Job }) {
  const [segs, setSegs] = useState<Segment[]>([]);
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    engine.segments(job.id, offset, 60).then((s) => setSegs((prev) => (offset === 0 ? s : [...prev, ...s])));
  }, [job.id, offset]);
  const flagged = segs.filter((s) => s.flags).length;
  return (
    <div>
      {flagged > 0 && <div className="mb-4 text-[12.5px] text-amber">{t("job.flags", { n: flagged })}</div>}
      <div className="grid grid-cols-2 gap-x-10 border-t border-rule">
        <div className="eyebrow py-3">{t("job.original")}</div>
        <div className="eyebrow py-3">{t("job.translation")}</div>
        {segs.filter((s) => s.kind !== "attr" && s.kind !== "meta").map((s) => (
          <Row key={s.seg_id} s={s} />
        ))}
      </div>
      {segs.length >= offset + 60 && (
        <Button className="mt-6" onClick={() => setOffset(offset + 60)}>…</Button>
      )}
    </div>
  );
}

function Row({ s }: { s: Segment }) {
  const heading = s.kind === "heading" || s.kind === "toc";
  const cls = heading ? "display text-[19px] py-3" : "reading py-2";
  return (
    <>
      <p className={`${cls} text-ink-3`}>{strip(s.source)}</p>
      <p className={`${cls} ${s.flags ? "underline decoration-amber decoration-dotted underline-offset-4" : ""}`} title={s.flags ?? undefined}>
        {strip(s.final ?? s.draft)}
      </p>
    </>
  );
}

function PdfCompare({ job }: { job: Job }) {
  const [page, setPage] = useState(0);
  const [urls, setUrls] = useState<{ a: string; b: string } | null>(null);
  useEffect(() => {
    Promise.all([engine.pageUrl(job.id, page, "source"), engine.pageUrl(job.id, page, "output")]).then(([a, b]) => setUrls({ a, b }));
  }, [job.id, page]);
  return (
    <div>
      <div className="grid grid-cols-2 gap-6">
        {urls && (["a", "b"] as const).map((k) => (
          <div key={k}>
            <div className="eyebrow mb-2">{k === "a" ? t("job.original") : t("job.translation")}</div>
            <img src={urls[k]} className="w-full rounded-[3px] bg-white shadow-[0_8px_24px_-12px_rgba(40,25,10,.45)]" draggable={false} />
          </div>
        ))}
      </div>
      <div className="mt-5 flex items-center justify-center gap-4">
        <Button variant="quiet" disabled={page === 0} onClick={() => setPage(page - 1)}>←</Button>
        <span className="tabular text-[13px] text-ink-2">{t("job.page", { n: page + 1 })}</span>
        <Button variant="quiet" onClick={() => setPage(page + 1)}>→</Button>
      </div>
    </div>
  );
}
