#!/usr/bin/env python3
"""Generate catalog/models.json from the curated list below.

For each file it records the real size and sha256 (from the Hugging Face API)
and reads the GGUF header over HTTP (streaming only the first megabytes) to get
the attention geometry needed to estimate KV-cache memory.

Usage: python scripts/build_catalog.py
"""

import json
import struct
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "catalog" / "models.json"

# Curated for literary translation across en/es/fr/de/it/ja/zh. `strengths` are
# 1–5 editorial ratings to guide the default choice; the in-app benchmark and
# your own reading are the final word.
MODELS = [
    {
        "id": "qwen3.5-4b", "name": "Qwen3.5 4B", "family": "Qwen3.5", "repo": "unsloth/Qwen3.5-4B-GGUF",
        "files": {"Q4_K_M": "Qwen3.5-4B-Q4_K_M.gguf", "Q6_K": "Qwen3.5-4B-Q6_K.gguf", "Q8_0": "Qwen3.5-4B-Q8_0.gguf"},
        "default_quant": "Q6_K", "tier": "light", "license": "Apache-2.0",
        "description": "Ligero y rápido. Para equipos con 8 GB o para borradores.",
        "strengths": {"en": 3, "es": 3, "fr": 3, "de": 3, "it": 3, "ja": 3, "zh": 4},
    },
    {
        "id": "hunyuan-mt-7b", "name": "Hunyuan-MT 7B", "family": "Hunyuan-MT", "repo": "mradermacher/Hunyuan-MT-7B-GGUF",
        "files": {"Q4_K_M": "Hunyuan-MT-7B.Q4_K_M.gguf", "Q6_K": "Hunyuan-MT-7B.Q6_K.gguf", "Q8_0": "Hunyuan-MT-7B.Q8_0.gguf"},
        "default_quant": "Q6_K", "tier": "specialist",
        "license": "Tencent Hunyuan Community License (no válida en la UE, Reino Unido ni Corea del Sur)",
        "description": "Especialista en traducción (1º en 30 de 31 pares en WMT25). Muy fuerte en chino y japonés; "
                       "puede omitir frases en textos densos, úsalo con calidad Impecable.",
        "strengths": {"en": 4, "es": 3, "fr": 3, "de": 3, "it": 3, "ja": 4, "zh": 5},
    },
    {
        "id": "qwen3.5-9b", "name": "Qwen3.5 9B", "family": "Qwen3.5", "repo": "unsloth/Qwen3.5-9B-GGUF",
        "files": {"Q4_K_M": "Qwen3.5-9B-Q4_K_M.gguf", "Q6_K": "Qwen3.5-9B-Q6_K.gguf", "Q8_0": "Qwen3.5-9B-Q8_0.gguf"},
        "default_quant": "Q6_K", "tier": "balanced", "license": "Apache-2.0",
        "description": "Equilibrio calidad/velocidad para 16 GB. Muy bueno en chino y japonés.",
        "strengths": {"en": 4, "es": 4, "fr": 4, "de": 4, "it": 4, "ja": 4, "zh": 5},
    },
    {
        "id": "gemma-4-12b", "name": "Gemma 4 12B", "family": "Gemma 4", "repo": "unsloth/gemma-4-12b-it-GGUF",
        "files": {"Q4_K_M": "gemma-4-12b-it-Q4_K_M.gguf", "Q6_K": "gemma-4-12b-it-Q6_K.gguf", "Q8_0": "gemma-4-12b-it-Q8_0.gguf"},
        "default_quant": "Q4_K_M", "tier": "literary", "license": "Apache-2.0",
        "description": "Prosa natural y elegante; excelente entre lenguas europeas y con el japonés.",
        "strengths": {"en": 5, "es": 5, "fr": 5, "de": 4, "it": 5, "ja": 4, "zh": 4},
    },
    {
        "id": "gemma-4-26b-a4b", "name": "Gemma 4 26B A4B", "family": "Gemma 4", "repo": "unsloth/gemma-4-26B-A4B-it-GGUF",
        "files": {"Q4_K_M": "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf", "Q6_K": "gemma-4-26B-A4B-it-UD-Q6_K.gguf"},
        "default_quant": "Q4_K_M", "tier": "premium", "license": "Apache-2.0", "moe": True,
        "description": "Mezcla de expertos: calidad de modelo grande con velocidad de uno pequeño. "
                       "Recomendado con 24 GB o más.",
        "strengths": {"en": 5, "es": 5, "fr": 5, "de": 5, "it": 5, "ja": 5, "zh": 4},
    },
    {
        "id": "qwen3.5-35b-a3b", "name": "Qwen3.5 35B A3B", "family": "Qwen3.5", "repo": "unsloth/Qwen3.5-35B-A3B-GGUF",
        "files": {"Q4_K_M": "Qwen3.5-35B-A3B-Q4_K_M.gguf", "Q6_K": "Qwen3.5-35B-A3B-Q6_K.gguf"},
        "default_quant": "Q4_K_M", "tier": "premium", "license": "Apache-2.0", "moe": True,
        "description": "La máxima calidad del catálogo, sobre todo con chino y japonés. Requiere 32 GB.",
        "strengths": {"en": 5, "es": 5, "fr": 5, "de": 5, "it": 5, "ja": 5, "zh": 5},
    },
]

GGUF_TYPES = {0: "B", 1: "b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "?", 10: "<Q", 11: "<q", 12: "<d"}


class Stream:
    def __init__(self, resp):
        self.resp = resp
        self.read_bytes = 0

    def read(self, n: int) -> bytes:
        out = b""
        while len(out) < n:
            chunk = self.resp.read(n - len(out))
            if not chunk:
                raise EOFError
            out += chunk
        self.read_bytes += n
        return out

    def unpack(self, fmt: str):
        return struct.unpack(fmt, self.read(struct.calcsize(fmt)))[0]

    def string(self) -> str:
        return self.read(self.unpack("<Q")).decode("utf-8", "replace")


def read_value(s: Stream, t: int):
    if t == 8:
        return s.string()
    if t == 9:
        sub, n = s.unpack("<I"), s.unpack("<Q")
        if sub == 8:
            for _ in range(n):
                s.string()
            return f"<{n} strings>"
        vals = [read_value(s, sub) for _ in range(n)]
        return vals if n <= 512 else f"<{n} values>"
    return s.unpack(GGUF_TYPES[t])


def gguf_metadata(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "folio-catalog"})
    with urllib.request.urlopen(req) as resp:
        s = Stream(resp)
        assert s.read(4) == b"GGUF", "not a GGUF file"
        s.unpack("<I")  # version
        s.unpack("<Q")  # tensor count
        n_kv = s.unpack("<Q")
        meta = {}
        for _ in range(n_kv):
            key = s.string()
            meta[key] = read_value(s, s.unpack("<I"))
        return meta


def geometry(meta: dict) -> dict:
    arch = meta["general.architecture"]
    g = lambda k, d=None: meta.get(f"{arch}.{k}", d)
    layers = g("block_count")
    heads = g("attention.head_count")
    kv_heads = g("attention.head_count_kv", heads)
    if isinstance(kv_heads, list):  # per-layer (e.g. Gemma sliding/global mix)
        kv_heads = max(kv_heads)
    emb = g("embedding_length")
    key_len = g("attention.key_length") or (emb // heads if heads else 128)
    val_len = g("attention.value_length") or key_len
    # Hybrid models (linear attention + periodic full attention) only keep a
    # growing KV cache on the full-attention layers.
    interval = g("full_attention_interval")
    kv_layers = layers // interval if interval else layers
    sliding = g("attention.sliding_window")
    return {
        "architecture": arch,
        "layers": layers,
        "kv_layers": kv_layers,
        "kv_heads": kv_heads,
        "key_length": key_len,
        "value_length": val_len,
        "sliding_window": sliding,
        "context_length": g("context_length"),
    }


def hf_tree(repo: str) -> dict:
    with urllib.request.urlopen(f"https://huggingface.co/api/models/{repo}/tree/main") as r:
        return {t["path"]: t for t in json.load(r)}


def main() -> None:
    models = []
    for m in MODELS:
        tree = hf_tree(m["repo"])
        quants = {}
        geo = None
        for q, fname in m["files"].items():
            t = tree[fname]
            url = f"https://huggingface.co/{m['repo']}/resolve/main/{fname}"
            quants[q] = {"file": fname, "url": url, "size": t["size"], "sha256": t["lfs"]["oid"]}
            if geo is None:
                geo = geometry(gguf_metadata(url))
        entry = {k: v for k, v in m.items() if k not in ("files", "repo")}
        entry.update({"repo": m["repo"], "quants": quants, "geometry": geo})
        models.append(entry)
        print(f"{m['id']}: {geo}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"version": 1, "models": models}, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
