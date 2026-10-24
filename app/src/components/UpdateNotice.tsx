import { useEffect } from "react";
import { bytes, duration } from "../lib/format";
import { t } from "../lib/i18n";
import { translationRunning, useUpdates } from "../lib/updates";
import { Bar } from "./ui";

/** Small card at the bottom of the sidebar when a new version is out. */
export default function UpdateNotice() {
  const { phase, update, downloaded, total, etaSeconds, checkNow, install } = useUpdates();

  useEffect(() => {
    const first = setTimeout(() => checkNow(true), 4000);
    const every = setInterval(() => checkNow(true), 6 * 3600 * 1000);
    return () => {
      clearTimeout(first);
      clearInterval(every);
    };
  }, [checkNow]);

  if (!update || !["available", "downloading", "installing"].includes(phase)) return null;
  const busy = translationRunning();

  return (
    <div className="rise mb-4 rounded-xl border border-rule bg-card p-3">
      <div className="text-[12px] font-medium">{t("u.available", { v: update.version })}</div>
      {phase === "available" && (
        <>
          {busy && <div className="mt-1 text-[11px] leading-snug text-ink-3">{t("u.busy")}</div>}
          <button
            disabled={busy}
            onClick={install}
            className="mt-2 h-7 w-full rounded-full bg-ink text-[12px] font-medium text-paper transition-colors hover:bg-ribbon disabled:opacity-40"
          >
            {t("u.install")}
          </button>
        </>
      )}
      {phase === "downloading" && (
        <div className="mt-2">
          <Bar value={total ? downloaded / total : 0} />
          <div className="tabular mt-1.5 flex justify-between text-[11px] text-ink-3">
            <span>{bytes(downloaded)}{total ? ` / ${bytes(total)}` : ""}</span>
            <span>{etaSeconds != null ? duration(etaSeconds) : ""}</span>
          </div>
        </div>
      )}
      {phase === "installing" && <div className="breathe mt-1 text-[11px] text-ink-3">{t("u.installing")}</div>}
    </div>
  );
}
