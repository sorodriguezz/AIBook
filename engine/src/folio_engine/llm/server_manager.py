"""Starts and supervises the bundled llama-server process."""

import asyncio
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ..errors import UserError
from .client import LLMClient

log = logging.getLogger(__name__)


@dataclass
class ServerConfig:
    model_path: str
    ctx_size: int = 16384
    gpu_layers: int = 999  # llama.cpp clamps to the real layer count
    threads: int | None = None
    flash_attn: bool = True


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def find_llama_server() -> str | None:
    explicit = os.environ.get("FOLIO_LLAMA_SERVER")
    if explicit and Path(explicit).exists():
        return explicit
    return shutil.which("llama-server")


class LlamaServerManager:
    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self.config: ServerConfig | None = None
        self.port: int | None = None
        self.client: LLMClient | None = None
        self._lock = asyncio.Lock()
        self.log_tail: list[str] = []
        self.last_used = time.monotonic()
        self.loaded_at: float | None = None
        self.active_requests = 0

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def base_url(self) -> str | None:
        return f"http://127.0.0.1:{self.port}" if self.port else None

    async def ensure(self, config: ServerConfig) -> LLMClient:
        """Start llama-server with `config`, reusing the process if unchanged."""
        async with self._lock:
            if self.running and self.config == config and self.client:
                self.touch()
                return self.client
            await self._stop_locked()
            binary = find_llama_server()
            if not binary:
                raise UserError("llama_missing", "llama-server binary not found (FOLIO_LLAMA_SERVER)", 500)
            if not Path(config.model_path).exists():
                raise UserError("model_missing", f"model file not found: {config.model_path}", 404)
            self.port = _free_port()
            args = [
                binary,
                "-m", config.model_path,
                "--host", "127.0.0.1",
                "--port", str(self.port),
                "-c", str(config.ctx_size),
                "-ngl", str(config.gpu_layers),
                "--parallel", "1",
                "--jinja",
                "--no-webui",
            ]
            if config.flash_attn:
                args += ["-fa", "auto"]
            if config.threads:
                args += ["-t", str(config.threads)]
            log.info("starting llama-server: %s", " ".join(args))
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            self._proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=flags,
            )
            asyncio.get_running_loop().run_in_executor(None, self._drain_output, self._proc)
            self.config = config
            self.client = LLMClient(self.base_url, on_use=self._on_use)
            await self._wait_healthy()
            self.loaded_at = time.time()
            self.touch()
            return self.client

    def touch(self) -> None:
        self.last_used = time.monotonic()

    def _on_use(self, delta: int) -> None:
        self.active_requests = max(0, self.active_requests + delta)
        self.touch()

    def idle_seconds(self) -> float:
        return 0.0 if self.active_requests else time.monotonic() - self.last_used

    def _drain_output(self, proc: subprocess.Popen) -> None:
        assert proc.stdout
        for line in proc.stdout:
            self.log_tail.append(line.rstrip())
            del self.log_tail[:-200]

    async def _wait_healthy(self, timeout: float = 600) -> None:
        assert self.client
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if not self.running:
                tail = "\n".join(self.log_tail[-15:])
                raise UserError("model_load_failed", f"llama-server exited while loading the model:\n{tail}", 500)
            if await self.client.health():
                return
            await asyncio.sleep(0.5)
        raise UserError("model_timeout", "llama-server did not become ready in time", 504)

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        if self.client:
            await self.client.close()
            self.client = None
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(asyncio.to_thread(self._proc.wait), 10)
            except asyncio.TimeoutError:
                self._proc.kill()
        self._proc = None
        self.config = None
        self.port = None
        self.loaded_at = None
        self.active_requests = 0
