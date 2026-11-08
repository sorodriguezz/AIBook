import { useEffect, useMemo, useState } from "react";
import { Button, Cover, Field, FitBadge, LANGS, Segmented, Select } from "../components/ui";
import { engine, native, type BookSample, type BookSettings, type ModelSpec } from "../lib/api";
import { friendlyError } from "../lib/errors";
import { bestQuant, executionMemory, recommend } from "../lib/estimate";
import { glossaryFromJob, glossarySources, pickGlossaryFile } from "../lib/glossary";
import { basename, bytes, duration } from "../lib/format";
import { t, type Key } from "../lib/i18n";
import { installedModels, modelSpec, outputDir, prepareNotifications, useApp } from "../lib/store";
import type { Benchmark, BookInfo, BookType, GlossaryFile, LangCode, Quality } from "../lib/types";

/** Seconds per source token for each quality level, given measured speeds. */
export function secondsPerToken(b: Benchmark | null, quality: Quality): number | null {
  if (!b?.gen_tps) return null;
  const pp = b.prompt_tps || 300;
  const gen = { fast: 1.15, high: 1.15, best: 2.4 }[quality];
  const read = { fast: 2.2, high: 3.6, best: 6.0 }[quality];
  return gen / b.gen_tps + read / pp;
}

export default function Configure({ path }: { path: string }) {
  const { go, settings, updateSettings, catalog, hardware, installed, jobs } = useApp();
  const [info, setInfo] = useState<BookInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [src, setSrcRaw] = useState<LangCode>("en");
  const [tgt, setTgtRaw] = useState<LangCode>(settings.lastTarget as LangCode);
  // Picking the other side's language swaps the pair, so the two selects can
  // never hold the same value (a select can't display a value it doesn't list).
  const setSrc = (v: LangCode) => {
    if (v === tgt) setTgtRaw(src !== v ? src : v === "es" ? "en" : "es");
    setSrcRaw(v);
  };
  const setTgt = (v: LangCode) => {
    if (v === src) setSrcRaw(tgt !== v ? tgt : v === "es" ? "en" : "es");
    setTgtRaw(v);
  };
  const [quality, setQuality] = useState<Quality>(settings.quality);
  const [honorifics, setHonorifics] = useState<"adapt" | "keep">("adapt");
  const [review, setReview] = useState(false);
  const [bookType, setBookType] = useState<BookType>("literary");
  const [busy, setBusy] = useState(false);
  const [lowMemory, setLowMemory] = useState(false);
  const [context, setContext] = useState(settings.ctxSize);
  const [quant, setQuant] = useState<string>();
  const [threads, setThreads] = useState("");
  const [gpu, setGpu] = useState("");
  const [cache, setCache] = useState("");
  const [kvCache, setKvCache] = useState<ModelSpec["kv_cache"]>("f16");
  const [reviewScope, setReviewScope] = useState<"full" | "flagged">("full");
  const [sampling, setSampling] = useState(false);
  const [sample, setSample] = useState<{ key: string; value: BookSample } | null>(null);
  const [sampleError, setSampleError] = useState<string | null>(null);
  const [seed, setSeed] = useState<GlossaryFile | null>(null);
  const [seedError, setSeedError] = useState<string | null>(null);
  const seedSources = glossarySources(jobs, src, tgt);
  const queued = Object.values(jobs).some((j) => j.status === "running" || j.status === "queued");
  // A glossary only fits the language pair it was made for.
  useEffect(() => {
    if (seed && ((seed.src && seed.src !== src) || (seed.tgt && seed.tgt !== tgt))) setSeed(null);
  }, [src, tgt]);
  const chooseSeed = async (load: () => Promise<GlossaryFile | null>) => {
    setSeedError(null);
    try {
      const g = await load();
      if (!g) return;
      if ((g.src && g.src !== src) || (g.tgt && g.tgt !== tgt)) {
        setSeedError(t("err.glossary_pair", { src: t(`lang.${g.src ?? src}` as Key), tgt: t(`lang.${g.tgt ?? tgt}` as Key) }));
      } else if (!g.characters?.length && !g.terms?.length) {
        setSeedError(t("err.glossary_empty"));
      } else {
        setSeed(g);
      }
    } catch (e) {
      setSeedError(friendlyError(e));
    }
  };

  useEffect(() => {
    native.hardware().then((hardware) => useApp.setState({ hardware })).catch(() => {});
    engine
      .inspect(path)
      .then((i) => {
        setInfo(i);
        if (i.book_type) setBookType(i.book_type);
        if (i.detected_language) {
          setSrcRaw(i.detected_language);
          if (i.detected_language === tgt) setTgtRaw(i.detected_language === "es" ? "en" : "es");
        }
      })
      .catch((e) => setError(friendlyError(e)));
  }, [path]);

  const available = useMemo(() => installedModels(), [catalog, installed]);
  const usable = useMemo(() => available.map((m) => ({ ...m, quants: Object.fromEntries(
    Object.entries(m.quants).filter(([, q]) => installed.some((file) => file.file === q.file)),
  ) })), [available, installed]);
  const suggested = useMemo(
    () => (usable.length ? recommend(usable, src, tgt, context, hardware) : null),
    [usable, src, tgt, context, hardware],
  );
  const [modelId, setModelId] = useState<string | null>(null);
  const chosen = modelId ?? suggested?.id ?? null;
  const installedChoice = usable.find((m) => m.id === chosen);
  const preferredQuant = quant ?? (chosen && settings.chosenQuant[chosen]) ?? undefined;
  const spec = chosen && installedChoice ? modelSpec(chosen, { ctx: context,
    quant: preferredQuant || bestQuant(installedChoice, context, hardware) }) : null;
  const model: ModelSpec | null = spec && {
    ...spec.spec,
    gpu_layers: gpu === "" ? spec.spec.gpu_layers : Number(gpu),
    threads: threads === "" ? undefined : Number(threads),
    cache_ram_mb: cache === "" ? (lowMemory ? 128 : undefined) : Number(cache),
    batch_size: lowMemory ? 256 : undefined,
    micro_batch_size: lowMemory ? 64 : undefined,
    kv_cache: kvCache === "f16" ? undefined : kvCache,
  };
  const memory = spec && model ? executionMemory(spec.model, spec.quant, model, hardware) : null;
  const fit = memory?.fit;
  const request: BookSettings | null = model && { path, src, tgt, quality, model, honorifics,
    glossary_seed: seed, book_type: bookType, low_memory: lowMemory, review_scope: reviewScope };
  const sampleKey = JSON.stringify(request);
  const currentSample = sample?.key === sampleKey ? sample.value : null;

  const matchingBenchmark = lowMemory || gpu || threads || cache || model?.kv_cache ? null : spec?.benchmark ?? null;
  const spt = secondsPerToken(matchingBenchmark, quality);
  const eta = spt && info?.tokens ? spt * info.tokens : null;
  const sampleEta = currentSample && info?.tokens && currentSample.source_tokens > 0
    ? currentSample.seconds * info.tokens / currentSample.source_tokens : null;

  const refreshMemory = async () => {
    const current = await native.hardware();
    useApp.setState({ hardware: current });
    return current;
  };

  const preview = async () => {
    if (!request || !spec) return;
    setSampling(true);
    setSampleError(null);
    try {
      const current = await refreshMemory();
      if (executionMemory(spec.model, spec.quant, request.model, current).fit === "no") {
        setSampleError(t("cfg.memoryNo"));
        return;
      }
      setSample({ key: sampleKey, value: await engine.sample(request) });
    } catch (e) {
      setSampleError(friendlyError(e));
    } finally {
      setSampling(false);
    }
  };

  const start = async () => {
    if (!spec || !info || !request) return;
    setBusy(true);
    setError(null);
    try {
      const current = await refreshMemory();
      if (executionMemory(spec.model, spec.quant, request.model, current).fit === "no") {
        setError(t("cfg.memoryNo"));
        return;
      }
      await updateSettings({ lastTarget: tgt, quality });
      prepareNotifications();
      const job = await engine.createJob({
        ...request,
        model_id: spec.model.id,
        review_glossary: review && quality !== "fast",
        output_dir: outputDir(),
        benchmark: matchingBenchmark,
      });
      useApp.setState((s) => ({ jobs: { ...s.jobs, [job.id]: job } }));
      go({ name: "job", id: job.id });
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setBusy(false);
    }
  };

  const jaInvolved = src === "ja" || tgt === "ja";

  return (
    <div className="mx-auto max-w-[880px] px-12 pb-16">
      <Button variant="quiet" className="-ml-2 mb-6 mt-2" onClick={() => go({ name: "translate" })}>
        ← {t("cfg.cancel")}
      </Button>

      <div className="grid grid-cols-[180px_1fr] gap-12">
        <div className="rise">
          <Cover src={info?.cover} title={info?.title ?? basename(path)} className="aspect-[2/3] w-[180px]" />
          {info && (
            <div className="mt-4 space-y-1 text-[12px] text-ink-3">
              <div>{info.format.toUpperCase()}</div>
              {info.pages != null && <div>{t("cfg.pages", { n: info.pages })}</div>}
              {info.chapters != null && <div>{t("cfg.chapters", { n: info.chapters })}</div>}
            </div>
          )}
        </div>

        <div className="rise min-w-0" style={{ animationDelay: "60ms" }}>
          <h1 className="display text-[34px] font-light leading-tight">
            {info?.title ?? (error ? basename(path) : <span className="breathe text-ink-3">{t("home.reading")}</span>)}
          </h1>
          {error && <div className="mt-4 rounded-xl bg-ribbon-soft px-4 py-3 text-[13px]">{error}</div>}
          {info?.scanned_pages && info.scanned_pages.length > 0 && (
            <div className="mt-3 text-[12.5px] text-amber">{t("cfg.scanned", { n: info.scanned_pages.length })}</div>
          )}

          <div className="mt-9 grid grid-cols-[1fr_auto_1fr] items-end gap-3">
            <Field label={t("cfg.from")}>
              <Select value={src} onChange={(v) => setSrc(v as LangCode)}>
                {LANGS.map((l) => (
                  <option key={l} value={l}>{t(`lang.${l}` as Key)}</option>
                ))}
              </Select>
            </Field>
            <button
              onClick={() => { setSrcRaw(tgt); setTgtRaw(src); }}
              className="mb-2 flex h-7 w-7 items-center justify-center rounded-full text-ink-3 hover:bg-paper-2 hover:text-ink"
              title="⇄"
            >
              ⇄
            </button>
            <Field label={t("cfg.to")}>
              <Select value={tgt} onChange={(v) => setTgt(v as LangCode)}>
                {LANGS.map((l) => (
                  <option key={l} value={l}>{t(`lang.${l}` as Key)}</option>
                ))}
              </Select>
            </Field>
          </div>

          {info?.detected_language && (
            <div className={`mt-2.5 text-[12.5px] ${src !== info.detected_language ? "text-amber" : "text-ink-3"}`}>
              {src !== info.detected_language
                ? t("cfg.detectedMismatch", { l: t(`lang.${info.detected_language}` as Key) })
                : t("cfg.detected", { l: t(`lang.${info.detected_language}` as Key) })}
            </div>
          )}

          <div className="mt-7">
            <Field label={t("cfg.model")}>
              {available.length === 0 ? (
                <div className="flex items-center justify-between rounded-xl border border-dashed border-rule px-4 py-3">
                  <span className="text-[13px] text-ink-2">{t("cfg.noModels")}</span>
                  <Button onClick={() => go({ name: "models" })}>{t("cfg.goModels")}</Button>
                </div>
              ) : (
                <div className="flex items-center gap-4">
                  <Select value={chosen ?? ""} onChange={(id) => { setModelId(id); setQuant(undefined); setGpu(""); }} className="flex-1">
                    {available.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name}
                        {m.id === suggested?.id ? `  ·  ${t("cfg.recommended")}` : ""}
                      </option>
                    ))}
                  </Select>
                  {fit && <FitBadge fit={fit} />}
                </div>
              )}
            </Field>
          </div>

          {spec && memory && (
            <section className="mt-7 rounded-2xl border border-rule bg-card/70 p-4">
              <Field label={t("cfg.resources")}>
                <Segmented value={lowMemory ? "low" : "auto"} onChange={(value) => setLowMemory(value === "low")}
                  options={[{ value: "auto", label: t("cfg.memoryAuto") }, { value: "low", label: t("cfg.memoryLow") }]} />
              </Field>
              {lowMemory && <p className="mt-3 text-[12px] text-ink-2">{t("cfg.memoryLowHint")}</p>}
              <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[12px] text-ink-2">
                <span>{t("cfg.memoryEstimate", { ram: bytes(memory.ram), available: memory.available === null ? "—" : bytes(memory.available) })}
                  {!!memory.vram && ` · ${t("cfg.vramEstimate", { vram: bytes(memory.vram) })}`}</span>
                <Button variant="quiet" onClick={() => void refreshMemory().catch((e) => setError(friendlyError(e)))}>{t("cfg.refreshMemory")}</Button>
              </div>
              {fit === "no" && <p className="text-[12px] text-ribbon">{t("cfg.memoryNo")}</p>}
              <details className="mt-3 border-t border-rule pt-3">
                <summary className="cursor-pointer text-[13px] font-medium">{t("cfg.advanced")}</summary>
                <div className="mt-4 grid gap-4 sm:grid-cols-2">
                  <Field label={t("cfg.quant")}><Select value={spec.quant} onChange={setQuant}>
                    {Object.keys(usable.find((m) => m.id === chosen)?.quants ?? {}).map((q) => <option key={q}>{q}</option>)}
                  </Select></Field>
                  <Field label={t("s.context")}><Select value={String(context)} onChange={(v) => setContext(Number(v))}>
                    {[...new Set([4096, 8192, 16384, 32768, 65536, settings.ctxSize])].sort((a, b) => a - b).map((n) =>
                      <option key={n} value={n}>{n / 1024}K</option>)}
                  </Select></Field>
                  <Field label={t("cfg.threads")}><Select value={threads} onChange={setThreads}>
                    <option value="">{t("cfg.memoryAuto")}</option>
                    {[1, 2, 4, 8, 16, 32].filter((n) => !hardware || n <= hardware.cores).map((n) => <option key={n}>{n}</option>)}
                  </Select></Field>
                  {!!hardware?.gpu_budget_bytes && <Field label={t("cfg.gpuLayers")}><Select value={gpu} onChange={setGpu}>
                    <option value="">{t("cfg.memoryAuto")}</option><option value="0">0 · CPU</option>
                    {[...new Set([8, 16, 32, spec.model.geometry.layers].filter((n) => n <= spec.model.geometry.layers))].map((n) => <option key={n}>{n}</option>)}
                  </Select></Field>}
                  <Field label={t("cfg.cacheLimit")}><Select value={cache} onChange={setCache}>
                    <option value="">{t("cfg.memoryAuto")}</option>
                    {[0, 128, 512, 1024, 2048].map((n) => <option key={n} value={n}>{n} MiB</option>)}
                  </Select></Field>
                  <Field label={t("cfg.kvCache")}><Select value={kvCache ?? "f16"} onChange={(v) => setKvCache(v as ModelSpec["kv_cache"])}>
                    {(["f16", "q8_0", "q4_0"] as const).map((v) => <option key={v}>{v}</option>)}
                  </Select></Field>
                  {quality === "best" && (info?.format === "epub" || !!info?.scanned_pages?.length) &&
                    <Field label={t("cfg.reviewScope")}><Select value={reviewScope} onChange={(v) => setReviewScope(v as typeof reviewScope)}>
                      <option value="full">{t("cfg.reviewFull")}</option><option value="flagged">{t("cfg.reviewFlagged")}</option>
                    </Select></Field>}
                </div>
                <p className="mt-3 text-[12px] text-ink-3">{t("cfg.advancedHint")}</p>
              </details>
            </section>
          )}

          <div className="mt-7">
            <Field label={t("cfg.bookType")}>
              <Segmented<BookType>
                value={bookType}
                onChange={setBookType}
                options={(["literary", "technical"] as BookType[]).map((b) => ({
                  value: b,
                  label: t(`cfg.bookType.${b}` as Key),
                  hint: t(`cfg.bookType.${b}.hint` as Key),
                }))}
              />
            </Field>
            {info?.book_type && (
              <div className={`mt-2.5 text-[12.5px] ${bookType !== info.book_type ? "text-amber" : "text-ink-3"}`}>
                {t("cfg.bookType.detected", { t: t(`cfg.bookType.${info.book_type}` as Key).toLowerCase() })}
              </div>
            )}
          </div>

          <div className="mt-7">
            <Field label={t("cfg.quality")}>
              <Segmented<Quality>
                value={quality}
                onChange={setQuality}
                options={(["fast", "high", "best"] as Quality[]).map((q) => ({
                  value: q,
                  label: t(`cfg.quality.${q}` as Key),
                  hint: t(`cfg.quality.${q}.hint` as Key),
                }))}
              />
            </Field>
          </div>

          {jaInvolved && (
            <div className="mt-7">
              <Field label={t("cfg.honorifics")}>
                <Segmented
                  value={honorifics}
                  onChange={setHonorifics}
                  options={[
                    { value: "adapt", label: t("cfg.honorifics.adapt") },
                    { value: "keep", label: t("cfg.honorifics.keep") },
                  ]}
                />
              </Field>
            </div>
          )}

          <div className="mt-7">
            <Field label={t("cfg.glossary")} hint={t("cfg.glossary.hint")}>
              {seed ? (
                <div className="flex items-center gap-3 rounded-xl border border-rule bg-card px-3.5 py-2.5 text-[13px]">
                  <span className="min-w-0 flex-1 truncate">{seed.title ?? t("gl.fileName")}</span>
                  <span className="text-[12px] text-ink-3">
                    {t("cfg.glossary.entries", { n: (seed.characters?.length ?? 0) + (seed.terms?.length ?? 0) })}
                  </span>
                  <button className="text-ink-3 hover:text-ribbon" onClick={() => setSeed(null)}>×</button>
                </div>
              ) : (
                <div className="flex flex-wrap items-center gap-2">
                  <Button onClick={() => chooseSeed(pickGlossaryFile)}>{t("cfg.glossary.file")}</Button>
                  {seedSources.length > 0 && (
                    <Select value="" onChange={(id) => id && chooseSeed(() => glossaryFromJob(jobs[id]))} className="min-w-[220px] flex-1">
                      <option value="">{t("cfg.glossary.pick")}</option>
                      {seedSources.map((j) => (
                        <option key={j.id} value={j.id}>{j.title}</option>
                      ))}
                    </Select>
                  )}
                </div>
              )}
              {seedError && <div className="mt-2 text-[12.5px] text-ribbon">{seedError}</div>}
            </Field>
          </div>

          {quality !== "fast" && (
            <label className="mt-6 flex cursor-pointer items-center gap-3 text-[13px] text-ink-2">
              <input type="checkbox" checked={review} onChange={(e) => setReview(e.target.checked)} className="h-4 w-4 accent-[var(--ribbon)]" />
              {t("cfg.review")}
            </label>
          )}

          {spec && info && <section className="mt-7 border-t border-rule pt-5">
            <Button disabled={sampling || queued || busy || fit === "no"} onClick={() => void preview()}>
              {t(sampling ? "cfg.sampling" : "cfg.sample")}
            </Button>
            <p className="mt-2 text-[12px] text-ink-3">{t("cfg.sampleHint")}</p>
            {sampleError && <p role="alert" className="mt-2 text-[13px] text-ribbon">{sampleError}</p>}
            {currentSample && <div className="mt-4 space-y-3" aria-live="polite">
              <p className="text-[12px] text-ink-2">{t("cfg.sampleMeasured", { t: duration(currentSample.seconds) })}
                {currentSample.kind !== "epub" && currentSample.items[0] && ` · ${t("job.page", { n: currentSample.items[0].chapter + 1 })}`}
              </p>
              {currentSample.items.map((item, i) => <div key={i} className="grid gap-3 rounded-xl border border-rule bg-card p-4 sm:grid-cols-2">
                <div><div className="eyebrow mb-2">{t("job.original")}</div><p className="reading break-words">{item.source}</p></div>
                <div><div className="eyebrow mb-2">{t("job.translation")}</div><p className="reading break-words">{item.target}</p>
                  {item.flagged && <p className="mt-2 text-[12px] text-ribbon">{t("cfg.sampleFlagged")}</p>}</div>
              </div>)}
            </div>}
          </section>}

          <div className="mt-10 flex items-center justify-between border-t border-rule pt-6">
            <div className="tabular text-[13px] text-ink-3">
              {sampleEta ? t("cfg.sampleEta", { a: duration(sampleEta * 0.7), b: duration(sampleEta * 1.5) })
                : eta ? t("cfg.estimate", { t: duration(eta) }) : ""}
              {queued && <div className="mt-0.5">{t("cfg.queueHint")}</div>}
            </div>
            <Button variant="primary" disabled={!spec || !info || busy || sampling || fit === "no" || src === tgt} onClick={start}>
              {queued ? t("cfg.startQueued") : t("cfg.start")} →
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
