#!/usr/bin/env python3
"""Freeze the Python engine with PyInstaller and stage it as a Tauri resource.

Usage (from the repo root): uv run --project engine --extra pdf --group build python scripts/build_engine.py
Output: app/src-tauri/resources/engine/folio-engine[.exe] plus its _internal/ folder.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "engine"
DEST = ROOT / "app" / "src-tauri" / "resources" / "engine"
BUILD = ROOT / "build" / "pyinstaller"


def main() -> None:
    entry = BUILD / "folio_entry.py"
    BUILD.mkdir(parents=True, exist_ok=True)
    # PyInstaller runs the entry as a script, so it cannot use package-relative imports.
    entry.write_text("from folio_engine.__main__ import main\n\nmain()\n")
    args = [
        sys.executable, "-m", "PyInstaller", str(entry),
        "--name", "folio-engine",
        "--onedir", "--noconfirm", "--clean", "--console",
        "--distpath", str(BUILD / "dist"),
        "--workpath", str(BUILD / "work"),
        "--specpath", str(BUILD),
        "--paths", str(ENGINE / "src"),
        "--collect-submodules", "folio_engine",
        # Packages that load data files or plugins dynamically.
        "--collect-all", "babeldoc",
        "--collect-all", "rapidocr",
        "--collect-all", "pymupdf",
        "--collect-all", "onnxruntime",
        "--collect-data", "tiktoken_ext",
        "--hidden-import", "tiktoken_ext.openai_public",
        "--collect-submodules", "uvicorn",
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
    ]
    subprocess.check_call(args)
    if DEST.exists():
        shutil.rmtree(DEST)
    shutil.copytree(BUILD / "dist" / "folio-engine", DEST)
    print(f"staged engine in {DEST}")


if __name__ == "__main__":
    main()
