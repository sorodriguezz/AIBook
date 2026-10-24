import { getCurrentWebview } from "@tauri-apps/api/webview";
import { open } from "@tauri-apps/plugin-dialog";
import { openPath, revealItemInDir } from "@tauri-apps/plugin-opener";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Button, Cover } from "../components/ui";
import { engine, type LibraryBook } from "../lib/api";
import { friendlyError } from "../lib/errors";
import { bytes } from "../lib/format";
import { t, type Key } from "../lib/i18n";
import { useApp } from "../lib/store";

const ACCEPT = /\.(pdf|epub)$/i;
type Filter = "all" | "pdf" | "epub";

export default function Library() {
  const { settings, go, conversions, jobs } = useApp();
  const dir = settings.libraryDir;
  const [books, setBooks] = useState<LibraryBook[] | null>(null);
  const [covers, setCovers] = useState<Record<string, string>>({});
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [selected, setSelected] = useState<LibraryBook | null>(null);
  const [hover, setHover] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!dir) return;
    try {
      const list = await engine.library(dir);
      setBooks(list);
      const urls: Record<string, string> = {};
      for (const b of list) if (b.has_cover) urls[b.cover_id] = await engine.coverUrl(b.cover_id);
      setCovers(urls);
      setError(null);
    } catch (e) {
      setError(friendlyError(e));
    }
  }, [dir]);

  // Rescan when translations or conversions finish (they write into the library).
  const finished = Object.values(conversions).filter((c) => c.status === "done").length +
    Object.values(jobs).filter((j) => j.status === "done").length;
  useEffect(() => {
    load();
  }, [load, finished]);

  const importPaths = useCallback(
    async (paths: string[]) => {
      if (!dir) return;
      const books = paths.filter((p) => ACCEPT.test(p));
      if (!books.length) return;
      try {
        await engine.libraryImport(dir, books);
        await load();
      } catch (e) {
        setError(friendlyError(e));
      }
    },
    [dir, load],
  );

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    getCurrentWebview()
      .onDragDropEvent((e) => {
        const p = e.payload;
        if (p.type === "enter" || p.type === "over") setHover(true);
        else if (p.type === "leave") setHover(false);
        else if (p.type === "drop") {
          setHover(false);
          importPaths(p.paths);
        }
      })
      .then((u) => (unlisten = u));
    return () => unlisten?.();
  }, [importPaths]);

  const add = async () => {
    const picked = await open({ multiple: true, filters: [{ name: "Libros", extensions: ["pdf", "epub"] }] });
    if (Array.isArray(picked)) importPaths(picked);
    else if (typeof picked === "string") importPaths([picked]);
  };

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (books ?? []).filter(
      (b) =>
        (filter === "all" || b.format === filter) &&
        (!q || b.title.toLowerCase().includes(q) || b.author.toLowerCase().includes(q)),
    );
  }, [books, query, filter]);

  return (
    <div className={`relative mx-auto max-w-[1040px] px-12 pb-20 transition-colors ${hover ? "bg-ribbon-soft" : ""}`}>
      <header className="rise mb-8 mt-6 flex items-end justify-between gap-6">
        <div className="min-w-0">
          <h1 className="display text-[44px] font-light leading-none">{t("lib.title")}</h1>
          <button
            onClick={() => dir && revealItemInDir(dir)}
            className="mt-3 max-w-full truncate text-left text-[13px] text-ink-3 hover:text-ink-2"
            title={dir ?? ""}
          >
            {dir?.replace(/^\/Users\/[^/]+/, "~")}
          </button>
        </div>
        <Button variant="primary" onClick={add}>+ {t("lib.add")}</Button>
      </header>

      <div className="rise mb-8 flex items-center gap-3" style={{ animationDelay: "60ms" }}>
        <div className="relative flex-1">
          <svg className="absolute left-3.5 top-1/2 -translate-y-1/2 text-ink-3" width="14" height="14" viewBox="0 0 16 16">
            <circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" strokeWidth="1.4" />
            <path d="m11 11 3.5 3.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("lib.search")}
            className="h-10 w-full rounded-full border border-rule bg-card pl-10 pr-4 text-[13.5px] outline-none transition-colors placeholder:text-ink-3 focus:border-ink-3"
          />
        </div>
        <div className="flex rounded-full bg-paper-2 p-1">
          {(["all", "epub", "pdf"] as Filter[]).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`rounded-full px-3.5 py-1.5 text-[12.5px] transition-colors ${filter === f ? "bg-card text-ink shadow-[0_1px_2px_rgba(0,0,0,.08)]" : "text-ink-2"}`}
            >
              {f === "all" ? t("lib.all") : f.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      {error && <div className="mb-6 rounded-xl bg-ribbon-soft px-4 py-3 text-[13px]">{error}</div>}

      {books === null ? (
        <div className="breathe text-[13px] text-ink-3">{t("lib.loading")}</div>
      ) : books.length === 0 ? (
        <Empty onAdd={add} />
      ) : shown.length === 0 ? (
        <p className="text-[13px] text-ink-3">{t("lib.noMatch")}</p>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(148px,1fr))] gap-x-7 gap-y-9">
          {shown.map((b, i) => (
            <button
              key={b.path}
              onClick={() => setSelected(b)}
              className="rise group text-left"
              style={{ animationDelay: `${Math.min(i, 20) * 25}ms` }}
            >
              <div className="transition-transform duration-300 group-hover:-translate-y-1">
                <Cover src={covers[b.cover_id]} title={b.title} className="aspect-[2/3] w-full" />
              </div>
              <div className="mt-3 line-clamp-2 text-[13.5px] font-medium leading-snug">{b.title}</div>
              <div className="mt-0.5 flex items-center gap-1.5 text-[11.5px] text-ink-3">
                <span className="rounded bg-paper-2 px-1.5 py-[1px] text-[10px] font-medium tracking-wide">{b.format.toUpperCase()}</span>
                {b.language && <span>{t(`lang.${b.language}` as Key)}</span>}
                {b.author && <span className="truncate">· {b.author}</span>}
              </div>
            </button>
          ))}
        </div>
      )}

      {selected && (
        <BookSheet
          book={selected}
          cover={covers[selected.cover_id]}
          onClose={() => setSelected(null)}
          onTranslate={() => go({ name: "configure", path: selected.path })}
          onConvert={() => go({ name: "convert", path: selected.path })}
        />
      )}
    </div>
  );
}

function Empty({ onAdd }: { onAdd: () => void }) {
  return (
    <div className="rise flex flex-col items-center rounded-[22px] border border-dashed border-rule px-10 py-16 text-center">
      {/* An empty shelf */}
      <svg width="140" height="70" viewBox="0 0 140 70" className="text-rule">
        <rect x="18" y="14" width="16" height="44" rx="2" fill="currentColor" />
        <rect x="37" y="22" width="12" height="36" rx="2" fill="currentColor" opacity=".7" />
        <rect x="52" y="10" width="18" height="48" rx="2" fill="currentColor" opacity=".85" />
        <rect x="74" y="26" width="10" height="32" rx="2" fill="currentColor" opacity=".6" />
        <path d="M86 58 l14 -36 l8 3 l-14 36z" fill="var(--ribbon)" opacity=".75" />
        <rect x="6" y="58" width="128" height="4" rx="2" fill="currentColor" />
      </svg>
      <div className="display mt-6 text-[22px]">{t("lib.empty")}</div>
      <p className="mt-1 max-w-[380px] text-[13px] text-ink-3">{t("lib.emptyHint")}</p>
      <Button variant="primary" className="mt-6" onClick={onAdd}>+ {t("lib.add")}</Button>
    </div>
  );
}

function BookSheet({
  book,
  cover,
  onClose,
  onTranslate,
  onConvert,
}: {
  book: LibraryBook;
  cover?: string;
  onClose: () => void;
  onTranslate: () => void;
  onConvert: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const other = book.format === "pdf" ? "EPUB" : "PDF";
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-ink/15 backdrop-blur-[2px]" onClick={onClose}>
      <aside
        onClick={(e) => e.stopPropagation()}
        className="rise h-full w-[380px] overflow-y-auto border-l border-rule bg-paper px-8 pb-10 pt-14 shadow-[-24px_0_48px_-24px_rgba(40,25,10,.35)]"
      >
        <button onClick={onClose} className="absolute right-5 top-12 text-[18px] text-ink-3 hover:text-ink">×</button>
        <Cover src={cover} title={book.title} className="mx-auto aspect-[2/3] w-[170px]" />
        <h2 className="display mt-7 text-[26px] font-light leading-tight">{book.title}</h2>
        {book.author && <div className="mt-1 text-[14px] text-ink-2">{book.author}</div>}
        <dl className="mt-6 grid grid-cols-2 gap-y-3 border-y border-rule py-4 text-[12.5px]">
          <dt className="text-ink-3">{t("lib.format")}</dt>
          <dd>{book.format.toUpperCase()}</dd>
          {book.language && (
            <>
              <dt className="text-ink-3">{t("lib.language")}</dt>
              <dd>{t(`lang.${book.language}` as Key)}</dd>
            </>
          )}
          {(book.pages ?? book.chapters) != null && (
            <>
              <dt className="text-ink-3">{book.pages != null ? t("lib.pages") : t("lib.chapters")}</dt>
              <dd className="tabular">{book.pages ?? book.chapters}</dd>
            </>
          )}
          <dt className="text-ink-3">{t("lib.size")}</dt>
          <dd className="tabular">{bytes(book.size)}</dd>
        </dl>
        <div className="mt-6 space-y-2">
          <Button variant="primary" className="w-full" onClick={onTranslate}>{t("lib.translate")}</Button>
          <Button className="w-full" onClick={onConvert}>{t("lib.convertTo", { f: other })}</Button>
          <div className="flex gap-2 pt-1">
            <Button variant="quiet" className="flex-1" onClick={() => openPath(book.path)}>{t("job.open")}</Button>
            <Button variant="quiet" className="flex-1" onClick={() => revealItemInDir(book.path)}>{t("job.reveal")}</Button>
          </div>
        </div>
      </aside>
    </div>
  );
}
