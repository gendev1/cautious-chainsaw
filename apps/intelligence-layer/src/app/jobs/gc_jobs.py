"""
app/jobs/gc_jobs.py — Garbage-collection jobs for stale
RAG chunks and embedding refresh.
"""
from __future__ import annotations

import logging
import uuid

import asyncpg

logger = logging.getLogger("sidecar.jobs.gc")


async def _source_exists(
    platform_client: object,
    source_id: str,
) -> bool:
    """
    Check whether a source still exists on the platform.

    Uses the platform client's document lookup. Returns
    False on any error (treat missing as deletable).
    """
    try:
        result = await platform_client.get_report_snapshot(  # type: ignore[attr-defined]
            source_id, access_scope=None
        )
        return result is not None
    except Exception:
        return False


async def gc_stale_chunks_job(
    ctx: dict,
    tenant_id: str,
    stale_threshold_days: int = 90,
    batch_size: int = 500,
) -> dict:
    """
    Delete chunks whose source no longer exists on the
    platform, or that are older than *stale_threshold_days*.

    Processes up to *batch_size* distinct sources per run.
    """
    pool: asyncpg.Pool = ctx["db_pool"]
    platform_client = ctx["platform_client"]
    tenant_uuid = uuid.UUID(tenant_id)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT source_id "
            "FROM rag_chunks "
            "WHERE tenant_id = $1 "
            "  AND created_at < "
            "    now() - ($2 || ' days')::interval "
            "ORDER BY source_id "
            "LIMIT $3",
            tenant_uuid,
            str(stale_threshold_days),
            batch_size,
        )

    sources_checked = 0
    sources_deleted = 0
    chunks_deleted = 0

    for row in rows:
        sid = row["source_id"]
        sources_checked += 1
        exists = await _source_exists(
            platform_client, sid
        )
        if exists:
            continue

        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM rag_chunks "
                "WHERE tenant_id = $1 "
                "  AND source_id = $2",
                tenant_uuid,
                sid,
            )
            count = int(result.split()[-1])
            chunks_deleted += count
            sources_deleted += 1

    logger.info(
        "gc_stale_chunks tenant=%s checked=%d "
        "deleted_sources=%d deleted_chunks=%d",
        tenant_id,
        sources_checked,
        sources_deleted,
        chunks_deleted,
    )
    return {
        "tenant_id": tenant_id,
        "sources_checked": sources_checked,
        "sources_deleted": sources_deleted,
        "chunks_deleted": chunks_deleted,
    }


async def gc_refresh_stale_embeddings_job(
    ctx: dict,
    tenant_id: str,
    older_than_days: int = 180,
    batch_size: int = 100,
) -> dict:
    """
    Re-index chunks whose embeddings are older than
    *older_than_days*.

    Enqueues index_document_job for each distinct source
    that has stale embeddings.
    """
    pool: asyncpg.Pool = ctx["db_pool"]
    arq_redis = ctx["arq_redis"]
    tenant_uuid = uuid.UUID(tenant_id)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT source_id, source_type "
            "FROM rag_chunks "
            "WHERE tenant_id = $1 "
            "  AND created_at < "
            "    now() - ($2 || ' days')::interval "
            "ORDER BY source_id "
            "LIMIT $3",
            tenant_uuid,
            str(older_than_days),
            batch_size,
        )

    enqueued = 0
    for row in rows:
        await arq_redis.enqueue_job(
            "index_document",
            tenant_id=tenant_id,
            source_id=row["source_id"],
            source_type=row["source_type"],
            text="",  # worker will re-fetch text
        )
        enqueued += 1

    logger.info(
        "gc_refresh_stale_embeddings tenant=%s "
        "enqueued=%d",
        tenant_id,
        enqueued,
    )
    return {
        "tenant_id": tenant_id,
        "sources_enqueued": enqueued,
    }
