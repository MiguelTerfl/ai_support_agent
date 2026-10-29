from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..types import Chunk


class _Embedder(Protocol):
    async def embed_query(self, text: str) -> list[float]: ...


class _Store(Protocol):
    async def hybrid_search(self, embedding: list[float], text: str, k: int) -> list[Chunk]: ...


@dataclass
class RetrievalResult:
    chunks: list[Chunk]  # only those above the relevance threshold
    best_score: float


class Retriever:
    """Retrieves context and applies a relevance gate: if nothing reaches
    `min_score`, it returns nothing and the agent must say it has no information."""

    def __init__(self, store: _Store, embedder: _Embedder, top_k: int = 4, min_score: float = 0.45) -> None:
        self.store = store
        self.embedder = embedder
        self.top_k = top_k
        self.min_score = min_score

    async def search(self, query: str) -> RetrievalResult:
        embedding = await self.embedder.embed_query(query)
        found = await self.store.hybrid_search(embedding, query, self.top_k)
        best = max((c.score for c in found), default=0.0)
        relevant = [c for c in found if c.score >= self.min_score]
        return RetrievalResult(chunks=relevant, best_score=best)
