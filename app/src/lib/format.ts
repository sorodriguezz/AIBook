import { t } from "./i18n";

export function bytes(n: number): string {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  const v = n / 1024 ** i;
  return `${v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)} ${u[i]}`;
}

export function speed(bps: number): string {
  return `${bytes(bps)}/s`;
}

/** "2 h 14 min", "6 min", "40 s" */
export function duration(seconds: number | null | undefined): string {
  if (seconds == null || !isFinite(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d} d ${h} h`;
  if (h) return `${h} h ${String(m).padStart(2, "0")} min`;
  if (m) return `${m} min`;
  return `${s} s`;
}

/** Clock-style elapsed time: 1:04:09 */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}

export function finishTime(etaSeconds: number | null | undefined): string | null {
  if (etaSeconds == null || !isFinite(etaSeconds)) return null;
  const d = new Date(Date.now() + etaSeconds * 1000);
  const sameDay = new Date().toDateString() === d.toDateString();
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return sameDay ? time : `${d.toLocaleDateString([], { weekday: "short" })} ${time}`;
}

export function basename(p: string): string {
  return p.split(/[\\/]/).pop() ?? p;
}

export function relative(ts: number): string {
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return t("time.now");
  if (diff < 3600) return t("time.minutes", { n: Math.floor(diff / 60) });
  if (diff < 86400) return t("time.hours", { n: Math.floor(diff / 3600) });
  return new Date(ts * 1000).toLocaleDateString();
}
