#!/usr/bin/env python3
"""End-to-end smoke test against a real model.

Starts the engine with the bundled llama-server, benchmarks the model and
translates the synthetic test EPUB, printing progress events as they arrive.

Usage: uv run --project engine python scripts/e2e_smoke.py MODEL.gguf [--tgt es] [--quality best]
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine" / "tests"))
from make_fixtures import make_epub  # noqa: E402

TOKEN = "e2e"


def api(port: int, method: str, path: str, body=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"content-type": "application/json", "x-folio-token": TOKEN},
    )
    with urllib.request.urlopen(req, timeout=3600) as r:
        return json.load(r)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--tgt", default="es")
    ap.add_argument("--quality", default="best")
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="folio-e2e-"))
    book = make_epub(work / "lantern.epub")
    llama = ROOT / "app" / "src-tauri" / "resources" / "llama" / ("llama-server.exe" if os.name == "nt" else "llama-server")
    env = {**os.environ, "FOLIO_TOKEN": TOKEN, "FOLIO_DATA_DIR": str(work / "data"), "FOLIO_LLAMA_SERVER": str(llama)}
    proc = subprocess.Popen([sys.executable, "-m", "folio_engine", "--port", "0"], env=env,
                            stdout=subprocess.PIPE, text=True)
    try:
        line = proc.stdout.readline()
        port = int(line.strip().split("=")[1])
        model = {"model_path": args.model, "ctx_size": 8192, "gpu_layers": 999}

        t0 = time.time()
        bench = api(port, "POST", "/benchmark", {"model": model, "tgt": args.tgt})
        print(f"benchmark ({time.time() - t0:.1f}s): prompt {bench['prompt_tps']} t/s, gen {bench['gen_tps']} t/s, "
              f"300 pages ≈ {bench['est_hours_300_pages']} h")

        job = api(port, "POST", "/jobs", {
            "path": str(book), "src": "en", "tgt": args.tgt, "quality": args.quality,
            "model_id": "test", "model": model, "benchmark": bench,
        })
        last = None
        while True:
            j = api(port, "GET", f"/jobs/{job['id']}")
            state = (j["status"], j["phase"], round(j["progress"], 2))
            if state != last:
                print("  ", *state)
                last = state
            if j["status"] in ("done", "failed", "cancelled", "paused"):
                break
            time.sleep(1)
        if j["status"] != "done":
            raise SystemExit(f"job ended as {j['status']}: {j['error']}")
        print(f"done in {j['stats'].get('elapsed_seconds')} s → {j['output_path']}")
        bible = j.get("bible") or {}
        print("bible characters:", [c.get("source") + "→" + str(c.get("target")) for c in bible.get("characters", [])])
        segs = api(port, "GET", f"/jobs/{job['id']}/segments")
        flagged = [s for s in segs if s["flags"]]
        print(f"{len(segs)} segments, {len(flagged)} flagged")
        for s in segs[:6]:
            print("  ·", (s["final"] or s["draft"])[:140])
        with zipfile.ZipFile(j["output_path"]) as z:
            assert z.namelist()[0] == "mimetype"
            assert z.read("OEBPS/images/map.png") == zipfile.ZipFile(book).read("OEBPS/images/map.png")
        print("output EPUB structure OK")
    finally:
        proc.terminate()
        proc.wait(10)


if __name__ == "__main__":
    main()
