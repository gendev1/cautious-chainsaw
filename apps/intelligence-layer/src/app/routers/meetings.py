"""app/routers/meetings.py -- Meeting prep, transcription, and summarization endpoints."""
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
from app.models.schemas import MeetingPrep, MeetingSummary
from app.services.platform_client import PlatformClient
from app.utils.errors import InternalHTTPError, ModelProviderHTTPError

logger = logging.getLogger("sidecar.routers.meetings")

router = APIRouter(prefix="/meetings", tags=["meetings"])

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class MeetingPrepRequest(BaseModel):
    client_id: str = Field(description="Client ID for meeting prep")
    household_id: str | None = Field(default=None, description="Household ID, if applicable")
    meeting_date: str | None = Field(default=None, description="Meeting date in ISO 8601 format")
    meeting_type: str = Field(
        default="general", description="Meeting type: general, review, planning"
    )


class TranscriptionRequest(BaseModel):
    meeting_id: str = Field(description="Meeting identifier")
    audio_storage_ref: str = Field(description="Object storage key for the audio file")
    duration_seconds: int = Field(description="Duration of the audio in seconds")


class TranscriptionJobAccepted(BaseModel):
    job_id: str = Field(description="Background job identifier")
    meeting_id: str = Field(description="Meeting identifier")
    status: str = Field(default="accepted", description="Job status")
    message: str = Field(default="Transcription job enqueued", description="Status message")


class MeetingSummarizeRequest(BaseModel):
    meeting_id: str = Field(description="Meeting identifier")
    transcript: str = Field(description="Full meeting transcript text")
    participants: list[str] = Field(default_factory=list, description="List of participant names")
    duration_minutes: int = Field(default=0, description="Meeting duration in minutes")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/prep")
async def prepare_meeting(
    body: MeetingPrepRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> MeetingPrep:
    """Run the meeting prep agent to generate a preparation brief."""
    try:
        from app.agents.base_deps import AgentDeps
        from app.agents.meeting_prep import meeting_prep_agent

        deps = AgentDeps(
            platform=platform,
            access_scope=ctx.access_scope,
            tenant_id=ctx.tenant_id,
            actor_id=ctx.actor_id,
        )

        prompt_parts = [
            f"Prepare a meeting brief for client {body.client_id}.",
            f"Meeting type: {body.meeting_type}",
        ]
        if body.household_id:
            prompt_parts.append(f"Household: {body.household_id}")
        if body.meeting_date:
            prompt_parts.append(f"Meeting date: {body.meeting_date}")

        result = await meeting_prep_agent.run("\n".join(prompt_parts), deps=deps)
        return result.output
    except Exception as exc:
        logger.exception("Meeting prep generation failed")
        raise ModelProviderHTTPError(str(exc), ctx.request_id) from exc


@router.post("/transcribe", status_code=202)
async def transcribe_meeting(
    body: TranscriptionRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> TranscriptionJobAccepted:
    """Enqueue a background transcription job for meeting audio."""
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
            "run_transcription",
            job_ctx.model_dump(),
            body.meeting_id,
            body.audio_storage_ref,
            body.duration_seconds,
            _job_timeout=max(600, body.duration_seconds * 2),
        )

        return TranscriptionJobAccepted(
            job_id=job.job_id,
            meeting_id=body.meeting_id,
            status="accepted",
            message=f"Transcription job enqueued for meeting {body.meeting_id}",
        )
    except Exception as exc:
        logger.exception("Failed to enqueue transcription job")
        raise InternalHTTPError(str(exc), ctx.request_id) from exc


@router.post("/summarize")
async def summarize_meeting(
    body: MeetingSummarizeRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> MeetingSummary:
    """Run the meeting summarizer agent on a transcript."""
    try:
        from app.agents.base_deps import AgentDeps
        from app.agents.meeting_summarizer import meeting_summarizer_agent

        deps = AgentDeps(
            platform=platform,
            access_scope=ctx.access_scope,
            tenant_id=ctx.tenant_id,
            actor_id=ctx.actor_id,
        )

        prompt_parts = [
            f"Summarize the following meeting (ID: {body.meeting_id}).",
            f"Duration: {body.duration_minutes} minutes.",
        ]
        if body.participants:
            prompt_parts.append(f"Participants: {', '.join(body.participants)}")
        prompt_parts.append(f"\nTranscript:\n{body.transcript}")

        result = await meeting_summarizer_agent.run("\n".join(prompt_parts), deps=deps)

        # Cache the summary in Redis
        cache_key = f"meeting_summary:{ctx.tenant_id}:{body.meeting_id}"
        await redis.set(
            cache_key,
            result.output.model_dump_json(),
            ex=86400,  # 24 hour TTL
        )

        return result.output
    except Exception as exc:
        logger.exception("Meeting summarization failed")
        raise ModelProviderHTTPError(str(exc), ctx.request_id) from exc


@router.get("/{meeting_id}/summary")
async def get_meeting_summary(
    meeting_id: str,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> MeetingSummary:
    """Read a cached meeting summary from Redis."""
    key = f"meeting_summary:{ctx.tenant_id}:{meeting_id}"
    cached = await redis.get(key)
    if cached is None:
        raise HTTPException(status_code=404, detail="Meeting summary not found")
    return MeetingSummary.model_validate(json.loads(cached))
