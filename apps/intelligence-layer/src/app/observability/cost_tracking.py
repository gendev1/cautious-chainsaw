"""app/observability/cost_tracking.py — Redis cost aggregation."""
from __future__ import annotations

import datetime
from decimal import Decimal

from redis.asyncio import Redis

SECONDS_IN_DAY = 86400
SECONDS_IN_32_DAYS = 86400 * 32


def _daily_cost_key(tenant_id: str) -> str:
    today = datetime.date.today().isoformat()
    return f"sidecar:cost:daily:{tenant_id}:{today}"


def _monthly_cost_key(tenant_id: str) -> str:
    month = datetime.date.today().strftime("%Y-%m")
    return f"sidecar:cost:monthly:{tenant_id}:{month}"


async def record_cost(
    redis: Redis,  # type: ignore[type-arg]
    tenant_id: str,
    cost: Decimal,
) -> None:
    micro_dollars = int(cost * 1_000_000)
    daily_key = _daily_cost_key(tenant_id)
    monthly_key = _monthly_cost_key(tenant_id)
    pipe = redis.pipeline()
    pipe.incrby(daily_key, micro_dollars)
    pipe.expire(daily_key, SECONDS_IN_DAY * 2)
    pipe.incrby(monthly_key, micro_dollars)
    pipe.expire(monthly_key, SECONDS_IN_32_DAYS)
    await pipe.execute()


async def get_daily_cost(
    redis: Redis,  # type: ignore[type-arg]
    tenant_id: str,
) -> Decimal:
    val = await redis.get(_daily_cost_key(tenant_id))
    if val:
        return Decimal(int(val)) / Decimal(1_000_000)
    return Decimal(0)


async def get_monthly_cost(
    redis: Redis,  # type: ignore[type-arg]
    tenant_id: str,
) -> Decimal:
    val = await redis.get(_monthly_cost_key(tenant_id))
    if val:
        return Decimal(int(val)) / Decimal(1_000_000)
    return Decimal(0)
