"""
app/jobs/indexing_jobs.py — ARQ jobs for document indexing.
"""
from __future__ import annotations

import logging
from dataclasses import asdict

from app.rag.chunking import ChunkMetadata
from app.rag.indexing import IndexingPipeline

logger = logging.getLogger("sidecar.jobs.indexing")


async def index_document_job(
    ctx: dict,
    tenant_id: str,
    source_id: str,
    source_type: str,
    text: str,
    household_id: str | None = None,
    client_id: str | None = None,
    advisor_id: str | None = None,
    account_id: str | None = None,
    visibility_tags: list[str] | None = None,
    title: str | None = None,
    author: str | None = None,
) -> dict:
    """
    Chunk, embed, and upsert a document into
    rag_chunks.
    """
    pipeline: IndexingPipeline = ctx["indexing_pipeline"]

    meta = ChunkMetadata(
        source_type=source_type,
        source_id=source_id,
        tenant_id=tenant_id,
        household_id=household_id,
        client_id=client_id,
        account_id=account_id,
        advisor_id=advisor_id,
        visibility_tags=visibility_tags or [],
        title=title,
        author=author,
    )

    result = await pipeline.index_source(text, meta)

    logger.info(
        "index_document_job finished source=%s "
        "chunks=%d tokens=%d",
        source_id,
        result.chunks_indexed,
        result.total_tokens,
    )
    return asdict(result)


async def delete_source_job(
    ctx: dict,
    tenant_id: str,
    source_id: str,
) -> dict:
    """Delete all chunks for a source."""
    pipeline: IndexingPipeline = ctx["indexing_pipeline"]
    deleted = await pipeline.delete_source(
        tenant_id, source_id
    )
    logger.info(
        "delete_source_job finished source=%s deleted=%d",
        source_id,
        deleted,
    )
    return {
        "source_id": source_id,
        "chunks_deleted": deleted,
    }
