import { open } from "@tauri-apps/plugin-dialog";
import { Button, Field, Segmented } from "../components/ui";
import { t } from "../lib/i18n";
import { useApp } from "../lib/store";
import { translationRunning, useUpdates } from "../lib/updates";

const CONTEXTS = [8192, 16384, 32768];

export default function Settings() {
  const { settings, updateSettings } = useApp();
  const up = useUpdates();

  const chooseDir = async () => {
    const dir = await open({ directory: true });
    if (typeof dir === "string") updateSettings({ outputDir: dir });
  };

  return (
    <div className="mx-auto max-w-[640px] px-12 pb-20">
      <h1 className="display rise mb-10 mt-6 text-[44px] font-light leading-none">{t("s.title")}</h1>
      <div className="rise space-y-9" style={{ animationDelay: "60ms" }}>
        <Field label={t("s.library")} hint={t("s.library.hint")}>
          <div className="flex items-center gap-3">
            <div className="h-10 flex-1 truncate rounded-xl border border-rule bg-card px-3.5 leading-10 text-[13px] text-ink-2">
              {settings.libraryDir ?? "—"}
            </div>
            <Button onClick={async () => {
              const dir = await open({ directory: true });
              if (typeof dir === "string") updateSettings({ libraryDir: dir });
            }}>{t("s.output.choose")}</Button>
          </div>
          <label className="mt-3 flex cursor-pointer items-center gap-3 text-[13px] text-ink-2">
            <input type="checkbox" checked={settings.saveToLibrary} onChange={(e) => updateSettings({ saveToLibrary: e.target.checked })} className="h-4 w-4 accent-[var(--ribbon)]" />
            {t("s.saveToLibrary")}
          </label>
        </Field>

        {!settings.saveToLibrary && <Field label={t("s.output")}>
          <div className="flex items-center gap-3">
            <div className="h-10 flex-1 truncate rounded-xl border border-rule bg-card px-3.5 leading-10 text-[13px] text-ink-2">
              {settings.outputDir ?? t("s.output.same")}
            </div>
            <Button onClick={chooseDir}>{t("s.output.choose")}</Button>
            {settings.outputDir && <Button variant="quiet" onClick={() => updateSettings({ outputDir: null })}>×</Button>}
          </div>
        </Field>}

        <Field label={t("s.theme")}>
          <Segmented
            value={settings.theme}
            onChange={(theme) => updateSettings({ theme })}
            options={[
              { value: "system", label: t("s.theme.system") },
              { value: "light", label: t("s.theme.light") },
              { value: "dark", label: t("s.theme.dark") },
            ]}
          />
        </Field>

        <Field label={t("s.language")}>
          <Segmented
            value={settings.uiLang}
            onChange={(uiLang) => updateSettings({ uiLang })}
            options={[
              { value: "es", label: "Español" },
              { value: "en", label: "English" },
            ]}
          />
        </Field>

        <Field label={t("s.context")} hint={t("s.context.hint")}>
          <Segmented
            value={String(settings.ctxSize)}
            onChange={(v) => updateSettings({ ctxSize: Number(v) })}
            options={CONTEXTS.map((c) => ({ value: String(c), label: `${c / 1024}K tokens` }))}
          />
        </Field>

        <Field label={t("u.title")}>
          <div className="flex items-center gap-4 rounded-xl border border-rule bg-card px-4 py-3">
            <div className="flex-1 text-[13px]">
              <div>{up.current ? t("u.version", { v: up.current }) : "AIBook"}</div>
              <div className="mt-0.5 text-[12px] text-ink-3">
                {up.phase === "checking" && t("u.checking")}
                {up.phase === "none" && t("u.none")}
                {up.phase === "available" && up.update && t("u.available", { v: up.update.version })}
                {up.phase === "error" && up.error && t(up.error)}
              </div>
            </div>
            {up.phase === "available" ? (
              <Button variant="primary" disabled={translationRunning()} onClick={up.install}>{t("u.install")}</Button>
            ) : (
              <Button disabled={up.phase === "checking" || up.phase === "downloading"} onClick={() => up.checkNow()}>{t("u.check")}</Button>
            )}
          </div>
        </Field>
      </div>
    </div>
  );
}
