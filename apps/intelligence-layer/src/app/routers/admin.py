"""app/routers/admin.py — Internal admin endpoints."""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.config import get_settings
from app.observability.cost_tracking import (
    get_daily_cost,
    get_monthly_cost,
)
from app.observability.token_budget import get_tokens_used

router = APIRouter(prefix="/internal/admin", tags=["admin"])


class TenantCostResponse(BaseModel):
    tenant_id: str
    daily_cost_usd: Decimal
    monthly_cost_usd: Decimal
    daily_tokens_used: int
    daily_token_limit: int


@router.get(
    "/cost/{tenant_id}",
    response_model=TenantCostResponse,
)
async def tenant_cost(
    tenant_id: str,
    request: Request,
) -> TenantCostResponse:
    redis = request.app.state.redis
    settings = get_settings()
    daily_cost = await get_daily_cost(redis, tenant_id)
    monthly_cost = await get_monthly_cost(redis, tenant_id)
    tokens_used = await get_tokens_used(
        redis,
        settings.token_budget_redis_prefix,
        tenant_id,
    )
    return TenantCostResponse(
        tenant_id=tenant_id,
        daily_cost_usd=daily_cost,
        monthly_cost_usd=monthly_cost,
        daily_tokens_used=tokens_used,
        daily_token_limit=settings.default_daily_token_limit,
    )
