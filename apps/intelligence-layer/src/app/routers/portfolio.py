"""app/routers/portfolio.py -- Portfolio analysis endpoints."""
from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from langfuse import Langfuse
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.context import RequestContext
from app.dependencies import get_langfuse, get_platform_client, get_redis, get_request_context
from app.jobs.enqueue import JobContext, enqueue_portfolio_construction
from app.models.schemas import PortfolioAnalysis
from app.portfolio_construction.events import ProgressEventEmitter
from app.portfolio_construction.models import (
    ConstructPortfolioRequest,
    PortfolioConstructionAccepted,
)
from app.services.platform_client import PlatformClient
from app.utils.errors import ModelProviderHTTPError

logger = logging.getLogger("sidecar.routers.portfolio")

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class PortfolioAnalysisRequest(BaseModel):
    client_id: str = Field(description="Client to analyze portfolio for")
    analysis_types: list[str] = Field(
        default_factory=list,
        description="Types of analysis: allocation, drift, performance, risk",
    )
    account_ids: list[str] = Field(
        default_factory=list,
        description="Specific account IDs to analyze; empty means all client accounts",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/analyze")
async def analyze_portfolio(
    body: PortfolioAnalysisRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> PortfolioAnalysis:
    """Run the portfolio analyst agent to analyze a client's portfolio."""
    try:
        from app.agents.base_deps import AgentDeps
        from app.agents.portfolio_analyst import portfolio_analyst_agent

        deps = AgentDeps(
            platform=platform,
            access_scope=ctx.access_scope,
            tenant_id=ctx.tenant_id,
            actor_id=ctx.actor_id,
        )

        prompt_parts = [
            f"Analyze the portfolio for client {body.client_id}.",
        ]
        if body.analysis_types:
            prompt_parts.append(f"Focus on: {', '.join(body.analysis_types)}.")
        else:
            prompt_parts.append(
                "Provide a comprehensive analysis covering allocation, drift, "
                "performance, and risk."
            )
        if body.account_ids:
            prompt_parts.append(f"Account IDs to analyze: {', '.join(body.account_ids)}.")
        else:
            prompt_parts.append("Analyze all accounts for this client.")

        result = await portfolio_analyst_agent.run("\n".join(prompt_parts), deps=deps)
        return result.output
    except Exception as exc:
        logger.exception("Portfolio analysis failed")
        raise ModelProviderHTTPError(str(exc), ctx.request_id) from exc


# ---------------------------------------------------------------------------
# Portfolio Construction v2
# ---------------------------------------------------------------------------


@router.post("/construct", status_code=202)
async def construct_portfolio(
    body: ConstructPortfolioRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
    conversation_id: str | None = None,
) -> PortfolioConstructionAccepted:
    """Enqueue a portfolio construction job and return 202 with job_id.

    If conversation_id is provided, the job_id is stored in the
    conversation state so the copilot can reference it in follow-up chat.
    """
    job_ctx = JobContext(
        tenant_id=ctx.tenant_id,
        actor_id=ctx.actor_id,
        actor_type=ctx.actor_type,
        request_id=ctx.request_id,
        access_scope=ctx.access_scope.model_dump() if ctx.access_scope else {},
    )
    job_id = await enqueue_portfolio_construction(job_ctx, body)

    # Store portfolio job_id in conversation state for chat follow-up
    if conversation_id:
        from app.services.conversation_memory import ConversationMemory
        memory = ConversationMemory(redis)
        state = await memory.load_state(
            ctx.tenant_id, ctx.actor_id, conversation_id,
        )
        messages = await memory.load(
            ctx.tenant_id, ctx.actor_id, conversation_id,
        )
        await memory.save(
            ctx.tenant_id, ctx.actor_id, conversation_id,
            messages,
            extra_state={"active_portfolio_job_id": job_id},
        )

    return PortfolioConstructionAccepted(job_id=job_id)


@router.get("/jobs/{job_id}")
async def get_job_status(
    job_id: str,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> dict:
    """Get the status and optionally the result of a portfolio construction job."""
    emitter = ProgressEventEmitter(redis)
    status = await emitter.get_job_status(job_id)

    result_data = None
    if status == "job_completed":
        raw = await redis.get(f"sidecar:portfolio:result:{job_id}")
        if raw:
            result_data = json.loads(raw)

    return {
        "job_id": job_id,
        "status": status,
        "result": result_data,
    }


@router.get("/jobs/{job_id}/events")
async def get_job_events(
    job_id: str,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> StreamingResponse:
    """Stream progress events for a portfolio construction job (SSE)."""
    emitter = ProgressEventEmitter(redis)
    events = await emitter.read_events(job_id)

    async def event_generator():
        for event in events:
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )
