import { open } from "@tauri-apps/plugin-dialog";
import { engine } from "./api";
import type { Bible, GlossaryFile, Job } from "./types";

type Entry = { source: string; target?: string } & Record<string, any>;

/** Merge an imported glossary into `base`: imported renderings win, other details only fill gaps. */
export function mergeGlossary(base: Bible, incoming: GlossaryFile): { bible: Bible; added: number; updated: number } {
  let added = 0;
  let updated = 0;
  const merge = <T extends Entry>(rows: T[], extra: T[] = []): T[] => {
    const out = rows.map((r) => ({ ...r }));
    const index = new Map(out.map((r) => [r.source.trim().toLowerCase(), r]));
    for (const e of extra) {
      const key = e.source?.trim().toLowerCase();
      if (!key) continue;
      const cur = index.get(key);
      if (!cur) {
        const row = { ...e };
        out.push(row);
        index.set(key, row);
        added++;
        continue;
      }
      let changed = false;
      for (const [k, v] of Object.entries(e)) {
        if (v == null || v === "") continue;
        if (k === "target" ? cur.target !== v : !cur[k]) {
          (cur as any)[k] = v;
          changed = true;
        }
      }
      if (changed) updated++;
    }
    return out;
  };
  const style = { ...base.style };
  for (const [k, v] of Object.entries(incoming.style ?? {})) if (v && !(style as any)[k]) (style as any)[k] = v;
  return {
    bible: { ...base, style, characters: merge(base.characters, incoming.characters), terms: merge(base.terms, incoming.terms) },
    added,
    updated,
  };
}

/** Ask for an AIBook glossary file and read it; null if the user cancels. */
export async function pickGlossaryFile(): Promise<GlossaryFile | null> {
  const path = await open({ multiple: false, filters: [{ name: "AIBook", extensions: ["json"] }] });
  return typeof path === "string" ? engine.readGlossary(path) : null;
}

/** Another translation's glossary, in the same shape as a file. */
export async function glossaryFromJob(job: Job): Promise<GlossaryFile> {
  const bible = await engine.bible(job.id);
  return { ...bible, title: job.title, src: job.src, tgt: job.tgt };
}

/** Earlier translations whose glossary can be reused for this language pair. */
export function glossarySources(jobs: Record<string, Job>, src: string, tgt: string, except?: string): Job[] {
  return Object.values(jobs)
    .filter((j) => j.id !== except && j.src === src && j.tgt === tgt && j.quality !== "fast" && j.status !== "queued")
    .sort((a, b) => b.created_at - a.created_at);
}
