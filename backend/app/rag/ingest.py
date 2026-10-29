"""Idempotent ingestion: reads data/docs/*.md, builds chunks and embeddings, and stores them in pgvector.

Uso:
    python -m app.rag.ingest            # only re-ingests documents that changed (and prunes deleted ones)
    python -m app.rag.ingest --reset    # recreates the chunks table (e.g. if you change EMBED_DIM)
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
from pathlib import Path

from ..config import Settings, get_settings
from ..db import Database
from ..llm import OllamaClient
from .chunking import split_markdown

log = logging.getLogger("ingest")


async def ingest(db: Database, llm: OllamaClient, settings: Settings, reset: bool = False) -> dict[str, int]:
    if reset:
        await db.reset_chunks()
    stats = {"documents": 0, "skipped": 0, "chunks": 0, "pruned": 0}
    files = sorted(Path(settings.docs_dir).glob("*.md"))
    if not files:
        log.warning("No .md documents in %s", settings.docs_dir)
    for path in files:
        text = path.read_text(encoding="utf-8")
        # The hash includes the model and parameters: if they change, the document is re-ingested.
        fingerprint = f"{settings.embed_model}|{settings.chunk_chars}|{settings.chunk_overlap}|{text}"
        digest = hashlib.sha256(fingerprint.encode()).hexdigest()
        if await db.get_document_hash(path.name) == digest:
            stats["skipped"] += 1
            continue
        chunks = split_markdown(text, path.name, settings.chunk_chars, settings.chunk_overlap)
        embeddings = await llm.embed_documents([c.content for c in chunks])
        await db.replace_document(path.name, digest, chunks, embeddings)
        stats["documents"] += 1
        stats["chunks"] += len(chunks)
        log.info("Ingested %s (%d chunks)", path.name, len(chunks))
    if files:  # never prune everything because of an empty/misconfigured docs dir
        stats["pruned"] = await db.prune_documents([p.name for p in files])
    return stats


async def _main(reset: bool) -> None:
    settings = get_settings()
    db = Database(settings.database_url, settings.embed_dim)
    llm = OllamaClient(
        settings.ollama_base_url, settings.llm_model, settings.embed_model,
        embed_query_prefix=settings.embed_query_prefix, embed_doc_prefix=settings.embed_doc_prefix,
    )
    await db.open()
    try:
        await db.init_schema()
        print(await ingest(db, llm, settings, reset=reset))
    finally:
        await llm.aclose()
        await db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    asyncio.run(_main(parser.parse_args().reset))
