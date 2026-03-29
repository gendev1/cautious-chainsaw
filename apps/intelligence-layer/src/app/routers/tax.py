"""app/routers/tax.py -- Tax planning analysis endpoints."""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from langfuse import Langfuse
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.context import RequestContext
from app.dependencies import get_langfuse, get_platform_client, get_redis, get_request_context
from app.models.schemas import TaxPlan
from app.services.platform_client import PlatformClient
from app.utils.errors import ModelProviderHTTPError

logger = logging.getLogger("sidecar.routers.tax")

router = APIRouter(prefix="/tax", tags=["tax"])

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class TaxPlanRequest(BaseModel):
    client_id: str = Field(description="Client to generate tax plan for")
    tax_year: int = Field(description="Tax year to analyze")
    include_scenarios: bool = Field(
        default=True, description="Whether to include scenario modeling"
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/plan")
async def generate_tax_plan(
    body: TaxPlanRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> TaxPlan:
    """Run the tax planner agent to generate a tax optimization analysis."""
    try:
        from app.agents.base_deps import AgentDeps
        from app.agents.tax_planner import tax_planner_agent

        deps = AgentDeps(
            platform=platform,
            access_scope=ctx.access_scope,
            tenant_id=ctx.tenant_id,
            actor_id=ctx.actor_id,
        )

        prompt_parts = [
            f"Generate a tax planning analysis for client {body.client_id}.",
            f"Tax year: {body.tax_year}.",
        ]
        if body.include_scenarios:
            prompt_parts.append(
                "Include scenario modeling comparing at least two optimization strategies."
            )
        else:
            prompt_parts.append(
                "Focus on current situation and opportunities without detailed scenario modeling."
            )

        result = await tax_planner_agent.run("\n".join(prompt_parts), deps=deps)
        return result.data
    except Exception as exc:
        logger.exception("Tax plan generation failed")
        raise ModelProviderHTTPError(str(exc), ctx.request_id) from exc
