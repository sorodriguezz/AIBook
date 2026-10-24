import { useEffect, useMemo, useState } from "react";
import { Button, Cover, Field, FitBadge, LANGS, Segmented, Select } from "../components/ui";
import { engine } from "../lib/api";
import { friendlyError } from "../lib/errors";
import { estimate, recommend } from "../lib/estimate";
import { basename, duration } from "../lib/format";
import { t, type Key } from "../lib/i18n";
import { installedModels, modelSpec, outputDir, useApp } from "../lib/store";
import type { Benchmark, BookInfo, LangCode, Quality } from "../lib/types";

/** Seconds per source token for each quality level, given measured speeds. */
export function secondsPerToken(b: Benchmark | null, quality: Quality): number | null {
  if (!b?.gen_tps) return null;
  const pp = b.prompt_tps || 300;
  const gen = { fast: 1.15, high: 1.15, best: 2.4 }[quality];
  const read = { fast: 2.2, high: 3.6, best: 6.0 }[quality];
  return gen / b.gen_tps + read / pp;
}

export default function Configure({ path }: { path: string }) {
  const { go, settings, updateSettings, catalog, hardware, installed } = useApp();
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
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    engine
      .inspect(path)
      .then((i) => {
        setInfo(i);
        if (i.detected_language) {
          setSrcRaw(i.detected_language);
          if (i.detected_language === tgt) setTgtRaw(i.detected_language === "es" ? "en" : "es");
        }
      })
      .catch((e) => setError(friendlyError(e)));
  }, [path]);

  const available = useMemo(() => installedModels(), [catalog, installed]);
  const suggested = useMemo(
    () => (available.length ? recommend(available, src, tgt, settings.ctxSize, hardware) : null),
    [available, src, tgt, settings.ctxSize, hardware],
  );
  const [modelId, setModelId] = useState<string | null>(null);
  const chosen = modelId ?? suggested?.id ?? null;
  const spec = chosen ? modelSpec(chosen) : null;
  const fit = spec ? estimate(spec.model, spec.quant, settings.ctxSize, hardware).fit : null;

  const spt = secondsPerToken(spec?.benchmark ?? null, quality);
  const eta = spt && info?.tokens ? spt * info.tokens : null;

  const start = async () => {
    if (!spec || !info) return;
    setBusy(true);
    try {
      await updateSettings({ lastTarget: tgt, quality });
      const job = await engine.createJob({
        path,
        src,
        tgt,
        quality,
        model_id: spec.model.id,
        model: spec.spec,
        honorifics,
        review_glossary: review && quality !== "fast",
        output_dir: outputDir(),
        benchmark: spec.benchmark,
      });
      useApp.setState((s) => ({ jobs: { ...s.jobs, [job.id]: job } }));
      go({ name: "job", id: job.id });
    } catch (e: any) {
      setError(friendlyError(e));
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
                  <Select value={chosen ?? ""} onChange={setModelId} className="flex-1">
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

          {quality !== "fast" && (
            <label className="mt-6 flex cursor-pointer items-center gap-3 text-[13px] text-ink-2">
              <input type="checkbox" checked={review} onChange={(e) => setReview(e.target.checked)} className="h-4 w-4 accent-[var(--ribbon)]" />
              {t("cfg.review")}
            </label>
          )}

          <div className="mt-10 flex items-center justify-between border-t border-rule pt-6">
            <div className="tabular text-[13px] text-ink-3">{eta ? t("cfg.estimate", { t: duration(eta) }) : ""}</div>
            <Button variant="primary" disabled={!spec || !info || busy || fit === "no" || src === tgt} onClick={start}>
              {t("cfg.start")} →
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
