"""app/observability/token_budget.py — Redis token budget."""
from __future__ import annotations

import datetime

from redis.asyncio import Redis

SECONDS_IN_DAY = 86400


def _budget_key(prefix: str, tenant_id: str) -> str:
    today = datetime.date.today().isoformat()
    return f"{prefix}:{tenant_id}:{today}"


async def get_tokens_used(
    redis: Redis,  # type: ignore[type-arg]
    prefix: str,
    tenant_id: str,
) -> int:
    key = _budget_key(prefix, tenant_id)
    val = await redis.get(key)
    return int(val) if val else 0


async def increment_tokens(
    redis: Redis,  # type: ignore[type-arg]
    prefix: str,
    tenant_id: str,
    tokens: int,
) -> int:
    key = _budget_key(prefix, tenant_id)
    pipe = redis.pipeline()
    pipe.incrby(key, tokens)
    pipe.expire(key, SECONDS_IN_DAY * 2)
    results = await pipe.execute()
    return results[0]


async def check_budget(
    redis: Redis,  # type: ignore[type-arg]
    prefix: str,
    tenant_id: str,
    limit: int,
) -> tuple[bool, int]:
    used = await get_tokens_used(redis, prefix, tenant_id)
    return used < limit, used
