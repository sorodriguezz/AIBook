import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import type {
  Benchmark,
  Bible,
  BookInfo,
  CatalogModel,
  DownloadProgress,
  Hardware,
  InstalledModel,
  Job,
  Segment,
} from "./types";

// ---- Rust side ---------------------------------------------------------------

export const native = {
  catalog: () => invoke<{ version: number; models: CatalogModel[] }>("catalog"),
  hardware: () => invoke<Hardware>("hardware_info"),
  installed: () => invoke<InstalledModel[]>("installed_models"),
  partialSize: (file: string) => invoke<number>("partial_size", { file }),
  startDownload: (req: { key: string; url: string; file: string; size: number; sha256: string }) =>
    invoke<void>("start_download", { req }),
  pauseDownload: (key: string) => invoke<void>("pause_download", { key }),
  cancelDownload: (key: string) => invoke<void>("cancel_download", { key }),
  deleteModel: (file: string) => invoke<void>("delete_model", { file }),
  engineLog: () => invoke<string | null>("engine_log"),
  onDownload: (cb: (p: DownloadProgress) => void): Promise<UnlistenFn> =>
    listen<DownloadProgress>("download-progress", (e) => cb(e.payload)),
};

// ---- Python engine -----------------------------------------------------------

let enginePromise: Promise<{ url: string; token: string }> | null = null;

function engineInfo() {
  if (!enginePromise) {
    enginePromise = invoke<{ url: string; token: string }>("engine_info").catch((e) => {
      enginePromise = null;
      // "engine_start_failed\n<last log lines>" from the Rust side.
      const [code, ...log] = String(e).split("\n");
      throw new EngineError(log.join("\n") || code, 0, code === "engine_start_failed" ? code : "engine_down");
    });
  }
  return enginePromise;
}

export class EngineError extends Error {
  constructor(
    message: string,
    public status: number,
    public code: string | null = null,
    public params: Record<string, string> = {},
  ) {
    super(message);
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const { url, token } = await engineInfo();
  let res: Response;
  try {
    res = await fetch(url + path, {
      method,
      headers: { "content-type": "application/json", "x-folio-token": token },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    enginePromise = null; // engine may have crashed; the next call restarts it
    throw new EngineError(String(e), 0);
  }
  if (!res.ok) {
    let detail: any = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {}
    if (detail && typeof detail === "object" && "code" in detail) {
      throw new EngineError(detail.message, res.status, detail.code, detail.params ?? {});
    }
    throw new EngineError(typeof detail === "string" ? detail : JSON.stringify(detail), res.status);
  }
  return res.json() as Promise<T>;
}

export interface LibraryBook {
  path: string;
  title: string;
  author: string;
  language: string | null;
  format: "pdf" | "epub";
  pages?: number;
  chapters?: number;
  size: number;
  added: number;
  cover_id: string;
  has_cover: boolean;
}

export interface Conversion {
  id: string;
  input_path: string;
  output_path: string;
  target: "pdf" | "epub";
  options: Record<string, any>;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  progress: number;
  error: string | null;
  error_code: string | null;
  title: string;
  created_at: number;
  eta_seconds?: number | null;
}

export interface LlmStatus {
  running: boolean;
  model_path: string | null;
  ctx_size: number | null;
  busy: boolean;
  idle_seconds: number;
  unload_in: number | null;
}

export interface ModelSpec {
  model_path: string;
  ctx_size: number;
  gpu_layers: number;
}

export const engine = {
  inspect: (path: string) => call<BookInfo>("POST", "/inspect", { path }),
  jobs: () => call<Job[]>("GET", "/jobs"),
  job: (id: string) => call<Job>("GET", `/jobs/${id}`),
  createJob: (req: {
    path: string;
    src: string;
    tgt: string;
    quality: string;
    model_id: string;
    model: ModelSpec;
    honorifics: string;
    review_glossary: boolean;
    output_dir: string | null;
    benchmark: Benchmark | null;
  }) => call<Job>("POST", "/jobs", req),
  pause: (id: string) => call("POST", `/jobs/${id}/pause`),
  resume: (id: string, model?: ModelSpec) => call("POST", `/jobs/${id}/resume`, model ?? null),
  cancel: (id: string) => call("POST", `/jobs/${id}/cancel`),
  remove: (id: string) => call("DELETE", `/jobs/${id}`),
  bible: (id: string) => call<Bible>("GET", `/jobs/${id}/bible`),
  saveBible: (id: string, bible: Bible) => call("PUT", `/jobs/${id}/bible`, bible),
  segments: (id: string, offset = 0, limit = 200) =>
    call<Segment[]>("GET", `/jobs/${id}/segments?offset=${offset}&limit=${limit}`),
  library: (dir: string) => call<LibraryBook[]>("GET", `/library?dir=${encodeURIComponent(dir)}`),
  libraryImport: (dir: string, paths: string[]) => call<string[]>("POST", "/library/import", { dir, paths }),
  coverUrl: async (coverId: string) => {
    const { url, token } = await engineInfo();
    return `${url}/library/cover/${coverId}?token=${token}`;
  },
  conversions: () => call<Conversion[]>("GET", "/conversions"),
  convert: (req: { path: string; target: "pdf" | "epub"; options: Record<string, any>; out_dir: string | null; title?: string }) =>
    call<Conversion>("POST", "/conversions", req),
  cancelConversion: (id: string) => call("POST", `/conversions/${id}/cancel`),
  removeConversion: (id: string) => call("DELETE", `/conversions/${id}`),
  llmStatus: () => call<LlmStatus>("GET", "/llm/status"),
  llmStop: () => call<LlmStatus>("POST", "/llm/stop"),
  benchmark: (model: ModelSpec, tgt = "es") => call<Benchmark>("POST", "/benchmark", { model, tgt }),
  pageUrl: async (id: string, page: number, which: "source" | "output") => {
    const { url, token } = await engineInfo();
    return `${url}/jobs/${id}/page?n=${page}&which=${which}&token=${token}`;
  },
};

/** Subscribe to engine events, reconnecting if the socket drops. */
export function subscribeEvents(onEvent: (ev: any) => void): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let retry = 500;

  const connect = async () => {
    try {
      const { url, token } = await engineInfo();
      ws = new WebSocket(url.replace(/^http/, "ws") + `/events?token=${token}`);
      ws.onmessage = (m) => onEvent(JSON.parse(m.data));
      ws.onopen = () => (retry = 500);
      ws.onclose = () => {
        if (!closed) setTimeout(connect, (retry = Math.min(retry * 2, 8000)));
      };
    } catch {
      if (!closed) setTimeout(connect, (retry = Math.min(retry * 2, 8000)));
    }
  };
  connect();
  return () => {
    closed = true;
    ws?.close();
  };
}
