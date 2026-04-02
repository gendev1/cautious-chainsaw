"""
app/middleware/cost_tracking.py — Per-request cost accumulation middleware.

Ported from Claude Code's cost-tracker.ts pattern: accumulates model usage,
agent usage, and tool call counts in-memory during a request, then flushes
to Redis once at request end.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.observability.cost_tracking import (
    ModelUsage,
    record_agent_usage,
    record_model_usage,
)

logger = logging.getLogger("sidecar.cost_middleware")


@dataclass
class RequestCostAccumulator:
    """In-memory cost accumulator for a single request.

    Collects model usage, agent usage, and tool call counts without
    hitting Redis on every increment. Flushed once at request end.
    """

    tenant_id: str
    request_id: str
    _model_usages: list[ModelUsage] = field(default_factory=list)
    _agent_costs: dict[str, Decimal] = field(default_factory=dict)
    _agent_input_tokens: dict[str, int] = field(default_factory=dict)
    _agent_output_tokens: dict[str, int] = field(default_factory=dict)
    _agent_tool_calls: dict[str, int] = field(default_factory=dict)
    _agent_durations: dict[str, float] = field(default_factory=dict)
    _total_cost_usd: Decimal = Decimal(0)
    _total_input_tokens: int = 0
    _total_output_tokens: int = 0
    _total_cache_read_tokens: int = 0
    _flushed: bool = False
    wall_start_ms: float = field(default_factory=lambda: time.monotonic() * 1000)

    def record_model_usage(self, usage: ModelUsage) -> None:
        """Accumulate model usage in memory."""
        self._model_usages.append(usage)
        self._total_cost_usd += usage.cost_usd
        self._total_input_tokens += usage.input_tokens
        self._total_output_tokens += usage.output_tokens
        self._total_cache_read_tokens += usage.cache_read_tokens

    def record_agent_run(
        self,
        agent_name: str,
        cost_usd: Decimal,
        input_tokens: int,
        output_tokens: int,
        tool_calls: int,
        duration_ms: float,
    ) -> None:
        """Accumulate agent-level usage in memory."""
        self._agent_costs[agent_name] = (
            self._agent_costs.get(agent_name, Decimal(0)) + cost_usd
        )
        self._agent_input_tokens[agent_name] = (
            self._agent_input_tokens.get(agent_name, 0) + input_tokens
        )
        self._agent_output_tokens[agent_name] = (
            self._agent_output_tokens.get(agent_name, 0) + output_tokens
        )
        self._agent_tool_calls[agent_name] = (
            self._agent_tool_calls.get(agent_name, 0) + tool_calls
        )
        self._agent_durations[agent_name] = (
            self._agent_durations.get(agent_name, 0.0) + duration_ms
        )
        self._total_cost_usd += cost_usd
        self._total_input_tokens += input_tokens
        self._total_output_tokens += output_tokens

    async def flush(self, redis: Any) -> None:
        """Write all accumulated usage to Redis in a single pipeline batch."""
        if self._flushed:
            return
        self._flushed = True

        # Flush model usages
        for usage in self._model_usages:
            await record_model_usage(redis, self.tenant_id, usage)

        # Flush agent usages
        for agent_name in self._agent_costs:
            await record_agent_usage(
                redis,
                self.tenant_id,
                agent_name,
                cost_usd=self._agent_costs[agent_name],
                input_tokens=self._agent_input_tokens.get(agent_name, 0),
                output_tokens=self._agent_output_tokens.get(agent_name, 0),
                tool_calls=self._agent_tool_calls.get(agent_name, 0),
                duration_ms=self._agent_durations.get(agent_name, 0.0),
            )

    def summary(self) -> dict[str, Any]:
        """Return a summary dict suitable for SSE cost.update event."""
        return {
            "tenant_id": self.tenant_id,
            "request_id": self.request_id,
            "total_cost_usd": str(self._total_cost_usd),
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "total_cache_read_tokens": self._total_cache_read_tokens,
            "wall_duration_ms": round(
                time.monotonic() * 1000 - self.wall_start_ms, 1
            ),
        }


def get_cost_accumulator(request: Request) -> RequestCostAccumulator | None:
    """Retrieve the cost accumulator from request.state."""
    return getattr(request.state, "cost_accumulator", None)


class CostTrackingMiddleware(BaseHTTPMiddleware):
    """Attaches a RequestCostAccumulator to each request and flushes on response."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        # Extract tenant and request IDs from headers
        tenant_id = request.headers.get("X-Tenant-ID", "unknown")
        request_id = request.headers.get("X-Request-ID", "unknown")

        accumulator = RequestCostAccumulator(
            tenant_id=tenant_id,
            request_id=request_id,
        )
        request.state.cost_accumulator = accumulator

        response = await call_next(request)

        # Flush accumulated costs to Redis
        redis = getattr(request.app.state, "redis", None)
        if redis is not None:
            try:
                await accumulator.flush(redis)
            except Exception:
                logger.exception("cost_flush_error request_id=%s", request_id)

        return response
