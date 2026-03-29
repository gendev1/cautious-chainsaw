"""app/routers/portfolio.py -- Portfolio analysis endpoints."""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from langfuse import Langfuse
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.context import RequestContext
from app.dependencies import get_langfuse, get_platform_client, get_redis, get_request_context
from app.models.schemas import PortfolioAnalysis
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
        return result.data
    except Exception as exc:
        logger.exception("Portfolio analysis failed")
        raise ModelProviderHTTPError(str(exc), ctx.request_id) from exc
