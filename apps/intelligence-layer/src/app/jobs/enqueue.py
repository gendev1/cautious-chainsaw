"""
app/jobs/enqueue.py �� Job enqueue helpers for the API process.
"""
from __future__ import annotations

from typing import Any

from arq.connections import ArqRedis, RedisSettings, create_pool
from pydantic import BaseModel

from app.config import get_settings


class JobContext(BaseModel):
    """Tenant and actor context propagated to every background job."""

    tenant_id: str
    actor_id: str
    actor_type: str
    request_id: str
    access_scope: dict


_pool: ArqRedis | None = None


async def get_job_pool() -> ArqRedis:
    """Lazily create and cache the ARQ Redis pool."""
    global _pool  # noqa: PLW0603
    if _pool is None:
        settings = get_settings()
        _pool = await create_pool(
            RedisSettings.from_dsn(settings.redis_url)
        )
    return _pool


async def enqueue_transcription(
    job_ctx: JobContext,
    meeting_id: str,
    audio_object_key: str,
    audio_duration_seconds: int,
) -> str:
    """Enqueue an audio transcription job."""
    pool = await get_job_pool()
    job = await pool.enqueue_job(
        "run_transcription",
        job_ctx.model_dump(),
        meeting_id,
        audio_object_key,
        audio_duration_seconds,
        _job_timeout=max(600, audio_duration_seconds * 2),
    )
    return job.job_id


async def enqueue_meeting_summary(
    job_ctx: JobContext,
    meeting_id: str,
    transcript_key: str,
) -> str:
    """Enqueue a meeting summary job."""
    pool = await get_job_pool()
    job = await pool.enqueue_job(
        "run_meeting_summary",
        job_ctx.model_dump(),
        meeting_id,
        transcript_key,
    )
    return job.job_id


async def enqueue_firm_report(
    job_ctx: JobContext,
    report_type: str,
    filters: dict | None = None,
) -> str:
    """Enqueue a firm-wide report generation job."""
    pool = await get_job_pool()
    job = await pool.enqueue_job(
        "run_firm_report",
        job_ctx.model_dump(),
        report_type,
        filters or {},
        _job_timeout=1800,
    )
    return job.job_id


async def enqueue_rag_index_update(
    job_ctx: JobContext,
    source_type: str,
    source_id: str,
    event_type: str,
) -> str:
    """Enqueue a RAG index update job."""
    pool = await get_job_pool()
    job = await pool.enqueue_job(
        "run_rag_index_update",
        job_ctx.model_dump(),
        source_type,
        source_id,
        event_type,
    )
    return job.job_id


async def enqueue_portfolio_construction(
    job_ctx: JobContext,
    request: Any,
) -> str:
    """Enqueue a portfolio construction job."""
    pool = await get_job_pool()
    request_data = request.model_dump() if hasattr(request, "model_dump") else request
    job = await pool.enqueue_job(
        "run_portfolio_construction",
        job_ctx.model_dump(),
        request_data,
        _job_timeout=1800,
    )
    return job.job_id
