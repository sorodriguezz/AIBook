import type { CatalogModel, Hardware, QuantKey } from "./types";

const GB = 1024 ** 3;

export type Fit = "optimal" | "works" | "slow" | "no";

export interface MemoryEstimate {
  weights: number;
  kv: number;
  total: number;
  fit: Fit;
  gpuLayers: number;
}

/** KV-cache bytes for `ctx` tokens at f16. */
export function kvBytes(m: CatalogModel, ctx: number): number {
  const g = m.geometry;
  const perTokenPerLayer = g.kv_heads * (g.key_length + g.value_length) * 2;
  if (g.sliding_window) {
    // Sliding-window models keep full context only on their periodic global layers.
    const global = Math.ceil(g.kv_layers / 6);
    const local = g.kv_layers - global;
    return perTokenPerLayer * (global * ctx + local * Math.min(ctx, g.sliding_window));
  }
  return perTokenPerLayer * g.kv_layers * ctx;
}

export function estimate(m: CatalogModel, quant: QuantKey, ctx: number, hw: Hardware | null): MemoryEstimate {
  const weights = m.quants[quant]?.size ?? 0;
  const kv = kvBytes(m, ctx);
  const overhead = 0.6 * GB + weights * 0.04; // compute buffers, runtime
  const total = weights + kv + overhead;
  if (!hw) return { weights, kv, total, fit: "works", gpuLayers: 999 };

  const gpu = hw.gpu_budget_bytes;
  const ram = hw.ram_bytes;
  let fit: Fit;
  let gpuLayers = 999;
  if (hw.unified_memory) {
    fit = total <= gpu * 0.95 ? "optimal" : total <= ram * 0.85 ? "slow" : "no";
  } else if (gpu > 0) {
    if (total <= gpu * 0.92) {
      fit = "optimal";
    } else {
      // Offload as many layers as fit; the rest run on the CPU from system RAM.
      const perLayer = weights / m.geometry.layers;
      const avail = gpu * 0.92 - kv - overhead;
      gpuLayers = Math.max(0, Math.floor(avail / perLayer));
      const share = gpuLayers / m.geometry.layers;
      const fitsRam = total - gpuLayers * perLayer <= ram * 0.7;
      fit = !fitsRam ? "no" : share >= 0.5 || m.moe ? "works" : "slow";
    }
  } else {
    // CPU-only backend (e.g. Windows on ARM): everything lives in RAM.
    gpuLayers = 0;
    fit = total <= ram * 0.55 ? "works" : total <= ram * 0.8 ? "slow" : "no";
  }
  return { weights, kv, total, fit, gpuLayers };
}

/** Best quant that still runs well, falling back to the smallest one. */
export function bestQuant(m: CatalogModel, ctx: number, hw: Hardware | null): QuantKey {
  const keys = Object.keys(m.quants).sort((a, b) => m.quants[b].size - m.quants[a].size);
  for (const k of keys) {
    const f = estimate(m, k, ctx, hw).fit;
    if (f === "optimal" || (f === "works" && !hw?.gpu_budget_bytes)) return k;
  }
  return keys[keys.length - 1];
}

/** Model to suggest for a language pair on this machine. */
export function recommend(models: CatalogModel[], src: string, tgt: string, ctx: number, hw: Hardware | null) {
  const score = (m: CatalogModel) => {
    const q = bestQuant(m, ctx, hw);
    const fit = estimate(m, q, ctx, hw).fit;
    const fitScore = { optimal: 3, works: 2, slow: 0.5, no: -10 }[fit];
    const lang = Math.min(m.strengths[src as keyof typeof m.strengths] ?? 3, m.strengths[tgt as keyof typeof m.strengths] ?? 3);
    return fitScore * 2 + lang * 1.5 + (m.tier === "premium" ? 1 : 0);
  };
  return [...models].sort((a, b) => score(b) - score(a))[0];
}
