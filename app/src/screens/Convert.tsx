import { getCurrentWebview } from "@tauri-apps/api/webview";
import { open } from "@tauri-apps/plugin-dialog";
import { openPath, revealItemInDir } from "@tauri-apps/plugin-opener";
import { useEffect, useState } from "react";
import { Bar, Button, Field, Segmented } from "../components/ui";
import { engine, type Conversion } from "../lib/api";
import { friendlyError } from "../lib/errors";
import { basename, duration, relative } from "../lib/format";
import { t, type Key } from "../lib/i18n";
import { outputDir, useApp } from "../lib/store";

const ACCEPT = /\.(pdf|epub)$/i;
type Page = "a5" | "6x9" | "a4" | "letter";
type Mode = "reflow" | "fixed";

export default function Convert({ initialPath }: { initialPath?: string }) {
  const { conversions } = useApp();
  const [path, setPath] = useState<string | null>(initialPath ?? null);
  const [page, setPage] = useState<Page>("a5");
  const [fontSize, setFontSize] = useState("11");
  const [mode, setMode] = useState<Mode>("reflow");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
          if (file) setPath(file);
        }
      })
      .then((u) => (unlisten = u));
    return () => unlisten?.();
  }, []);

  const choose = async () => {
    const file = await open({ multiple: false, filters: [{ name: "Libros", extensions: ["pdf", "epub"] }] });
    if (typeof file === "string") setPath(file);
  };

  const from = path?.toLowerCase().endsWith(".pdf") ? "pdf" : path ? "epub" : null;
  const target = from === "pdf" ? "epub" : "pdf";

  const start = async () => {
    if (!path) return;
    setBusy(true);
    setError(null);
    try {
      const options = target === "pdf" ? { page, font_size: Number(fontSize), margin_mm: 16 } : { mode };
      const c = await engine.convert({ path, target, options, out_dir: outputDir() });
      useApp.setState((s) => ({ conversions: { ...s.conversions, [c.id]: c } }));
      setPath(null);
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setBusy(false);
    }
  };

  const list = Object.values(conversions).sort((a, b) => b.created_at - a.created_at);

  return (
    <div className="mx-auto max-w-[880px] px-12 pb-16">
      <header className="rise mb-8 mt-6">
        <h1 className="display text-[44px] font-light leading-[1.05]">{t("cv.title")}</h1>
        <p className="mt-3 max-w-[480px] text-[15px] text-ink-2">{t("cv.subtitle")}</p>
      </header>

      {!path ? (
        <button
          onClick={choose}
          className={`rise group block w-full rounded-[22px] border border-dashed px-10 py-12 text-left transition-all duration-300 ${
            hover ? "border-ribbon bg-ribbon-soft" : "border-rule hover:border-ink-3 hover:bg-card/60"
          }`}
        >
          <div className="flex items-center gap-8">
            <FormatGlyphs />
            <div>
              <div className="display text-[24px]">{t("cv.drop")}</div>
              <div className="mt-1 text-[13px] text-ink-3">{t("cv.dropHint")}</div>
              <div className="mt-5 inline-flex h-9 items-center rounded-full bg-ink px-4 text-[13px] font-medium text-paper transition-colors group-hover:bg-ribbon">
                {t("home.choose")}
              </div>
            </div>
          </div>
        </button>
      ) : (
        <section className="rise rounded-[22px] border border-rule bg-card p-7">
          <div className="flex items-center gap-4">
            <span className="rounded-md bg-paper-2 px-2 py-1 text-[11px] font-semibold tracking-wide">{from?.toUpperCase()}</span>
            <svg width="22" height="10" viewBox="0 0 22 10" className="text-ribbon">
              <path d="M0 5h19M15 1l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.5" />
            </svg>
            <span className="rounded-md bg-ink px-2 py-1 text-[11px] font-semibold tracking-wide text-paper">{target.toUpperCase()}</span>
            <span className="display ml-2 min-w-0 flex-1 truncate text-[19px]">{basename(path)}</span>
            <Button variant="quiet" onClick={() => setPath(null)}>{t("cfg.cancel")}</Button>
          </div>

          <div className="mt-7 space-y-6">
            {target === "pdf" ? (
              <>
                <Field label={t("cv.pageSize")}>
                  <Segmented<Page>
                    value={page}
                    onChange={setPage}
                    options={[
                      { value: "a5", label: "A5", hint: t("cv.page.a5") },
                      { value: "6x9", label: "6 × 9 in", hint: t("cv.page.6x9") },
                      { value: "a4", label: "A4", hint: t("cv.page.a4") },
                      { value: "letter", label: t("cv.letter"), hint: "8,5 × 11 in" },
                    ]}
                  />
                </Field>
                <Field label={t("cv.fontSize")}>
                  <Segmented
                    value={fontSize}
                    onChange={setFontSize}
                    options={["10", "11", "12", "13"].map((v) => ({ value: v, label: `${v} pt` }))}
                  />
                </Field>
              </>
            ) : (
              <Field label={t("cv.mode")}>
                <Segmented<Mode>
                  value={mode}
                  onChange={setMode}
                  options={[
                    { value: "reflow", label: t("cv.mode.reflow"), hint: t("cv.mode.reflow.hint") },
                    { value: "fixed", label: t("cv.mode.fixed"), hint: t("cv.mode.fixed.hint") },
                  ]}
                />
              </Field>
            )}
          </div>

          {error && <div className="mt-6 rounded-xl bg-ribbon-soft px-4 py-3 text-[13px]">{error}</div>}
          <div className="mt-8 flex justify-end border-t border-rule pt-6">
            <Button variant="primary" disabled={busy} onClick={start}>{t("cv.start", { f: target.toUpperCase() })} →</Button>
          </div>
        </section>
      )}

      <section className="mt-14">
        <div className="eyebrow mb-4">{t("cv.recent")}</div>
        {list.length === 0 ? (
          <p className="text-[13px] text-ink-3">{t("cv.empty")}</p>
        ) : (
          <ul className="divide-y divide-rule border-y border-rule">
            {list.map((c) => <Row key={c.id} c={c} />)}
          </ul>
        )}
      </section>
    </div>
  );
}

function Row({ c }: { c: Conversion }) {
  const running = c.status === "running" || c.status === "queued";
  const remove = async () => {
    await engine.removeConversion(c.id);
    useApp.setState((s) => {
      const next = { ...s.conversions };
      delete next[c.id];
      return { conversions: next };
    });
  };
  return (
    <li className="flex items-center gap-5 py-4">
      <span className="w-11 shrink-0 rounded-md bg-paper-2 py-1 text-center text-[10.5px] font-semibold tracking-wide">
        {c.target.toUpperCase()}
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[14px]">{basename(c.output_path)}</div>
        <div className="mt-0.5 text-[12px] text-ink-3">
          {c.status === "failed"
            ? friendlyError(new Error(c.error ?? ""), c.error_code)
            : c.status === "running"
              ? c.eta_seconds != null ? t("m.left", { t: duration(c.eta_seconds) }) : t("job.calculating")
              : `${t(`status.${c.status}` as Key)} · ${relative(c.created_at)}`}
        </div>
        {running && <Bar value={c.progress} className="mt-2.5 max-w-[360px]" />}
      </div>
      <div className="flex shrink-0 gap-1">
        {c.status === "done" && (
          <>
            <Button onClick={() => openPath(c.output_path)}>{t("job.open")}</Button>
            <Button variant="quiet" onClick={() => revealItemInDir(c.output_path)}>{t("job.reveal")}</Button>
          </>
        )}
        {running ? (
          <Button variant="danger" onClick={() => engine.cancelConversion(c.id)}>{t("job.cancel")}</Button>
        ) : (
          <Button variant="danger" onClick={remove}>×</Button>
        )}
      </div>
    </li>
  );
}

/** Two little documents, a page and an e-reader, with a ribbon arrow between them. */
function FormatGlyphs() {
  return (
    <svg width="120" height="92" viewBox="0 0 120 92" className="shrink-0">
      <rect x="4" y="8" width="44" height="60" rx="3" fill="var(--card)" stroke="var(--rule)" />
      {[18, 26, 34, 42, 50].map((y, i) => (
        <rect key={y} x="11" y={y} width={i % 2 ? 24 : 30} height="3" rx="1.5" fill="var(--rule)" />
      ))}
      <text x="26" y="82" textAnchor="middle" fontSize="9" fontWeight="600" fill="var(--ink-3)" letterSpacing="1">PDF</text>
      <path d="M54 38h12M62 33l5 5-5 5" fill="none" stroke="var(--ribbon)" strokeWidth="1.8" strokeLinecap="round" />
      <rect x="72" y="4" width="44" height="66" rx="7" fill="var(--ink)" />
      <rect x="77" y="10" width="34" height="50" rx="2" fill="var(--card)" />
      {[17, 24, 31, 38, 45].map((y, i) => (
        <rect key={y} x="81" y={y} width={i % 2 ? 20 : 26} height="2.5" rx="1.25" fill="var(--rule)" />
      ))}
      <text x="94" y="82" textAnchor="middle" fontSize="9" fontWeight="600" fill="var(--ink-3)" letterSpacing="1">EPUB</text>
    </svg>
  );
}
