import type { ButtonHTMLAttributes, ReactNode } from "react";
import type { Fit } from "../lib/estimate";
import { t, type Key } from "../lib/i18n";
import type { LangCode } from "../lib/types";

export const LANGS: LangCode[] = ["en", "es", "fr", "de", "it", "ja", "zh"];

type Variant = "primary" | "ghost" | "quiet" | "danger";

export function Button({
  variant = "ghost",
  className = "",
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  const styles: Record<Variant, string> = {
    primary:
      "bg-ink text-paper hover:bg-ribbon disabled:bg-ink-3 disabled:hover:bg-ink-3 px-5 h-10 shadow-[0_1px_0_rgba(0,0,0,.15)]",
    ghost: "border border-rule hover:border-ink-3 hover:bg-card px-4 h-9",
    quiet: "text-ink-2 hover:text-ink px-2 h-8",
    danger: "text-ink-3 hover:text-ribbon px-2 h-8",
  };
  return (
    <button
      className={`inline-flex items-center justify-center gap-2 rounded-full text-[13px] font-medium transition-colors duration-200 disabled:cursor-not-allowed disabled:opacity-60 ${styles[variant]} ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { value: T; label: string; hint?: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="grid gap-1 rounded-2xl bg-paper-2 p-1" style={{ gridTemplateColumns: `repeat(${options.length}, 1fr)` }}>
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            onClick={() => onChange(o.value)}
            className={`rounded-xl px-3 py-2.5 text-left transition-all duration-200 ${
              active ? "bg-card shadow-[0_1px_2px_rgba(0,0,0,.08)]" : "hover:bg-card/50"
            }`}
          >
            <div className={`text-[13px] font-medium ${active ? "text-ink" : "text-ink-2"}`}>{o.label}</div>
            {o.hint && <div className="mt-0.5 text-[11.5px] leading-snug text-ink-3">{o.hint}</div>}
          </button>
        );
      })}
    </div>
  );
}

export function Select({
  value,
  onChange,
  children,
  className = "",
}: {
  value: string;
  onChange: (v: string) => void;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`relative ${className}`}>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-10 w-full appearance-none rounded-xl border border-rule bg-card pl-3.5 pr-9 text-[13.5px] outline-none transition-colors hover:border-ink-3 focus:border-ink-2"
      >
        {children}
      </select>
      <svg className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-ink-3" width="12" height="12" viewBox="0 0 12 12">
        <path d="M3 4.5 6 7.5 9 4.5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      </svg>
    </div>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <div className="eyebrow mb-2">{label}</div>
      {children}
      {hint && <div className="mt-1.5 text-[12px] text-ink-3">{hint}</div>}
    </label>
  );
}

export function FitBadge({ fit }: { fit: Fit }) {
  const color = { optimal: "text-moss", works: "text-ink-2", slow: "text-amber", no: "text-ribbon" }[fit];
  const dots = { optimal: 3, works: 2, slow: 1, no: 0 }[fit];
  return (
    <span className={`inline-flex items-center gap-1.5 text-[12px] font-medium ${color}`}>
      <span className="flex gap-[3px]">
        {[0, 1, 2].map((i) => (
          <span key={i} className={`h-[7px] w-[3px] rounded-full ${i < dots ? "bg-current" : "bg-rule"}`} />
        ))}
      </span>
      {t(`m.fit.${fit}` as Key)}
    </span>
  );
}

/** A slim progress rule with a ribbon-coloured fill. */
export function Bar({ value, active = true, className = "" }: { value: number; active?: boolean; className?: string }) {
  return (
    <div className={`relative h-[3px] overflow-hidden rounded-full bg-rule ${className}`}>
      <div
        className={`absolute inset-y-0 left-0 rounded-full transition-[width] duration-700 ease-out ${active ? "bg-ribbon" : "bg-ink-3"}`}
        style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
      />
      {active && value < 1 && <div className="shimmer absolute inset-0" />}
    </div>
  );
}

export function LangPair({ src, tgt }: { src: string; tgt: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[12px] text-ink-2">
      {t(`lang.${src}` as Key)}
      <svg width="14" height="8" viewBox="0 0 14 8" className="text-ink-3">
        <path d="M0 4h12M9 1l3 3-3 3" fill="none" stroke="currentColor" strokeWidth="1.2" />
      </svg>
      {t(`lang.${tgt}` as Key)}
    </span>
  );
}

export function Cover({ src, title, className = "" }: { src?: string | null; title?: string | null; className?: string }) {
  return (
    <div
      className={`relative overflow-hidden rounded-[3px] bg-paper-2 shadow-[0_1px_1px_rgba(0,0,0,.08),0_8px_24px_-12px_rgba(40,25,10,.45)] ${className}`}
    >
      {src ? (
        <img src={src} alt="" className="h-full w-full object-cover" draggable={false} />
      ) : (
        <div className="flex h-full w-full items-end p-2">
          <span className="display line-clamp-3 text-[11px] leading-tight text-ink-2">{title}</span>
        </div>
      )}
      {/* spine highlight */}
      <div className="pointer-events-none absolute inset-y-0 left-0 w-[6px] bg-gradient-to-r from-black/15 to-transparent" />
    </div>
  );
}

export function Strength({ value }: { value: number }) {
  return (
    <span className="inline-flex gap-[2px]">
      {[1, 2, 3, 4, 5].map((i) => (
        <span key={i} className={`h-[5px] w-[5px] rounded-full ${i <= value ? "bg-ink-2" : "bg-rule"}`} />
      ))}
    </span>
  );
}
