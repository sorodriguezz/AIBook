import { save } from "@tauri-apps/plugin-dialog";
import { openPath, revealItemInDir } from "@tauri-apps/plugin-opener";
import { useEffect, useState } from "react";
import Reviewer from "../components/Reviewer";
import { Bar, Button, Cover, LangPair } from "../components/ui";
import { engine, native } from "../lib/api";
import { friendlyError } from "../lib/errors";
import { basename, clock, duration, finishTime } from "../lib/format";
import { t, type Key } from "../lib/i18n";
import { modelSpec, queuePosition, useApp } from "../lib/store";
import type { Job } from "../lib/types";
import Glossary from "./Glossary";

export default function JobView({ id }: { id: string }) {
  const job = useApp((s) => s.jobs[id]);
  const progress = useApp((s) => s.progress[id]);
  const go = useApp((s) => s.go);
  const [lastPreview, setLastPreview] = useState<{ source: string; target: string } | null>(null);
  // Immediate feedback while the engine stops; cleared when the status changes.
  const [stopping, setStopping] = useState<"pause" | "cancel" | null>(null);
  const [reviewing, setReviewing] = useState(false);
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
            {job.options.book_type === "technical" && <span>· {t("cfg.bookType.technical")}</span>}
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
              <div className="eyebrow">{t(`phase.${phase}` as Key)}{progress?.detail && phase === "translating"
                ? ` · ${t(progress.detail_kind === "paragraphs" ? "job.paragraphs" : "job.chapter", { d: progress.detail.replace("/", " / ") })}`
                : ""}</div>
              <div className="display tabular mt-3 text-[64px] font-extralight leading-none">
                {job.status === "running" ? (eta != null ? duration(eta) : <span className="breathe text-[40px] text-ink-3">{t("job.calculating")}</span>)
                  : job.status === "queued" && queuePosition(job.id) ? t("job.queued", { n: queuePosition(job.id)! })
                  : t(`status.${job.status}` as Key)}
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
              <button
                className="mt-2 text-[12px] text-ink-3 underline-offset-2 hover:text-ink-2 hover:underline"
                onClick={async () => {
                  const log = await native.engineLog();
                  if (log) revealItemInDir(log).catch(() => {});
                }}
              >
                {t("err.showLog")}
              </button>
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

          {job.status !== "queued" && job.progress > 0 && (
            <div className="mt-8 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-rule pt-5">
              <ExportButton job={job} partial />
              <Button variant="quiet" onClick={() => setReviewing(!reviewing)}>{t("job.review")} {reviewing ? "↑" : "↓"}</Button>
            </div>
          )}
          {reviewing && (
            <div className="rise mt-6">
              <Reviewer job={job} />
            </div>
          )}

          {lastPreview && !reviewing && (
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
  const [tab, setTab] = useState<"pages" | "text">(job.format === "pdf" ? "pages" : "text");
  const [glossaryError, setGlossaryError] = useState<string | null>(null);
  const exportGlossary = async () => {
    setGlossaryError(null);
    const path = await save({
      defaultPath: `${t("gl.fileName")} - ${job.title ?? "AIBook"}.json`,
      filters: [{ name: "AIBook", extensions: ["json"] }],
    });
    if (!path) return;
    try {
      await engine.exportGlossary(job.id, path);
      revealItemInDir(path).catch(() => {});
    } catch (e) {
      setGlossaryError(friendlyError(e));
    }
  };
  return (
    <section className="rise mt-10">
      <div className="flex items-center gap-3">
        <span className="display text-[22px]">{t("phase.done")}</span>
        {elapsed != null && <span className="tabular text-[13px] text-ink-3">· {duration(elapsed)}</span>}
      </div>
      <div className="mt-5 flex flex-wrap gap-2">
        <Button variant="primary" onClick={() => job.output_path && openPath(job.output_path)}>{t("job.open")}</Button>
        <Button onClick={() => job.output_path && revealItemInDir(job.output_path)}>{t("job.reveal")}</Button>
        {job.quality !== "fast" && <Button variant="quiet" onClick={exportGlossary}>{t("job.exportGlossary")}</Button>}
      </div>
      {glossaryError && <div className="mt-4 rounded-xl bg-ribbon-soft px-4 py-3 text-[13px]">{glossaryError}</div>}

      <RebuildBanner job={job} />

      <div className="mt-12">
        <div className="mb-4 flex items-center gap-4">
          <div className="eyebrow">{t("rv.title")}</div>
          {job.format === "pdf" && (
            <div className="flex rounded-full bg-paper-2 p-1">
              {(["pages", "text"] as const).map((k) => (
                <button
                  key={k}
                  onClick={() => setTab(k)}
                  className={`rounded-full px-3 py-1 text-[12px] transition-colors ${tab === k ? "bg-card text-ink shadow-[0_1px_2px_rgba(0,0,0,.08)]" : "text-ink-2"}`}
                >
                  {t(k === "pages" ? "rv.pages" : "rv.text")}
                </button>
              ))}
            </div>
          )}
        </div>
        {tab === "pages" ? <PdfCompare job={job} /> : <Reviewer job={job} />}
      </div>
    </section>
  );
}

/** After corrections, the file on disk is stale until the book is rebuilt. */
function RebuildBanner({ job }: { job: Job }) {
  const ev = useApp((s) => s.exports[job.id]);
  const mine = ev && !ev.path.includes(` — ${t("ex.partialLabel")}`);
  const running = mine && ev.state === "running";
  if (!job.stats?.dirty && !(mine && (running || ev.state === "failed" || Date.now() / 1000 - ev.at < 8))) return null;
  return (
    <div className="rise mt-6 flex items-center gap-4 rounded-xl border border-rule bg-card px-4 py-3 text-[13px]">
      <span className="flex-1 text-ink-2">
        {running ? t("rv.rebuilding", { p: Math.round(ev.progress * 100) })
          : mine && ev.state === "failed" ? friendlyError(new Error(ev.error ?? ""), ev.error_code)
          : job.stats?.dirty ? t("rv.dirty") : t("rv.rebuilt")}
      </span>
      {job.stats?.dirty && !running && (
        <Button variant="primary" onClick={() => engine.exportBook(job.id).catch(() => {})}>{t("rv.rebuild")}</Button>
      )}
    </div>
  );
}

/** Writes what is translated so far to a separate file, to start reading early. */
function ExportButton({ job, partial }: { job: Job; partial?: boolean }) {
  const ev = useApp((s) => s.exports[job.id]);
  const [error, setError] = useState<string | null>(null);
  const start = async () => {
    setError(null);
    try {
      await engine.exportBook(job.id, partial ? t("ex.partialLabel") : undefined);
    } catch (e) {
      setError(friendlyError(e));
    }
  };
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button disabled={ev?.state === "running"} onClick={start} title={t("ex.hint")}>
        {ev?.state === "running" ? <span className="breathe">{t("ex.running", { p: Math.round(ev.progress * 100) })}</span> : t("ex.partial")}
      </Button>
      {ev?.state === "done" && (
        <button className="text-[12.5px] text-ink-2 underline-offset-2 hover:underline" onClick={() => openPath(ev.path)}>
          {t("ex.done", { f: basename(ev.path) })}
        </button>
      )}
      {ev?.state === "failed" && <span className="text-[12.5px] text-ribbon">{friendlyError(new Error(ev.error ?? ""), ev.error_code)}</span>}
      {error && <span className="text-[12.5px] text-ribbon">{error}</span>}
    </div>
  );
}

function PdfCompare({ job }: { job: Job }) {
  const [page, setPage] = useState(0);
  const [urls, setUrls] = useState<{ a: string; b: string } | null>(null);
  useEffect(() => {
    // The output changes when the book is rebuilt: bust the image cache.
    const v = `&v=${job.stats?.exported_at ?? 0}`;
    Promise.all([engine.pageUrl(job.id, page, "source"), engine.pageUrl(job.id, page, "output")]).then(([a, b]) => setUrls({ a, b: b + v }));
  }, [job.id, page, job.stats?.exported_at]);
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
