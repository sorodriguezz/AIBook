"""Async client for llama-server's OpenAI-compatible API."""

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx


@dataclass
class Completion:
    text: str
    prompt_tokens: int
    completion_tokens: int
    prompt_seconds: float
    gen_seconds: float
    wall_seconds: float


class LLMError(RuntimeError):
    pass


_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


class LLMClient:
    def __init__(self, base_url: str, model: str = "local", on_use: Callable[[int], None] | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._on_use = on_use or (lambda _: None)  # +1 when a request starts, -1 when it ends
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(1800, connect=10))

    async def close(self) -> None:
        await self._http.aclose()

    async def health(self) -> bool:
        try:
            r = await self._http.get(f"{self.base_url}/health", timeout=3)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def chat(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        json_mode: bool = False,
        top_p: float = 0.9,
    ) -> Completion:
        body: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "repeat_penalty": 1.05,
            # Reasoning models (Qwen3 etc.) spend minutes "thinking" before every
            # paragraph; our explicit review pass is a better use of that time.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        t0 = time.monotonic()
        self._on_use(1)
        try:
            r = await self._http.post(f"{self.base_url}/v1/chat/completions", json=body)
        except httpx.HTTPError as e:
            raise LLMError(f"model server unreachable: {e}") from e
        finally:
            self._on_use(-1)
        if r.status_code != 200:
            raise LLMError(f"model server error {r.status_code}: {r.text[:300]}")
        data = r.json()
        wall = time.monotonic() - t0
        text = data["choices"][0]["message"].get("content") or ""
        text = _THINK_RE.sub("", text).strip()
        usage = data.get("usage") or {}
        timings = data.get("timings") or {}
        p_tok = usage.get("prompt_tokens") or timings.get("prompt_n") or 0
        c_tok = usage.get("completion_tokens") or timings.get("predicted_n") or 0
        p_ms = timings.get("prompt_ms")
        g_ms = timings.get("predicted_ms")
        if p_ms is None or g_ms is None:
            # Server without timings: attribute everything to generation.
            p_ms, g_ms = 0.0, wall * 1000
        return Completion(text, p_tok, c_tok, p_ms / 1000, g_ms / 1000, wall)

    async def chat_json(self, messages: list[dict], **kw) -> tuple[dict, Completion]:
        comp = await self.chat(messages, json_mode=True, **kw)
        return parse_json_loose(comp.text), comp

    async def tokenize_count(self, text: str) -> int:
        r = await self._http.post(f"{self.base_url}/tokenize", json={"content": text})
        r.raise_for_status()
        return len(r.json().get("tokens", []))


def parse_json_loose(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    raise LLMError("model did not return valid JSON")
