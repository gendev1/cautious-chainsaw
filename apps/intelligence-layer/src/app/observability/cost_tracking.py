"""
app/observability/cost_tracking.py — Redis cost aggregation with
per-model and per-agent granularity.

Enhanced with patterns from Claude Code's cost-tracker.ts:
- Per-model token usage tracking (input/output/cache tokens)
- Per-agent cost attribution
- Request duration tracking
- Cache hit/miss statistics
"""
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from decimal import Decimal

from redis.asyncio import Redis

logger = logging.getLogger("sidecar.cost_tracking")

SECONDS_IN_DAY = 86400
SECONDS_IN_32_DAYS = 86400 * 32


# ---------------------------------------------------------------------------
# Per-model usage tracking (ported from claudecode/cost-tracker.ts)
# ---------------------------------------------------------------------------

@dataclass
class ModelUsage:
    """Token usage and cost for a specific model.

    Mirrors Claude Code's ModelUsage type with input/output/cache
    token tracking per model.
    """

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: Decimal = Decimal(0)
    request_count: int = 0
    total_duration_ms: float = 0.0


@dataclass
class AgentUsage:
    """Cost and token usage attributed to a specific agent."""

    agent_name: str
    model_usage: dict[str, ModelUsage] = field(default_factory=dict)
    tool_calls: int = 0
    total_duration_ms: float = 0.0

    @property
    def total_cost_usd(self) -> Decimal:
        return sum(
            (mu.cost_usd for mu in self.model_usage.values()),
            Decimal(0),
        )

    @property
    def total_tokens(self) -> int:
        return sum(
            mu.input_tokens + mu.output_tokens
            for mu in self.model_usage.values()
        )


@dataclass
class RequestCostSummary:
    """Full cost summary for a single request."""

    tenant_id: str
    request_id: str
    total_cost_usd: Decimal = Decimal(0)
    agent_usage: dict[str, AgentUsage] = field(default_factory=dict)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    wall_duration_ms: float = 0.0


# ---------------------------------------------------------------------------
# Key builders
# ---------------------------------------------------------------------------

def _daily_cost_key(tenant_id: str) -> str:
    today = datetime.date.today().isoformat()
    return f"sidecar:cost:daily:{tenant_id}:{today}"


def _monthly_cost_key(tenant_id: str) -> str:
    month = datetime.date.today().strftime("%Y-%m")
    return f"sidecar:cost:monthly:{tenant_id}:{month}"


def _model_usage_key(tenant_id: str, model: str) -> str:
    today = datetime.date.today().isoformat()
    return f"sidecar:cost:model:{tenant_id}:{model}:{today}"


def _agent_usage_key(tenant_id: str, agent_name: str) -> str:
    today = datetime.date.today().isoformat()
    return f"sidecar:cost:agent:{tenant_id}:{agent_name}:{today}"


# ---------------------------------------------------------------------------
# Recording functions
# ---------------------------------------------------------------------------

async def record_cost(
    redis: Redis,  # type: ignore[type-arg]
    tenant_id: str,
    cost: Decimal,
) -> None:
    """Record aggregate cost (backwards compatible)."""
    micro_dollars = int(cost * 1_000_000)
    daily_key = _daily_cost_key(tenant_id)
    monthly_key = _monthly_cost_key(tenant_id)
    pipe = redis.pipeline()
    pipe.incrby(daily_key, micro_dollars)
    pipe.expire(daily_key, SECONDS_IN_DAY * 2)
    pipe.incrby(monthly_key, micro_dollars)
    pipe.expire(monthly_key, SECONDS_IN_32_DAYS)
    await pipe.execute()


async def record_model_usage(
    redis: Redis,  # type: ignore[type-arg]
    tenant_id: str,
    usage: ModelUsage,
) -> None:
    """Record per-model token usage and cost.

    Ported from Claude Code's addToTotalSessionCost / addToTotalModelUsage.
    """
    key = _model_usage_key(tenant_id, usage.model)
    pipe = redis.pipeline()
    pipe.hincrby(key, "input_tokens", usage.input_tokens)
    pipe.hincrby(key, "output_tokens", usage.output_tokens)
    pipe.hincrby(key, "cache_read_tokens", usage.cache_read_tokens)
    pipe.hincrby(key, "cache_creation_tokens", usage.cache_creation_tokens)
    pipe.hincrby(key, "cost_micro_usd", int(usage.cost_usd * 1_000_000))
    pipe.hincrby(key, "request_count", usage.request_count)
    pipe.expire(key, SECONDS_IN_DAY * 2)
    await pipe.execute()

    # Also record to aggregate daily/monthly
    await record_cost(redis, tenant_id, usage.cost_usd)


async def record_agent_usage(
    redis: Redis,  # type: ignore[type-arg]
    tenant_id: str,
    agent_name: str,
    cost_usd: Decimal,
    input_tokens: int = 0,
    output_tokens: int = 0,
    tool_calls: int = 0,
    duration_ms: float = 0.0,
) -> None:
    """Record per-agent cost attribution."""
    key = _agent_usage_key(tenant_id, agent_name)
    pipe = redis.pipeline()
    pipe.hincrby(key, "cost_micro_usd", int(cost_usd * 1_000_000))
    pipe.hincrby(key, "input_tokens", input_tokens)
    pipe.hincrby(key, "output_tokens", output_tokens)
    pipe.hincrby(key, "tool_calls", tool_calls)
    pipe.hincrbyfloat(key, "duration_ms", duration_ms)
    pipe.hincrby(key, "invocation_count", 1)
    pipe.expire(key, SECONDS_IN_DAY * 2)
    await pipe.execute()


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------

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


async def get_model_usage(
    redis: Redis,  # type: ignore[type-arg]
    tenant_id: str,
    model: str,
) -> ModelUsage | None:
    """Retrieve per-model usage stats for today."""
    key = _model_usage_key(tenant_id, model)
    data = await redis.hgetall(key)  # type: ignore[misc]
    if not data:
        return None
    return ModelUsage(
        model=model,
        input_tokens=int(data.get("input_tokens", 0)),
        output_tokens=int(data.get("output_tokens", 0)),
        cache_read_tokens=int(data.get("cache_read_tokens", 0)),
        cache_creation_tokens=int(data.get("cache_creation_tokens", 0)),
        cost_usd=Decimal(int(data.get("cost_micro_usd", 0))) / Decimal(1_000_000),
        request_count=int(data.get("request_count", 0)),
    )


async def get_agent_usage(
    redis: Redis,  # type: ignore[type-arg]
    tenant_id: str,
    agent_name: str,
) -> dict | None:
    """Retrieve per-agent usage stats for today."""
    key = _agent_usage_key(tenant_id, agent_name)
    data = await redis.hgetall(key)  # type: ignore[misc]
    if not data:
        return None
    return {
        "agent_name": agent_name,
        "cost_usd": Decimal(int(data.get("cost_micro_usd", 0))) / Decimal(1_000_000),
        "input_tokens": int(data.get("input_tokens", 0)),
        "output_tokens": int(data.get("output_tokens", 0)),
        "tool_calls": int(data.get("tool_calls", 0)),
        "duration_ms": float(data.get("duration_ms", 0)),
        "invocation_count": int(data.get("invocation_count", 0)),
    }


async def get_cost_breakdown(
    redis: Redis,  # type: ignore[type-arg]
    tenant_id: str,
    models: list[str] | None = None,
    agents: list[str] | None = None,
) -> dict:
    """Get a full cost breakdown for today.

    Mirrors Claude Code's formatTotalCost() output structure.
    """
    daily = await get_daily_cost(redis, tenant_id)
    monthly = await get_monthly_cost(redis, tenant_id)

    result: dict = {
        "daily_cost_usd": str(daily),
        "monthly_cost_usd": str(monthly),
        "model_usage": {},
        "agent_usage": {},
    }

    if models:
        for model in models:
            usage = await get_model_usage(redis, tenant_id, model)
            if usage:
                result["model_usage"][model] = {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_tokens": usage.cache_read_tokens,
                    "cost_usd": str(usage.cost_usd),
                    "request_count": usage.request_count,
                }

    if agents:
        for agent_name in agents:
            usage = await get_agent_usage(redis, tenant_id, agent_name)
            if usage:
                result["agent_usage"][agent_name] = usage

    return result
