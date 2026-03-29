"""
app/rag/indexing.py — Chunk-and-embed indexing pipeline.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

import asyncpg

from app.rag.chunking import ChunkMetadata, TextChunker
from app.rag.embeddings import EmbeddingClient

logger = logging.getLogger("sidecar.rag.indexing")


@dataclass
class IndexingResult:
    """Outcome of a single index_source call."""

    source_id: str
    chunks_indexed: int
    chunks_deleted: int
    total_tokens: int


def _to_pgvector(embedding: list[float]) -> str:
    """Format a float list as a pgvector literal."""
    inner = ",".join(f"{v:.8f}" for v in embedding)
    return f"[{inner}]"


def _metadata_jsonb(meta: ChunkMetadata) -> dict[str, Any]:
    """Extract JSON-safe metadata dict for the jsonb column."""
    return {
        "title": meta.title,
        "author": meta.author,
        "created_at": meta.created_at,
        **meta.extra,
    }


class IndexingPipeline:
    """Chunk text, embed, and upsert into rag_chunks."""

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        embedder: EmbeddingClient,
        chunker: TextChunker | None = None,
    ) -> None:
        self._pool = db_pool
        self._embedder = embedder
        self._chunker = chunker or TextChunker()

    async def index_source(
        self,
        text: str,
        metadata: ChunkMetadata,
    ) -> IndexingResult:
        """
        Chunk *text*, embed each chunk, then
        delete-and-insert inside a single transaction.
        """
        chunks = self._chunker.chunk_text(text, metadata)
        if not chunks:
            return IndexingResult(
                source_id=metadata.source_id,
                chunks_indexed=0,
                chunks_deleted=0,
                total_tokens=0,
            )

        texts = [c.text for c in chunks]
        embed_results = await self._embedder.embed_texts(texts)

        tenant_uuid = uuid.UUID(metadata.tenant_id)
        total_tokens = sum(r.token_count for r in embed_results)

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                deleted = await conn.fetchval(
                    "DELETE FROM rag_chunks "
                    "WHERE tenant_id = $1 "
                    "  AND source_id = $2 "
                    "RETURNING count(*)",
                    tenant_uuid,
                    metadata.source_id,
                )
                deleted_count: int = deleted or 0

                for chunk, emb in zip(
                    chunks, embed_results, strict=True
                ):
                    m = chunk.metadata
                    await conn.execute(
                        "INSERT INTO rag_chunks ("
                        "  tenant_id, source_type,"
                        "  source_id, chunk_index,"
                        "  body, embedding,"
                        "  token_count,"
                        "  household_id, client_id,"
                        "  advisor_id, account_id,"
                        "  visibility_tags, meta"
                        ") VALUES ("
                        "  $1,$2,$3,$4,$5,$6::vector,"
                        "  $7,$8,$9,$10,$11,$12,$13"
                        ")",
                        tenant_uuid,
                        m.source_type,
                        m.source_id,
                        chunk.chunk_index,
                        chunk.text,
                        _to_pgvector(emb.embedding),
                        chunk.token_count,
                        m.household_id,
                        m.client_id,
                        m.advisor_id,
                        m.account_id,
                        m.visibility_tags,
                        _metadata_jsonb(m),
                    )

        logger.info(
            "indexed source=%s chunks=%d deleted=%d",
            metadata.source_id,
            len(chunks),
            deleted_count,
        )
        return IndexingResult(
            source_id=metadata.source_id,
            chunks_indexed=len(chunks),
            chunks_deleted=deleted_count,
            total_tokens=total_tokens,
        )

    async def delete_source(
        self,
        tenant_id: str,
        source_id: str,
    ) -> int:
        """Delete all chunks for a source. Returns count."""
        tenant_uuid = uuid.UUID(tenant_id)
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM rag_chunks "
                "WHERE tenant_id = $1 "
                "  AND source_id = $2",
                tenant_uuid,
                source_id,
            )
            count = int(result.split()[-1])
        logger.info(
            "deleted source=%s count=%d",
            source_id,
            count,
        )
        return count
