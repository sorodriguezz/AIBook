import { useEffect, useState } from "react";
import { engine } from "../lib/api";
import { friendlyError } from "../lib/errors";
import { t } from "../lib/i18n";
import type { Job, Segment } from "../lib/types";
import { plain } from "./Reviewer";
import { Button } from "./ui";

export function useJobActivity(id: string, enabled: boolean, running: boolean) {
  const [data, setData] = useState<Awaited<ReturnType<typeof engine.activity>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    let timer: number;
    const poll = async () => {
      let delay = 1800;
      try {
        const next = await engine.activity(id);
        if (!stopped) {
          setData(next);
          setError(null);
        }
      } catch (e) {
        if (!stopped) setError(friendlyError(e));
        delay = 4500;
      }
      if (running && !stopped) timer = window.setTimeout(poll, delay);
    };
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [id, enabled, running, revision]);

  return { data, error, refresh: () => setRevision((n) => n + 1) };
}

export default function LiveActivity({ job, activity }: { job: Job; activity: ReturnType<typeof useJobActivity> }) {
  const [pinned, setPinned] = useState<Segment | null>(null);
  const { data, error, refresh } = activity;
  const current = pinned ? data?.items.find((row) => row.seg_id === pinned.seg_id) ?? pinned : data?.items[0];
  const location = (row: Segment) => t(job.format === "pdf" ? "rv.page" : "job.liveChapter", { n: row.chapter + 1 });

  return (
    <section className="rise rounded-2xl border border-rule bg-card/70 p-5" aria-label={t("job.liveToggle")}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="eyebrow">{t("job.liveToggle")}</h2>
          {data && <div className="tabular mt-1 text-[12px] text-ink-3">{t("job.liveSaved", { n: data.counts.translated })}</div>}
        </div>
        {current && (
          <Button variant="quiet" aria-pressed={!pinned} onClick={() => setPinned(null)}>
            <span className={`h-2 w-2 rounded-full ${pinned ? "bg-rule" : "bg-ribbon"}`} />
            {t("job.liveFollow")}
          </Button>
        )}
      </div>
      {error && (
        <div role="status" className="mt-3 flex items-center justify-between gap-2 rounded-xl bg-ribbon-soft px-3 py-1 text-[12px]">
          {error} <Button variant="quiet" onClick={refresh}>{t("err.retry")}</Button>
        </div>
      )}
      {current ? (
        <>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            {(["original", "translation"] as const).map((kind) => (
              <div key={kind} className="min-w-0 rounded-xl border border-rule/70 bg-paper/70 p-4">
                <div className="eyebrow mb-3">{t(`job.${kind}`)} · {location(current)}</div>
                <p className="reading whitespace-pre-wrap break-words">
                  {plain(kind === "original" ? current.source : current.final ?? current.draft)}
                </p>
              </div>
            ))}
          </div>
          <div className="mt-4 flex flex-wrap gap-1.5" aria-label={t("job.liveRecent")} role="group">
            {data?.items.map((row, i) => (
              <button key={row.seg_id} type="button" aria-pressed={current.seg_id === row.seg_id}
                onClick={() => setPinned(row)} title={plain(row.source).slice(0, 140)}
                className={`tabular rounded-full border px-3 py-1 text-[12px] ${current.seg_id === row.seg_id ? "border-ink-3 bg-paper-2" : "border-rule hover:bg-paper-2"}`}>
                {i === 0 ? t("job.liveLatest") : `−${i}`} · {location(row)}
              </button>
            ))}
          </div>
        </>
      ) : !error && (
        <p role="status" className="mt-4 text-[13px] text-ink-3">{t(data ? "job.liveEmpty" : "job.liveLoading")}</p>
      )}
    </section>
  );
}
