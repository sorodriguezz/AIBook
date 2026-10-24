import { save as saveDialog } from "@tauri-apps/plugin-dialog";
import { revealItemInDir } from "@tauri-apps/plugin-opener";
import { useEffect, useState } from "react";
import { Button, Select } from "../components/ui";
import { engine } from "../lib/api";
import { friendlyError } from "../lib/errors";
import { glossaryFromJob, glossarySources, mergeGlossary, pickGlossaryFile } from "../lib/glossary";
import { t, type Key } from "../lib/i18n";
import { useApp } from "../lib/store";
import type { Bible, GlossaryFile, Job } from "../lib/types";

const cell =
  "w-full rounded-lg border border-transparent bg-transparent px-2 py-1.5 text-[13.5px] outline-none transition-colors hover:border-rule focus:border-ink-3 focus:bg-card";

export default function Glossary({ job }: { job: Job }) {
  const [bible, setBible] = useState<Bible | null>(null);
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState<{ text: string; error?: boolean } | null>(null);
  const jobs = useApp((s) => s.jobs);
  const sources = glossarySources(jobs, job.src, job.tgt, job.id);

  useEffect(() => {
    engine.bible(job.id).then((b) => setBible({ ...b, style: b.style ?? {}, characters: b.characters ?? [], terms: b.terms ?? [] }));
  }, [job.id]);

  if (!bible) return <div className="breathe mt-12 text-ink-3">…</div>;

  const update = (patch: Partial<Bible>) => setBible({ ...bible, ...patch });
  const save = async () => {
    setSaving(true);
    await engine.saveBible(job.id, bible);
  };

  const importFrom = async (load: () => Promise<GlossaryFile | null>) => {
    setNote(null);
    try {
      const g = await load();
      if (!g) return;
      if ((g.src && g.src !== job.src) || (g.tgt && g.tgt !== job.tgt)) {
        setNote({ text: t("err.glossary_pair", { src: t(`lang.${g.src ?? job.src}` as Key), tgt: t(`lang.${g.tgt ?? job.tgt}` as Key) }), error: true });
        return;
      }
      if (!g.characters?.length && !g.terms?.length) {
        setNote({ text: t("err.glossary_empty"), error: true });
        return;
      }
      const merged = mergeGlossary(bible, g);
      setBible(merged.bible);
      setNote({ text: t("gl.imported", { n: merged.added, m: merged.updated }) });
    } catch (e) {
      setNote({ text: friendlyError(e), error: true });
    }
  };

  const exportFile = async () => {
    const path = await saveDialog({
      defaultPath: `${t("gl.fileName")} - ${job.title ?? "AIBook"}.json`,
      filters: [{ name: "AIBook", extensions: ["json"] }],
    });
    if (!path) return;
    try {
      await engine.exportGlossary(job.id, path, bible);
      revealItemInDir(path).catch(() => {});
    } catch (e) {
      setNote({ text: friendlyError(e), error: true });
    }
  };

  return (
    <section className="rise mt-12">
      <h2 className="display text-[26px] font-light">{t("gl.title")}</h2>
      <p className="mt-1 text-[13.5px] text-ink-2">{t("gl.subtitle")}</p>

      <div className="mt-5 flex flex-wrap items-center gap-2">
        <Button onClick={() => importFrom(pickGlossaryFile)}>{t("gl.import")}</Button>
        {sources.length > 0 && (
          <Select
            value=""
            onChange={(id) => id && importFrom(() => glossaryFromJob(jobs[id]))}
            className="max-w-[260px]"
          >
            <option value="">{t("gl.fromJob")}</option>
            {sources.map((j) => (
              <option key={j.id} value={j.id}>{j.title}</option>
            ))}
          </Select>
        )}
        <Button variant="quiet" onClick={exportFile}>{t("gl.export")}</Button>
      </div>
      {note && (
        <div className={`mt-3 rounded-xl px-4 py-2.5 text-[12.5px] ${note.error ? "bg-ribbon-soft" : "bg-paper-2 text-ink-2"}`}>{note.text}</div>
      )}

      <div className="mt-8 grid grid-cols-2 gap-x-8 gap-y-3 rounded-2xl bg-paper-2 p-5">
        {(["genre", "tone", "narration", "notes"] as const).map((k) => (
          <label key={k} className={k === "notes" ? "col-span-2" : ""}>
            <div className="eyebrow mb-1 px-2">{t(`gl.${k}` as Key)}</div>
            <input className={cell} value={bible.style[k] ?? ""} onChange={(e) => update({ style: { ...bible.style, [k]: e.target.value } })} />
          </label>
        ))}
      </div>

      <Table
        title={t("gl.characters")}
        rows={bible.characters}
        extra={{ key: "voice", label: t("gl.voice") }}
        onChange={(characters) => update({ characters })}
      />
      <Table
        title={t("gl.terms")}
        rows={bible.terms}
        extra={{ key: "note", label: t("gl.note") }}
        onChange={(terms) => update({ terms })}
      />

      <div className="sticky bottom-0 mt-10 flex justify-end border-t border-rule bg-paper/90 py-5 backdrop-blur">
        <Button variant="primary" disabled={saving} onClick={save}>{t("gl.continue")} →</Button>
      </div>
    </section>
  );
}

function Table<T extends { source: string; target?: string } & Record<string, any>>({
  title,
  rows,
  extra,
  onChange,
}: {
  title: string;
  rows: T[];
  extra: { key: string; label: string };
  onChange: (rows: T[]) => void;
}) {
  const set = (i: number, k: string, v: string) => onChange(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  return (
    <div className="mt-10">
      <div className="mb-2 flex items-center justify-between">
        <div className="eyebrow">{title} · {rows.length}</div>
        <Button variant="quiet" onClick={() => onChange([...rows, { source: "", target: "" } as T])}>+ {t("gl.add")}</Button>
      </div>
      <div className="grid grid-cols-[1fr_1fr_1.4fr_24px] gap-x-2 border-t border-rule">
        {[t("gl.source"), t("gl.target"), extra.label, ""].map((h, i) => (
          <div key={i} className="px-2 py-2 text-[11.5px] text-ink-3">{h}</div>
        ))}
        {rows.map((r, i) => (
          <div key={i} className="contents">
            <input className={cell} value={r.source} onChange={(e) => set(i, "source", e.target.value)} />
            <input className={`${cell} font-medium`} value={r.target ?? ""} placeholder={r.source} onChange={(e) => set(i, "target", e.target.value)} />
            <input className={`${cell} text-ink-2`} value={r[extra.key] ?? ""} onChange={(e) => set(i, extra.key, e.target.value)} />
            <button className="text-ink-3 hover:text-ribbon" onClick={() => onChange(rows.filter((_, j) => j !== i))}>×</button>
          </div>
        ))}
      </div>
    </div>
  );
}
