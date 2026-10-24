import { useEffect, useState } from "react";
import { Bar, Button, FitBadge, LANGS, Strength } from "../components/ui";
import { engine, native, type LlmStatus } from "../lib/api";
import { friendlyError } from "../lib/errors";
import { bestQuant, estimate } from "../lib/estimate";
import { bytes, duration, speed } from "../lib/format";
import { t, type Key } from "../lib/i18n";
import { useApp } from "../lib/store";
import type { CatalogModel } from "../lib/types";

export default function Models() {
  const { catalog, hardware } = useApp();
  return (
    <div className="mx-auto max-w-[920px] px-12 pb-20">
      <header className="rise mb-10 mt-6">
        <h1 className="display text-[44px] font-light leading-none">{t("m.title")}</h1>
        <p className="mt-3 text-[15px] text-ink-2">{t("m.subtitle")}</p>
      </header>

      {hardware && (
        <section className="rise mb-12 grid grid-cols-3 gap-px overflow-hidden rounded-2xl border border-rule bg-rule" style={{ animationDelay: "60ms" }}>
          <Stat label={t("m.ram")} value={bytes(hardware.ram_bytes)} sub={hardware.cpu} />
          <Stat
            label={t("m.gpu")}
            value={hardware.gpu_budget_bytes ? bytes(hardware.gpu_budget_bytes) : "—"}
            sub={hardware.unified_memory ? "Memoria unificada" : hardware.gpus[0]?.name ?? "CPU"}
          />
          <Stat label={t("m.backend")} value={hardware.backend} sub={`${hardware.os} · ${hardware.arch} · ${hardware.cores} núcleos`} />
        </section>
      )}

      <LoadedModel />

      <div className="space-y-5">
        {catalog.map((m, i) => (
          <ModelCard key={m.id} m={m} delay={100 + i * 50} />
        ))}
      </div>
    </div>
  );
}

/** Which model currently holds memory, with a way to free it. */
function LoadedModel() {
  const { catalog, hardware, settings } = useApp();
  const status = useLlmStatus();
  const [error, setError] = useState<string | null>(null);
  const [freeing, setFreeing] = useState(false);
  if (!status?.running || !status.model_path) return null;

  const file = status.model_path.split(/[\\/]/).pop()!;
  const found = catalog
    .flatMap((m) => Object.entries(m.quants).map(([q, v]) => ({ m, q, v })))
    .find((x) => x.v.file === file);
  const mem = found ? estimate(found.m, found.q, status.ctx_size ?? settings.ctxSize, hardware).total : null;

  const free = async () => {
    setFreeing(true);
    setError(null);
    try {
      await engine.llmStop();
      useLlmStatus.refresh();
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setFreeing(false);
    }
  };

  return (
    <section className="rise mb-8 flex items-center gap-4 rounded-2xl border border-rule bg-card px-5 py-4">
      <span className="breathe h-2 w-2 shrink-0 rounded-full bg-moss" />
      <div className="min-w-0 flex-1">
        <div className="text-[13.5px]">
          {t("m.inMemory")} <span className="font-medium">{found ? `${found.m.name} · ${found.q}` : file}</span>
          {mem != null && <span className="tabular text-ink-3"> · ≈ {bytes(mem)}</span>}
        </div>
        <div className="mt-0.5 text-[12px] text-ink-3">
          {status.busy ? t("m.inUse") : status.unload_in != null ? t("m.unloadIn", { t: duration(status.unload_in) }) : ""}
        </div>
        {error && <div className="mt-1 text-[12px] text-ribbon">{error}</div>}
      </div>
      <Button disabled={status.busy || freeing} onClick={free} title={status.busy ? t("m.inUse") : undefined}>
        {freeing ? <span className="breathe">{t("m.freeing")}</span> : t("m.free")}
      </Button>
    </section>
  );
}

/** Polls the engine for the loaded model while the Models screen is open. */
function useLlmStatus(): LlmStatus | null {
  const [status, setStatus] = useState<LlmStatus | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => engine.llmStatus().then((s) => alive && setStatus(s)).catch(() => {});
    useLlmStatus.refresh = load;
    load();
    const h = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(h);
    };
  }, []);
  return status;
}
useLlmStatus.refresh = () => {};

function Stat({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="bg-card px-5 py-4">
      <div className="eyebrow">{label}</div>
      <div className="display tabular mt-2 text-[24px] font-light">{value}</div>
      <div className="mt-1 truncate text-[11.5px] text-ink-3" title={sub}>{sub}</div>
    </div>
  );
}

function ModelCard({ m, delay }: { m: CatalogModel; delay: number }) {
  const { hardware, settings, updateSettings, installed, downloads, partials, refreshInstalled } = useApp();
  const ctx = settings.ctxSize;
  const [picked, setPicked] = useState(settings.chosenQuant[m.id] ?? bestQuant(m, ctx, hardware));
  // While one quantization of this model downloads, the card stays on it so the
  // progress never disappears and two variants never compete for bandwidth.
  const busyQuant = Object.keys(m.quants).find((k) => {
    const d = downloads[`${m.id}:${k}`];
    return d && (d.state === "downloading" || d.state === "verifying");
  });
  const quant = busyQuant ?? picked;
  const [measuring, setMeasuring] = useState(false);
  const [benchError, setBenchError] = useState<string | null>(null);

  const q = m.quants[quant];
  const est = estimate(m, quant, ctx, hardware);
  const key = `${m.id}:${quant}`;
  const dl = downloads[key];
  const file = installed.find((i) => i.file === q.file);
  const partial = partials[q.file];
  const active = dl && (dl.state === "downloading" || dl.state === "verifying");
  const bench = settings.benchmarks[key];

  const choose = (k: string) => {
    if (busyQuant) return;
    setPicked(k);
    updateSettings({ chosenQuant: { ...settings.chosenQuant, [m.id]: k } });
  };

  const download = () => native.startDownload({ key, url: q.url, file: q.file, size: q.size, sha256: q.sha256 });

  const measure = async () => {
    if (!file) return;
    setMeasuring(true);
    setBenchError(null);
    try {
      const b = await engine.benchmark({ model_path: file.path, ctx_size: ctx, gpu_layers: est.gpuLayers });
      await updateSettings({ benchmarks: { ...settings.benchmarks, [key]: b } });
      useLlmStatus.refresh();
    } catch (e: any) {
      setBenchError(friendlyError(e));
    } finally {
      setMeasuring(false);
    }
  };

  const remove = async (path: string) => {
    setBenchError(null);
    try {
      // The file can't be removed (on Windows) while llama-server has it open.
      const s = await engine.llmStatus();
      if (s.running && s.model_path === path) await engine.llmStop();
      await native.deleteModel(q.file);
      refreshInstalled();
      useLlmStatus.refresh();
    } catch (e) {
      setBenchError(friendlyError(e));
    }
  };

  const progress = active ? dl.downloaded / Math.max(1, dl.total) : partial ? partial / q.size : 0;

  return (
    <article className="rise rounded-[20px] border border-rule bg-card p-6 transition-shadow hover:shadow-[0_12px_32px_-20px_rgba(40,25,10,.4)]" style={{ animationDelay: `${delay}ms` }}>
      <div className="flex items-start justify-between gap-6">
        <div className="min-w-0">
          <div className="flex items-baseline gap-3">
            <h2 className="display text-[22px]">{m.name}</h2>
            <span className="eyebrow">{t(`m.tier.${m.tier}` as Key)}</span>
          </div>
          <p className="mt-1.5 max-w-[520px] text-[13px] leading-relaxed text-ink-2">{m.description}</p>
        </div>
        <FitBadge fit={est.fit} />
      </div>

      <div className="mt-5 grid grid-cols-[auto_auto_auto_1fr] items-end gap-x-8">
        <div>
          <div className="eyebrow mb-1.5">Cuantización</div>
          <div className="flex gap-1">
            {Object.keys(m.quants).map((k) => (
              <button
                key={k}
                onClick={() => choose(k)}
                disabled={!!busyQuant && k !== quant}
                title={busyQuant && k !== quant ? t("m.lockedQuant") : undefined}
                className={`rounded-md px-2 py-1 text-[11.5px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-35 ${k === quant ? "bg-ink text-paper" : "text-ink-2 hover:bg-paper-2"}`}
              >
                {k.replace("_K_M", "").replace("_K", "").replace("_0", "")}
              </button>
            ))}
          </div>
        </div>
        <Metric label={t("m.size")} value={bytes(q.size)} />
        <Metric
          label={t("m.needs")}
          value={bytes(est.total)}
          title={`Pesos ${bytes(est.weights)} + contexto ${bytes(est.kv)} (${ctx.toLocaleString()} tokens)`}
        />
        <div className="justify-self-end">
          <div className="eyebrow mb-1.5 text-right">{t("m.languages")}</div>
          <div className="flex gap-3">
            {LANGS.map((l) => (
              <div key={l} className="flex flex-col items-center gap-1" title={t(`lang.${l}` as Key)}>
                <span className="text-[10.5px] uppercase text-ink-3">{l}</span>
                <Strength value={m.strengths[l]} />
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="mt-6 border-t border-rule pt-5">
        {file ? (
          <div className="flex items-center gap-4">
            <span className="inline-flex items-center gap-1.5 text-[12.5px] font-medium text-moss">✓ {t("m.installed")}</span>
            {bench?.gen_tps && (
              <span className="tabular text-[12.5px] text-ink-2">
                {t("m.measured", { n: bench.gen_tps })}
                {bench.est_hours_300_pages != null && <span className="text-ink-3"> · {t("m.book", { t: duration(bench.est_hours_300_pages * 3600) })}</span>}
              </span>
            )}
            <div className="ml-auto flex gap-1">
              <Button disabled={measuring} onClick={measure}>{measuring ? <span className="breathe">{t("m.measuring")}</span> : t("m.measure")}</Button>
              <Button variant="danger" onClick={() => remove(file.path)}>{t("m.delete")}</Button>
            </div>
          </div>
        ) : active ? (
          <div>
            <div className="tabular mb-2 flex items-baseline justify-between text-[12.5px]">
              <span className="text-ink-2">
                {dl.state === "verifying" ? t("m.verifying") : `${bytes(dl.downloaded)} / ${bytes(dl.total)} · ${speed(dl.bytes_per_sec)}`}
              </span>
              <span className="display text-[17px]">
                {dl.state === "downloading" && dl.eta_seconds != null ? t("m.left", { t: duration(dl.eta_seconds) }) : ""}
              </span>
            </div>
            <Bar value={progress} />
            <div className="mt-3 flex justify-end gap-1">
              <Button variant="quiet" onClick={() => native.pauseDownload(key)}>{t("m.pause")}</Button>
              <Button variant="danger" onClick={() => native.cancelDownload(key)}>{t("m.cancel")}</Button>
            </div>
          </div>
        ) : (
          <div className="flex items-center gap-4">
            {partial ? <Bar value={progress} active={false} className="max-w-[240px] flex-1" /> : null}
            {dl?.state === "error" && <span className="text-[12.5px] text-ribbon">{dl.error}</span>}
            <span className="text-[11.5px] text-ink-3">{m.license}</span>
            <Button variant="primary" className="ml-auto" disabled={est.fit === "no"} onClick={download}>
              {partial ? t("m.resume") : t("m.download")} · {bytes(q.size)}
            </Button>
          </div>
        )}
        {benchError && <div className="mt-3 text-[12.5px] text-ribbon">{benchError}</div>}
        {bench?.sample_output && (
          <details className="mt-4">
            <summary className="cursor-pointer text-[12px] text-ink-3">{t("m.sample")}</summary>
            <p className="reading mt-2 whitespace-pre-line text-ink-2">{bench.sample_output.replace(/<\/?seg[^>]*>/g, "")}</p>
          </details>
        )}
      </div>
    </article>
  );
}

function Metric({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div title={title}>
      <div className="eyebrow mb-1.5">{label}</div>
      <div className="tabular text-[15px] font-medium">{value}</div>
    </div>
  );
}
