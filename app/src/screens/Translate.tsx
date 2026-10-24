import { getCurrentWebview } from "@tauri-apps/api/webview";
import { open } from "@tauri-apps/plugin-dialog";
import { useEffect, useState } from "react";
import { revealItemInDir } from "@tauri-apps/plugin-opener";
import { Bar, Button, Cover, LangPair } from "../components/ui";
import { native } from "../lib/api";
import { duration, relative } from "../lib/format";
import { t, type Key } from "../lib/i18n";
import { queuePosition, useApp } from "../lib/store";
import type { Job } from "../lib/types";

const ACCEPT = /\.(pdf|epub)$/i;

export default function Translate() {
  const { jobs, go, engineError, connectEngine } = useApp();
  const [retrying, setRetrying] = useState(false);
  const [hover, setHover] = useState(false);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    getCurrentWebview()
      .onDragDropEvent((e) => {
        const p = e.payload;
        if (p.type === "enter" || p.type === "over") setHover(true);
        else if (p.type === "leave") setHover(false);
        else if (p.type === "drop") {
          setHover(false);
          const file = p.paths.find((x) => ACCEPT.test(x));
          if (file) go({ name: "configure", path: file });
        }
      })
      .then((u) => (unlisten = u));
    return () => unlisten?.();
  }, [go]);

  const choose = async () => {
    const file = await open({ multiple: false, filters: [{ name: "Libros", extensions: ["pdf", "epub"] }] });
    if (typeof file === "string") go({ name: "configure", path: file });
  };

  const list = Object.values(jobs).sort((a, b) => b.created_at - a.created_at);

  return (
    <div className="mx-auto max-w-[880px] px-12 pb-16">
      <header className="rise mb-8 mt-6">
        <h1 className="display text-[44px] font-light leading-[1.05]">{t("home.title")}</h1>
        <p className="mt-3 max-w-[460px] text-[15px] text-ink-2">{t("home.subtitle")}</p>
      </header>

      <button
        onClick={choose}
        className={`rise group relative block w-full overflow-hidden rounded-[22px] border border-dashed text-left transition-all duration-300 ${
          hover ? "border-ribbon bg-ribbon-soft" : "border-rule hover:border-ink-3 hover:bg-card/60"
        }`}
        style={{ animationDelay: "80ms" }}
      >
        <div className="flex items-center gap-8 px-10 py-12">
          {/* Stacked pages illustration */}
          <div className="relative h-[92px] w-[72px] shrink-0">
            <div className={`absolute inset-0 rounded-[3px] border border-rule bg-card transition-transform duration-500 ${hover ? "-rotate-6 -translate-x-2" : "-rotate-3"}`} />
            <div className={`absolute inset-0 rounded-[3px] border border-rule bg-card transition-transform duration-500 ${hover ? "rotate-6 translate-x-2" : "rotate-2"}`} />
            <div className="absolute inset-0 flex flex-col gap-[5px] rounded-[3px] border border-rule bg-card p-2.5">
              {[90, 70, 85, 60, 78].map((w, i) => (
                <div key={i} className="h-[3px] rounded-full bg-rule" style={{ width: `${w}%` }} />
              ))}
            </div>
            <div className={`absolute -top-1 right-3 w-[9px] bg-ribbon transition-all duration-500 ${hover ? "h-[64px]" : "h-[38px]"}`} style={{ clipPath: "polygon(0 0,100% 0,100% 100%,50% 85%,0 100%)" }} />
          </div>
          <div>
            <div className="display text-[24px] font-normal">{t("home.drop")}</div>
            <div className="mt-1 text-[13px] text-ink-3">{t("home.drop.hint")}</div>
            <div className="mt-5 inline-flex h-9 items-center rounded-full bg-ink px-4 text-[13px] font-medium text-paper transition-colors group-hover:bg-ribbon">
              {t("home.choose")}
            </div>
          </div>
        </div>
      </button>

      {engineError && (
        <div className="mt-6 flex items-center gap-4 rounded-xl border border-ribbon/40 bg-ribbon-soft px-4 py-3 text-[13px] text-ink-2">
          <span className="flex-1">{engineError}</span>
          <Button
            variant="quiet"
            onClick={async () => {
              const log = await native.engineLog();
              if (log) revealItemInDir(log).catch(() => {});
            }}
          >
            {t("err.showLog")}
          </Button>
          <Button
            disabled={retrying}
            onClick={async () => {
              setRetrying(true);
              await connectEngine();
              setRetrying(false);
            }}
          >
            {retrying ? t("err.retrying") : t("err.retry")}
          </Button>
        </div>
      )}

      <section className="mt-14">
        <div className="eyebrow mb-4">{t("home.recent")}</div>
        {list.length === 0 ? (
          <p className="text-[13px] text-ink-3">{t("home.empty")}</p>
        ) : (
          <ul className="divide-y divide-rule border-y border-rule">
            {list.map((j, i) => (
              <JobRow key={j.id} job={j} delay={i * 40} onOpen={() => go({ name: "job", id: j.id })} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function JobRow({ job, onOpen, delay }: { job: Job; onOpen: () => void; delay: number }) {
  const progress = useApp((s) => s.progress[job.id]);
  const value = job.status === "done" ? 1 : progress?.progress ?? job.progress;
  const running = job.status === "running";
  return (
    <li className="rise" style={{ animationDelay: `${delay}ms` }}>
      <button onClick={onOpen} className="flex w-full items-center gap-5 py-4 text-left transition-colors hover:bg-card/50">
        <Cover src={job.options.cover} title={job.title} className="h-[62px] w-[44px] shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="display truncate text-[17px]">{job.title}</div>
          <div className="mt-1 flex items-center gap-3">
            <LangPair src={job.src} tgt={job.tgt} />
            <span className="text-[12px] text-ink-3">· {job.format.toUpperCase()}</span>
            <span className="text-[12px] text-ink-3">· {relative(job.created_at)}</span>
          </div>
          {job.status !== "done" && job.status !== "cancelled" && <Bar value={value} active={running} className="mt-3 max-w-[340px]" />}
        </div>
        <div className="w-[150px] shrink-0 text-right">
          <div className={`text-[12.5px] font-medium ${job.status === "failed" ? "text-ribbon" : running ? "text-ink" : "text-ink-2"}`}>
            {job.status === "queued" && queuePosition(job.id) ? t("job.queued", { n: queuePosition(job.id)! }) : t(`status.${job.status}` as Key)}
          </div>
          {running && progress?.eta_seconds != null && (
            <div className="tabular mt-0.5 text-[12px] text-ink-3">{duration(progress.eta_seconds)}</div>
          )}
        </div>
      </button>
    </li>
  );
}

