from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Chunk:
    id: int
    source: str
    heading: str
    content: str
    score: float  # cosine similarity (0-1) of the vector against the query


@dataclass
class ChunkDraft:
    source: str
    heading: str
    chunk_index: int
    content: str
