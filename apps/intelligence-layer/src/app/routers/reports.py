"""app/routers/reports.py -- Report generation endpoints."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from langfuse import Langfuse
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.context import RequestContext
from app.dependencies import get_langfuse, get_platform_client, get_redis, get_request_context
from app.services.platform_client import PlatformClient
from app.utils.errors import InternalHTTPError, ModelProviderHTTPError

logger = logging.getLogger("sidecar.routers.reports")

router = APIRouter(prefix="/reports", tags=["reports"])

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class FirmWideReportRequest(BaseModel):
    report_type: str = Field(
        description="Report type: quarterly_review, aum_summary, client_activity"
    )
    filters: dict = Field(default_factory=dict, description="Optional filters for the report")


class FirmWideJobAccepted(BaseModel):
    job_id: str = Field(description="Background job identifier")
    report_type: str = Field(description="Type of report being generated")
    status: str = Field(default="accepted", description="Job status")
    message: str = Field(default="Report generation enqueued", description="Status message")


class ReportNarrativeRequest(BaseModel):
    report_id: str = Field(description="Report identifier to generate narrative for")
    snapshot: dict = Field(description="Data snapshot to narrate")
    tone: str = Field(
        default="professional",
        description="Narrative tone: professional, concise, detailed",
    )
    max_length_words: int = Field(default=1000, description="Maximum word count for the narrative")


class ReportNarrativeResponse(BaseModel):
    report_id: str = Field(description="Report identifier")
    narrative: str = Field(description="Generated narrative text")
    word_count: int = Field(description="Word count of the narrative")
    as_of: str = Field(description="ISO 8601 timestamp of generation")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/firm-wide", status_code=202)
async def generate_firm_wide_report(
    body: FirmWideReportRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> FirmWideJobAccepted:
    """Enqueue a background job to generate a firm-wide report."""
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
            "run_firm_report",
            job_ctx.model_dump(),
            body.report_type,
            body.filters,
            _job_timeout=1800,
        )

        return FirmWideJobAccepted(
            job_id=job.job_id,
            report_type=body.report_type,
            status="accepted",
            message=f"Firm-wide {body.report_type} report generation enqueued",
        )
    except Exception as exc:
        logger.exception("Failed to enqueue firm-wide report job")
        raise InternalHTTPError(str(exc), ctx.request_id) from exc


@router.post("/narrative")
async def generate_report_narrative(
    body: ReportNarrativeRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> ReportNarrativeResponse:
    """Run the firm reporter agent to generate a narrative for a report snapshot."""
    try:
        import json

        from app.agents.base_deps import AgentDeps
        from app.agents.firm_reporter import firm_reporter_agent

        deps = AgentDeps(
            platform=platform,
            access_scope=ctx.access_scope,
            tenant_id=ctx.tenant_id,
            actor_id=ctx.actor_id,
        )

        prompt = (
            f"Generate a narrative report for report ID {body.report_id}.\n"
            f"Tone: {body.tone}\n"
            f"Maximum length: {body.max_length_words} words\n\n"
            f"Data snapshot:\n{json.dumps(body.snapshot, indent=2)}"
        )

        result = await firm_reporter_agent.run(prompt, deps=deps)
        now = datetime.now(UTC).isoformat()

        # Build a narrative from the structured report
        narrative_parts = []
        if result.data.highlights:
            narrative_parts.append("Highlights: " + "; ".join(result.data.highlights))
        if result.data.concerns:
            narrative_parts.append("Concerns: " + "; ".join(result.data.concerns))
        narrative = (
            "\n\n".join(narrative_parts)
            if narrative_parts
            else str(result.data.model_dump())
        )

        return ReportNarrativeResponse(
            report_id=body.report_id,
            narrative=narrative,
            word_count=len(narrative.split()),
            as_of=now,
        )
    except Exception as exc:
        logger.exception("Report narrative generation failed")
        raise ModelProviderHTTPError(str(exc), ctx.request_id) from exc
