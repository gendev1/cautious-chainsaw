"""app/routers/digest.py -- Daily digest generation and retrieval endpoints."""
from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from langfuse import Langfuse
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.context import RequestContext
from app.dependencies import get_langfuse, get_platform_client, get_redis, get_request_context
from app.models.schemas import DailyDigest
from app.services.platform_client import PlatformClient
from app.utils.errors import InternalHTTPError

logger = logging.getLogger("sidecar.routers.digest")

router = APIRouter(prefix="/digest", tags=["digest"])

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class DigestGenerateRequest(BaseModel):
    advisor_id: str = Field(description="Advisor to generate digest for")
    force_regenerate: bool = Field(default=False, description="Force regeneration even if cached")


class DigestJobAccepted(BaseModel):
    job_id: str = Field(description="Background job identifier")
    status: str = Field(default="accepted", description="Job status")
    message: str = Field(default="Digest generation enqueued", description="Status message")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/generate", status_code=202)
async def generate_digest(
    body: DigestGenerateRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> DigestJobAccepted:
    """Enqueue a background job to generate the daily digest."""
    try:
        from app.jobs.enqueue import JobContext, get_job_pool

        job_ctx = JobContext(
            tenant_id=ctx.tenant_id,
            actor_id=ctx.actor_id,
            actor_type=ctx.actor_type,
            request_id=ctx.request_id,
            access_scope=ctx.access_scope.model_dump() if ctx.access_scope else {},
        )

        pool = await get_job_pool()
        job = await pool.enqueue_job(
            "run_digest",
            job_ctx.model_dump(),
            body.advisor_id,
            body.force_regenerate,
        )

        return DigestJobAccepted(
            job_id=job.job_id,
            status="accepted",
            message=f"Digest generation enqueued for advisor {body.advisor_id}",
        )
    except Exception as exc:
        logger.exception("Failed to enqueue digest generation job")
        raise InternalHTTPError(str(exc), ctx.request_id) from exc


@router.get("/latest")
async def get_latest_digest(
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> DailyDigest:
    """Read the latest cached daily digest from Redis."""
    key = f"digest:{ctx.tenant_id}:{ctx.actor_id}:latest"
    cached = await redis.get(key)
    if cached is None:
        raise HTTPException(status_code=404, detail="No cached digest found")
    return DailyDigest.model_validate(json.loads(cached))
