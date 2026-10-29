"""Minimal Ollama client (chat with tools, and embeddings)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx


class LLMUnavailable(RuntimeError):
    """The model server (your PC) is not responding or returned an error."""


@dataclass
class ChatResult:
    message: dict[str, Any]
    eval_count: int = 0
    total_ms: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        embed_model: str,
        num_ctx: int = 8192,
        temperature: float = 0.2,
        timeout: float = 180.0,
        embed_query_prefix: str = "",
        embed_doc_prefix: str = "",
    ) -> None:
        self.model = model
        self.embed_model = embed_model
        self.num_ctx = num_ctx
        self.temperature = temperature
        self.embed_query_prefix = embed_query_prefix
        self.embed_doc_prefix = embed_doc_prefix
        self._http = httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(timeout, connect=5.0))

    async def aclose(self) -> None:
        await self._http.aclose()

    async def ping(self) -> bool:
        try:
            r = await self._http.get("/api/tags", timeout=3.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> ChatResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"num_ctx": self.num_ctx, "temperature": self.temperature},
        }
        if tools:
            payload["tools"] = tools
        try:
            r = await self._http.post("/api/chat", json=payload)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"Ollama chat failed: {exc}") from exc
        data = r.json()
        return ChatResult(
            message=data.get("message", {}),
            eval_count=int(data.get("eval_count", 0) or 0),
            total_ms=float(data.get("total_duration", 0) or 0) / 1e6,
            raw=data,
        )

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        try:
            r = await self._http.post("/api/embed", json={"model": self.embed_model, "input": texts})
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"Ollama embed failed: {exc}") from exc
        return r.json()["embeddings"]

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed([self.embed_query_prefix + text]))[0]

    async def embed_documents(self, texts: list[str], batch_size: int = 16) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = [self.embed_doc_prefix + t for t in texts[i : i + batch_size]]
            out.extend(await self._embed(batch))
        return out
