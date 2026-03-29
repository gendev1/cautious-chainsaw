"""app/middleware/token_budget.py — Token budget enforcement."""
from __future__ import annotations

import datetime

from fastapi import HTTPException, Request

from app.config import get_settings
from app.observability.token_budget import check_budget


async def enforce_token_budget(request: Request) -> None:
    ctx = getattr(request.state, "context", None)
    if ctx is None:
        return

    settings = get_settings()
    redis = request.app.state.redis
    limit = settings.default_daily_token_limit

    allowed, used = await check_budget(
        redis,
        settings.token_budget_redis_prefix,
        ctx.tenant_id,
        limit,
    )
    if not allowed:
        now = datetime.datetime.now(datetime.UTC)
        midnight = (now + datetime.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        retry_after = int((midnight - now).total_seconds())
        raise HTTPException(
            status_code=429,
            detail={
                "error": "token_budget_exceeded",
                "tenant_id": ctx.tenant_id,
                "tokens_used": used,
                "daily_limit": limit,
            },
            headers={"Retry-After": str(retry_after)},
        )
