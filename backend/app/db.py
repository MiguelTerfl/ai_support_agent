"""Postgres access (pgvector + full-text search) with async psycopg 3."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .types import Chunk, ChunkDraft


def vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


def fts_query(text: str) -> str:
    """Turns a question into an OR query of terms ('a | b | c')."""
    seen: list[str] = []
    for tok in re.findall(r"[^\W_]{3,}", text.lower()):
        if tok not in seen:
            seen.append(tok)
    return " | ".join(seen[:12])


SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    source TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chunks (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL REFERENCES documents(source) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    heading TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    embedding vector({dim}) NOT NULL,
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    UNIQUE (source, chunk_index)
);
CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING gin (tsv);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    status TEXT NOT NULL,
    items TEXT NOT NULL,
    total_mxn NUMERIC(10, 2) NOT NULL,
    ordered_at DATE NOT NULL,
    shipped_at DATE,
    delivered_at DATE,
    carrier TEXT,
    tracking TEXT
);

CREATE TABLE IF NOT EXISTS tickets (
    id BIGSERIAL PRIMARY KEY,
    subject TEXT NOT NULL,
    description TEXT NOT NULL,
    email TEXT NOT NULL,
    order_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

# Fictional orders for the demo (the email works as basic verification).
# Statuses are English codes: in_transit, delivered, processing, cancelled.
SEED_ORDERS = [
    ("ORD-10001", "ana@example.com", "in_transit", "Aurora X5 Blender", 1899.00, date(2026, 10, 14), date(2026, 10, 15), None, "Estafeta", "EST123456789"),
    ("ORD-10002", "luis@example.com", "delivered", "R2 Robot Vacuum", 5490.00, date(2026, 10, 2), date(2026, 10, 3), date(2026, 10, 7), "DHL", "DHL998877665"),
    ("ORD-10003", "maria@example.com", "processing", "Aurora Brew Coffee Maker", 2350.00, date(2026, 10, 24), None, None, None, None),
    ("ORD-10004", "jorge@example.com", "delivered", "Pulse ANC Headphones", 1290.00, date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 14), "Estafeta", "EST555443322"),
    ("ORD-10005", "sofia@example.com", "cancelled", "Forno 30L Electric Oven", 3200.00, date(2026, 10, 20), None, None, None, None),
]


class Database:
    def __init__(self, url: str, embed_dim: int) -> None:
        self.embed_dim = int(embed_dim)
        self.pool = AsyncConnectionPool(url, min_size=1, max_size=6, open=False, kwargs={"row_factory": dict_row})

    async def open(self) -> None:
        await self.pool.open()
        await self.pool.wait(timeout=15)

    async def close(self) -> None:
        await self.pool.close()

    async def ping(self) -> bool:
        try:
            async with self.pool.connection() as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    async def init_schema(self) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(SCHEMA.format(dim=self.embed_dim))
            row = await (await conn.execute("SELECT atttypmod FROM pg_attribute WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'")).fetchone()
            if row and row["atttypmod"] not in (-1, self.embed_dim):
                raise RuntimeError(
                    f"The chunks table uses {row['atttypmod']}-dimension vectors but EMBED_DIM={self.embed_dim}. "
                    "Run `python -m app.rag.ingest --reset` to recreate it."
                )
            async with conn.cursor() as cur:
                await cur.executemany(
                    "INSERT INTO orders (order_id, email, status, items, total_mxn, ordered_at, shipped_at, delivered_at, carrier, tracking) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (order_id) DO UPDATE SET email = EXCLUDED.email, status = EXCLUDED.status, items = EXCLUDED.items, "
                    "total_mxn = EXCLUDED.total_mxn, ordered_at = EXCLUDED.ordered_at, shipped_at = EXCLUDED.shipped_at, "
                    "delivered_at = EXCLUDED.delivered_at, carrier = EXCLUDED.carrier, tracking = EXCLUDED.tracking",
                    SEED_ORDERS,
                )

    async def reset_chunks(self) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("DROP TABLE IF EXISTS chunks CASCADE")
            await conn.execute("DELETE FROM documents")
        await self.init_schema()

    # ---------------- RAG ----------------
    async def get_document_hash(self, source: str) -> str | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute("SELECT content_hash FROM documents WHERE source = %s", (source,))).fetchone()
        return row["content_hash"] if row else None

    async def prune_documents(self, keep: list[str]) -> int:
        """Deletes documents (and their chunks, via CASCADE) whose file no longer exists."""
        async with self.pool.connection() as conn:
            cur = await conn.execute("DELETE FROM documents WHERE NOT (source = ANY(%s))", (keep,))
            return cur.rowcount or 0

    async def replace_document(self, source: str, content_hash: str, chunks: list[ChunkDraft], embeddings: list[list[float]]) -> None:
        async with self.pool.connection() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM documents WHERE source = %s", (source,))
                await conn.execute("INSERT INTO documents (source, content_hash) VALUES (%s, %s)", (source, content_hash))
                async with conn.cursor() as cur:
                    await cur.executemany(
                        "INSERT INTO chunks (source, chunk_index, heading, content, embedding) VALUES (%s, %s, %s, %s, %s::vector)",
                        [(c.source, c.chunk_index, c.heading, c.content, vector_literal(e)) for c, e in zip(chunks, embeddings)],
                    )

    async def hybrid_search(self, embedding: list[float], text: str, k: int) -> list[Chunk]:
        """Hybrid search: vector (cosine) + English full-text, fused with RRF."""
        sql = """
        WITH vec AS (
            SELECT id, ROW_NUMBER() OVER (ORDER BY embedding <=> %(q)s::vector) AS r
            FROM chunks ORDER BY embedding <=> %(q)s::vector LIMIT 10
        ),
        fts AS (
            SELECT c.id, ROW_NUMBER() OVER (ORDER BY ts_rank_cd(c.tsv, query) DESC) AS r
            FROM chunks c, to_tsquery('english', %(t)s) query
            WHERE %(t)s <> '' AND c.tsv @@ query
            ORDER BY ts_rank_cd(c.tsv, query) DESC LIMIT 10
        )
        SELECT c.id, c.source, c.heading, c.content,
               1 - (c.embedding <=> %(q)s::vector) AS score,
               COALESCE(1.0 / (60 + vec.r), 0) + COALESCE(1.0 / (60 + fts.r), 0) AS rrf
        FROM chunks c
        LEFT JOIN vec ON vec.id = c.id
        LEFT JOIN fts ON fts.id = c.id
        WHERE vec.id IS NOT NULL OR fts.id IS NOT NULL
        ORDER BY rrf DESC
        LIMIT %(k)s
        """
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(sql, {"q": vector_literal(embedding), "t": fts_query(text), "k": k})).fetchall()
        return [Chunk(id=r["id"], source=r["source"], heading=r["heading"], content=r["content"], score=float(r["score"])) for r in rows]

    # ---------------- Business (demo) ----------------
    async def get_order(self, order_id: str, email: str) -> dict[str, Any] | None:
        """Returns the order only if the email matches (prevents looking up other people's orders)."""
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute("SELECT * FROM orders WHERE order_id = %s AND lower(email) = lower(%s)", (order_id, email))
            ).fetchone()
        return row

    async def create_ticket(self, subject: str, description: str, email: str, order_id: str | None) -> str:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    "INSERT INTO tickets (subject, description, email, order_id) VALUES (%s,%s,%s,%s) RETURNING id",
                    (subject, description, email, order_id),
                )
            ).fetchone()
        return f"TCK-{row['id']:06d}"
