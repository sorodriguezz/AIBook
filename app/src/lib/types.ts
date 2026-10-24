export type LangCode = "en" | "es" | "fr" | "de" | "it" | "ja" | "zh";
export type Quality = "fast" | "high" | "best";
export type QuantKey = string;

export interface Quant {
  file: string;
  url: string;
  size: number;
  sha256: string;
}

export interface Geometry {
  architecture: string;
  layers: number;
  kv_layers: number;
  kv_heads: number;
  key_length: number;
  value_length: number;
  sliding_window: number | null;
  context_length: number;
}

export interface CatalogModel {
  id: string;
  name: string;
  family: string;
  repo: string;
  tier: "light" | "balanced" | "literary" | "premium" | "specialist";
  license: string;
  description: string;
  moe?: boolean;
  default_quant: QuantKey;
  quants: Record<QuantKey, Quant>;
  strengths: Record<LangCode, number>;
  geometry: Geometry;
}

export interface Hardware {
  os: string;
  arch: string;
  cpu: string;
  cores: number;
  ram_bytes: number;
  available_ram_bytes: number;
  unified_memory: boolean;
  gpus: { name: string; vram_bytes: number }[];
  gpu_budget_bytes: number;
  backend: string;
}

export interface InstalledModel {
  file: string;
  path: string;
  size: number;
}

export interface DownloadProgress {
  key: string;
  state: "downloading" | "verifying" | "done" | "paused" | "cancelled" | "error";
  downloaded: number;
  total: number;
  bytes_per_sec: number;
  eta_seconds: number | null;
  elapsed_seconds: number;
  error: string | null;
}

export interface Benchmark {
  prompt_tps: number | null;
  gen_tps: number | null;
  wall_seconds: number;
  sample_output: string;
  est_hours_300_pages: number | null;
  measured_at: number;
}

export type JobStatus =
  | "queued"
  | "running"
  | "paused"
  | "awaiting_glossary"
  | "done"
  | "failed"
  | "cancelled";

export interface Job {
  id: string;
  input_path: string;
  output_path: string | null;
  format: "epub" | "pdf";
  title: string | null;
  src: LangCode;
  tgt: LangCode;
  quality: Quality;
  model_id: string;
  options: Record<string, any> & { cover?: string };
  status: JobStatus;
  phase: string | null;
  progress: number;
  error: string | null;
  stats: Record<string, any>;
  created_at: number;
  updated_at: number;
  bible?: Bible | null;
}

export interface ProgressEvent {
  type: "progress";
  job_id: string;
  phase: string;
  detail: string;
  detail_kind?: "paragraphs" | "";
  progress: number;
  eta_seconds: number | null;
  elapsed_seconds: number;
  prompt_tps?: number;
  gen_tps?: number;
  preview?: { source: string; target: string };
}

export interface BookInfo {
  format: "epub" | "pdf";
  title: string;
  detected_language: LangCode | null;
  cover?: string;
  pages?: number;
  chapters?: number;
  segments?: number;
  tokens?: number;
  scanned_pages?: number[];
}

export interface BibleCharacter {
  source: string;
  target?: string;
  aliases?: string[];
  gender?: string;
  voice?: string;
  address?: string;
}
export interface BibleTerm {
  source: string;
  target?: string;
  note?: string;
}
export interface Bible {
  style: { genre?: string; tone?: string; narration?: string; register?: string; notes?: string };
  characters: BibleCharacter[];
  terms: BibleTerm[];
  honorifics?: "adapt" | "keep";
}

export interface Segment {
  seg_id: string;
  kind: string;
  chapter: number; // chapter index (EPUB) or page number (PDF)
  source: string;
  draft: string | null;
  final: string | null;
  flags: string | null;
  edited: number;
}

export interface SegmentCounts {
  total: number;
  translated: number;
  flagged: number;
  edited: number;
}

export interface SegmentPage {
  items: Segment[];
  total: number;
  counts: SegmentCounts;
}

export interface ExportEvent {
  type: "export";
  job_id: string;
  state: "running" | "done" | "failed";
  progress: number;
  path: string;
  error: string | null;
  error_code: string | null;
  at: number;
}

/** An AIBook glossary file, or another translation's bible used as one. */
export interface GlossaryFile extends Partial<Bible> {
  title?: string | null;
  src?: LangCode | null;
  tgt?: LangCode | null;
}
