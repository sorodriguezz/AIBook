import { EngineError } from "./api";
import { t, type Key } from "./i18n";

/** A message the user can act on, from an engine error code when there is one. */
export function friendlyError(e: unknown, code?: string | null, params: Record<string, string> = {}): string {
  if (e instanceof EngineError) {
    code = code ?? e.code;
    params = { ...e.params, ...params };
    if (e.status === 0) code = code ?? "engine_down";
  }
  const raw = (e instanceof Error ? e.message : String(e ?? "")).split("\n")[0].slice(0, 200);
  if (code) {
    const vars: Record<string, string> = { detail: raw || t("err.unknown"), ...params };
    if (params.lang) vars.lang = t(`lang.${params.lang}` as Key);
    const key = `err.${code}` as Key;
    const msg = t(key, vars);
    if (msg !== key) return msg;
  }
  return raw ? t("err.internal", { detail: raw }) : t("err.unknown");
}
