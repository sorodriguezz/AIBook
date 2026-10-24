import { getVersion } from "@tauri-apps/api/app";
import { invoke } from "@tauri-apps/api/core";
import { relaunch } from "@tauri-apps/plugin-process";
import { check, type Update } from "@tauri-apps/plugin-updater";
import { create } from "zustand";
import { useApp } from "./store";

type Phase = "idle" | "checking" | "available" | "none" | "downloading" | "installing" | "error";

interface UpdateState {
  current: string;
  phase: Phase;
  update: Update | null;
  downloaded: number;
  total: number;
  bytesPerSec: number;
  etaSeconds: number | null;
  error: string | null;
  checkNow: (silent?: boolean) => Promise<void>;
  install: () => Promise<void>;
}

export const useUpdates = create<UpdateState>((set, get) => ({
  current: "",
  phase: "idle",
  update: null,
  downloaded: 0,
  total: 0,
  bytesPerSec: 0,
  etaSeconds: null,
  error: null,

  checkNow: async (silent = false) => {
    set({ phase: "checking", error: null, current: await getVersion() });
    try {
      const update = await check();
      set({ update, phase: update ? "available" : "none" });
    } catch (e) {
      // No releases yet or offline: stay quiet on the automatic check.
      set({ phase: silent ? "idle" : "error", error: String(e) });
    }
  },

  install: async () => {
    const { update } = get();
    if (!update) return;
    set({ phase: "downloading", downloaded: 0, total: 0, etaSeconds: null });
    let windowStart = performance.now();
    let windowBytes = 0;
    let ema = 0;
    try {
      await update.download((ev) => {
        if (ev.event === "Started") {
          set({ total: ev.data.contentLength ?? 0 });
        } else if (ev.event === "Progress") {
          windowBytes += ev.data.chunkLength;
          const downloaded = get().downloaded + ev.data.chunkLength;
          const dt = (performance.now() - windowStart) / 1000;
          if (dt >= 1) {
            const inst = windowBytes / dt;
            ema = ema ? 0.8 * ema + 0.2 * inst : inst;
            windowStart = performance.now();
            windowBytes = 0;
          }
          const total = get().total;
          set({
            downloaded,
            bytesPerSec: ema,
            etaSeconds: ema && total ? (total - downloaded) / ema : null,
          });
        }
      });
      set({ phase: "installing" });
      // The engine and llama-server must not hold files the installer replaces.
      await invoke("stop_engine");
      await update.install();
      await relaunch();
    } catch (e) {
      set({ phase: "error", error: String(e) });
    }
  },
}));

/** True while a translation is running; installing would interrupt it. */
export function translationRunning(): boolean {
  return Object.values(useApp.getState().jobs).some((j) => j.status === "running" || j.status === "queued");
}
