#!/usr/bin/env python3
"""Download the pinned llama.cpp server build for a target and stage it as a Tauri resource.

Usage: python scripts/fetch_llama.py [--target aarch64-apple-darwin|x86_64-pc-windows-msvc|aarch64-pc-windows-msvc]

The whole release folder is kept (llama-server needs its ggml backend
libraries next to it) under app/src-tauri/resources/llama/.
"""

import argparse
import io
import platform
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

LLAMA_BUILD = "b11500"
BASE = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_BUILD}"

ASSETS = {
    # Metal is built into the macOS binaries.
    "aarch64-apple-darwin": f"llama-{LLAMA_BUILD}-bin-macos-arm64.tar.gz",
    # Vulkan runs on NVIDIA, AMD and Intel GPUs and falls back to CPU.
    "x86_64-pc-windows-msvc": f"llama-{LLAMA_BUILD}-bin-win-vulkan-x64.zip",
    # Snapdragon: the CPU build (NEON/i8mm) is the reliable choice.
    "aarch64-pc-windows-msvc": f"llama-{LLAMA_BUILD}-bin-win-cpu-arm64.zip",
}

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "app" / "src-tauri" / "resources" / "llama"


def host_target() -> str:
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        return "aarch64-apple-darwin"
    if sys.platform == "win32":
        return "aarch64-pc-windows-msvc" if machine in ("arm64", "aarch64") else "x86_64-pc-windows-msvc"
    raise SystemExit("unsupported host; pass --target")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default=None)
    args = ap.parse_args()
    target = args.target or host_target()
    asset = ASSETS[target]
    url = f"{BASE}/{asset}"
    print(f"downloading {url}")
    data = urllib.request.urlopen(url).read()

    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)
    if asset.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(DEST)
    else:
        with tarfile.open(fileobj=io.BytesIO(data)) as t:
            if hasattr(tarfile, "data_filter"):
                t.extractall(DEST, filter="data")
            else:
                t.extractall(DEST)

    exe = "llama-server.exe" if "windows" in target else "llama-server"
    found = next(DEST.rglob(exe), None)
    if not found:
        raise SystemExit(f"{exe} not found in {asset}")
    # Flatten so the binary sits at resources/llama/<exe>.
    src_dir = found.parent
    if src_dir != DEST:
        for item in src_dir.iterdir():
            shutil.move(str(item), DEST / item.name)
        shutil.rmtree(src_dir if src_dir.parent == DEST else src_dir.parent, ignore_errors=True)
    # Keep only what the server needs: drop the other CLI tools to save space.
    keep_prefixes = ("llama-server", "lib", "ggml", "llama.", "libllama", "libggml", "libmtmd", "mtmd")
    for item in DEST.iterdir():
        if item.is_file() and not item.name.startswith(keep_prefixes) and item.suffix not in (".dll", ".dylib", ".so", ".metal"):
            item.unlink()
    (DEST / exe).chmod(0o755)
    (DEST / "BUILD").write_text(f"{LLAMA_BUILD} {asset}\n")
    print(f"staged {DEST / exe}")


if __name__ == "__main__":
    main()
