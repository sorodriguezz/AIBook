import { useEffect, type ReactNode } from "react";
import { t, useLang, type Key } from "./lib/i18n";
import { applyTheme, useApp, type Route } from "./lib/store";
import UpdateNotice from "./components/UpdateNotice";
import Configure from "./screens/Configure";
import JobView from "./screens/JobView";
import Convert from "./screens/Convert";
import Library from "./screens/Library";
import Translate from "./screens/Translate";
import Models from "./screens/Models";
import Settings from "./screens/Settings";

const NAV: { route: Route["name"]; key: Key; icon: ReactNode }[] = [
  {
    route: "translate",
    key: "nav.translate",
    icon: <path d="M3.5 5h7M7 3.5V5M5 5c.4 2.6 2.2 4.6 4.5 5.5M9 5c-.6 2.8-2.6 4.9-5 6M11 16.5l3-7.5 3 7.5M12.1 14h3.8" />,
  },
  {
    route: "library",
    key: "nav.library",
    icon: (
      <path d="M4 4.5h4.5a2 2 0 0 1 2 2V16a1.6 1.6 0 0 0-1.6-1.6H4zM16 4.5h-4.5a2 2 0 0 0-2 2V16a1.6 1.6 0 0 1 1.6-1.6H16z" />
    ),
  },
  {
    route: "convert",
    key: "nav.convert",
    icon: <path d="M4 7h10.5M11.5 4l3 3-3 3M16 13H5.5M8.5 10l-3 3 3 3" />,
  },
  {
    route: "models",
    key: "nav.models",
    icon: <path d="M10 3.5 16 7v6l-6 3.5L4 13V7zM4 7l6 3.5L16 7M10 10.5v6" />,
  },
  {
    route: "settings",
    key: "nav.settings",
    icon: <path d="M4 6h7M14 6h2M4 14h2M9 14h7M12.5 4v4M7.5 12v4" />,
  },
];

export default function App() {
  useLang();
  const { route, go, init, settings, jobs, conversions } = useApp();

  useEffect(() => {
    init();
  }, [init]);

  useEffect(() => {
    applyTheme(settings.theme);
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => applyTheme(settings.theme);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [settings.theme]);

  const running = Object.values(jobs).some((j) => j.status === "running");
  const active = route.name === "configure" || route.name === "job" ? "translate" : route.name;
  const converting = Object.values(conversions).some((c) => c.status === "running");

  return (
    <div className="flex h-full">
      <aside className="flex w-[200px] shrink-0 flex-col border-r border-rule px-4 pb-5 pt-9" data-tauri-drag-region>
        <div className="mb-10 px-2" data-tauri-drag-region>
          <div className="display text-[26px] font-medium leading-none">
            AIBook<span className="text-ribbon">.</span>
          </div>
        </div>
        <nav className="space-y-0.5">
          {NAV.map((n) => (
            <button
              key={n.route}
              onClick={() => go({ name: n.route } as Route)}
              className={`group flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-[13.5px] transition-colors ${
                active === n.route ? "bg-paper-2 text-ink" : "text-ink-2 hover:text-ink"
              }`}
            >
              <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" strokeLinecap="round">
                {n.icon}
              </svg>
              {t(n.key)}
              {((n.route === "translate" && running) || (n.route === "convert" && converting)) && (
                <span className="breathe ml-auto h-1.5 w-1.5 rounded-full bg-ribbon" />
              )}
            </button>
          ))}
        </nav>
        <div className="mt-auto">
          <UpdateNotice />
          <div className="px-2 text-[11px] leading-relaxed text-ink-3">{t("s.about")}</div>
        </div>
      </aside>

      {/* Window drag strip across the whole top edge; stays put while content scrolls. */}
      <div data-tauri-drag-region className="fixed inset-x-0 top-0 z-50 h-9" />

      <main className="relative min-w-0 flex-1 overflow-y-auto">
        <div className="h-8 w-full" />
        {route.name === "translate" && <Translate />}
        {route.name === "library" && <Library />}
        {route.name === "convert" && <Convert key={route.path ?? "new"} initialPath={route.path} />}
        {route.name === "configure" && <Configure key={route.path} path={route.path} />}
        {route.name === "job" && <JobView key={route.id} id={route.id} />}
        {route.name === "models" && <Models />}
        {route.name === "settings" && <Settings />}
      </main>
    </div>
  );
}
