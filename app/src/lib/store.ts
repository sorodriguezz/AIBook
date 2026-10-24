import { documentDir, join } from "@tauri-apps/api/path";
import { LazyStore } from "@tauri-apps/plugin-store";
import { create } from "zustand";
import { engine, native, subscribeEvents, type Conversion } from "./api";
import { friendlyError } from "./errors";
import { estimate } from "./estimate";
import { setUiLang, type UiLang } from "./i18n";
import type {
  Benchmark,
  CatalogModel,
  DownloadProgress,
  Hardware,
  InstalledModel,
  Job,
  ProgressEvent,
} from "./types";

const disk = new LazyStore("settings.json");

export interface Settings {
  theme: "system" | "light" | "dark";
  uiLang: UiLang;
  outputDir: string | null;
  libraryDir: string | null;
  saveToLibrary: boolean;
  ctxSize: number;
  lastTarget: string;
  quality: "fast" | "high" | "best";
  chosenQuant: Record<string, string>; // model id -> quant
  benchmarks: Record<string, Benchmark>; // "<model>:<quant>" -> result
}

const DEFAULTS: Settings = {
  theme: "system",
  uiLang: navigator.language.startsWith("es") ? "es" : "en",
  outputDir: null,
  libraryDir: null,
  saveToLibrary: true,
  ctxSize: 16384,
  lastTarget: "es",
  quality: "best",
  chosenQuant: {},
  benchmarks: {},
};

export type Route =
  | { name: "translate" }
  | { name: "library" }
  | { name: "convert"; path?: string }
  | { name: "configure"; path: string }
  | { name: "job"; id: string }
  | { name: "models" }
  | { name: "settings" };

interface AppState {
  route: Route;
  go: (r: Route) => void;

  settings: Settings;
  updateSettings: (patch: Partial<Settings>) => Promise<void>;

  catalog: CatalogModel[];
  hardware: Hardware | null;
  installed: InstalledModel[];
  downloads: Record<string, DownloadProgress>;
  partials: Record<string, number>;
  refreshInstalled: () => Promise<void>;

  jobs: Record<string, Job>;
  progress: Record<string, ProgressEvent>;
  conversions: Record<string, Conversion>;
  refreshConversions: () => Promise<void>;
  refreshJobs: () => Promise<void>;

  engineError: string | null;
  connectEngine: () => Promise<void>;
  init: () => Promise<void>;
}

export const useApp = create<AppState>((set, get) => ({
  route: { name: "translate" },
  go: (route) => set({ route }),

  settings: DEFAULTS,
  updateSettings: async (patch) => {
    const settings = { ...get().settings, ...patch };
    set({ settings });
    if (patch.uiLang) setUiLang(patch.uiLang);
    for (const [k, v] of Object.entries(patch)) await disk.set(k, v);
    await disk.save();
  },

  catalog: [],
  hardware: null,
  installed: [],
  downloads: {},
  partials: {},
  refreshInstalled: async () => {
    const installed = await native.installed();
    const partials: Record<string, number> = {};
    for (const m of get().catalog) {
      for (const q of Object.values(m.quants)) {
        if (!installed.some((i) => i.file === q.file)) {
          const n = await native.partialSize(q.file);
          if (n > 0) partials[q.file] = n;
        }
      }
    }
    set({ installed, partials });
  },

  jobs: {},
  progress: {},
  conversions: {},
  refreshConversions: async () => {
    const list = await engine.conversions();
    set({ conversions: Object.fromEntries(list.map((c) => [c.id, c])) });
  },
  refreshJobs: async () => {
    const list = await engine.jobs();
    set({ jobs: Object.fromEntries(list.map((j) => [j.id, j])) });
  },

  engineError: null,
  connectEngine: async () => {
    try {
      await get().refreshJobs();
      await get().refreshConversions();
      set({ engineError: null });
    } catch (e) {
      console.error("engine unavailable", e);
      set({ engineError: friendlyError(e) });
    }
  },
  init: async () => {
    const stored: Partial<Settings> = {};
    for (const k of Object.keys(DEFAULTS) as (keyof Settings)[]) {
      const v = await disk.get(k);
      if (v !== undefined && v !== null) (stored as any)[k] = v;
    }
    const settings = { ...DEFAULTS, ...stored };
    setUiLang(settings.uiLang);
    set({ settings });
    if (!settings.libraryDir) {
      // Default library: ~/Documents/AIBook (created on first scan).
      await get().updateSettings({ libraryDir: await join(await documentDir(), "AIBook") });
    }

    const [cat, hardware] = await Promise.all([native.catalog(), native.hardware()]);
    set({ catalog: cat.models, hardware });
    await get().refreshInstalled();

    native.onDownload((p) => {
      set((s) => ({ downloads: { ...s.downloads, [p.key]: p } }));
      if (p.state === "done" || p.state === "cancelled" || p.state === "paused") get().refreshInstalled();
    });

    subscribeEvents((ev) => {
      if (ev.type === "progress") {
        set((s) => ({ progress: { ...s.progress, [ev.job_id]: ev } }));
      } else if (ev.type === "conversion") {
        set((s) => ({ conversions: { ...s.conversions, [ev.conversion.id]: ev.conversion } }));
      } else if (ev.type === "job") {
        set((s) => ({ jobs: { ...s.jobs, [ev.job.id]: { ...s.jobs[ev.job.id], ...ev.job } } }));
      }
    });
    await get().connectEngine();
  },
}));

/** Path, context and GPU split for running an installed model. */
export function modelSpec(modelId: string) {
  const { catalog, installed, settings, hardware } = useApp.getState();
  const m = catalog.find((x) => x.id === modelId);
  if (!m) return null;
  const quants = Object.entries(m.quants);
  const preferred = settings.chosenQuant[m.id];
  const pick =
    quants.find(([k, q]) => k === preferred && installed.some((i) => i.file === q.file)) ??
    quants.find(([, q]) => installed.some((i) => i.file === q.file));
  if (!pick) return null;
  const [quant, q] = pick;
  const file = installed.find((i) => i.file === q.file)!;
  const est = estimate(m, quant, settings.ctxSize, hardware);
  return {
    model: m,
    quant,
    spec: { model_path: file.path, ctx_size: settings.ctxSize, gpu_layers: est.gpuLayers },
    benchmark: settings.benchmarks[`${m.id}:${quant}`] ?? null,
  };
}

/** Where translations and conversions are written. */
export function outputDir(): string | null {
  const { settings } = useApp.getState();
  return settings.saveToLibrary && settings.libraryDir ? settings.libraryDir : settings.outputDir;
}

export function installedModels(): CatalogModel[] {
  const { catalog, installed } = useApp.getState();
  return catalog.filter((m) => Object.values(m.quants).some((q) => installed.some((i) => i.file === q.file)));
}

export function applyTheme(theme: Settings["theme"]) {
  const dark = theme === "dark" || (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}
