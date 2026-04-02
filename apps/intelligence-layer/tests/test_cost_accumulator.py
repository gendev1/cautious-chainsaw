"""Tests for RequestCostAccumulator and CostTrackingMiddleware."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import pytest

from app.middleware.cost_tracking import (
    CostTrackingMiddleware,
    RequestCostAccumulator,
    get_cost_accumulator,
)
from app.observability.cost_tracking import ModelUsage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_accumulator(
    tenant_id: str = "t1",
    request_id: str = "req-001",
) -> RequestCostAccumulator:
    return RequestCostAccumulator(tenant_id=tenant_id, request_id=request_id)


def _make_model_usage(
    model: str = "anthropic:claude-sonnet-4-6",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cost_usd: Decimal = Decimal("0.001"),
) -> ModelUsage:
    return ModelUsage(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        request_count=1,
    )


# ---------------------------------------------------------------------------
# In-memory accumulation
# ---------------------------------------------------------------------------


def test_accumulator_records_model_usage_in_memory() -> None:
    """A single model usage is accumulated in memory without Redis."""
    acc = _make_accumulator()
    usage = _make_model_usage(input_tokens=100, output_tokens=50, cost_usd=Decimal("0.005"))

    acc.record_model_usage(usage)

    summary = acc.summary()
    assert summary["total_input_tokens"] == 100
    assert summary["total_output_tokens"] == 50
    assert Decimal(summary["total_cost_usd"]) == Decimal("0.005")


def test_accumulator_records_multiple_model_usages() -> None:
    """Multiple model usages are accumulated correctly."""
    acc = _make_accumulator()

    acc.record_model_usage(_make_model_usage(
        model="anthropic:claude-sonnet-4-6",
        input_tokens=100,
        output_tokens=50,
        cost_usd=Decimal("0.005"),
    ))
    acc.record_model_usage(_make_model_usage(
        model="anthropic:claude-haiku-4-5",
        input_tokens=200,
        output_tokens=100,
        cost_usd=Decimal("0.002"),
    ))

    summary = acc.summary()
    assert summary["total_input_tokens"] == 300
    assert summary["total_output_tokens"] == 150
    assert Decimal(summary["total_cost_usd"]) == Decimal("0.007")


def test_accumulator_records_agent_run() -> None:
    """Agent run data is accumulated in memory."""
    acc = _make_accumulator()

    acc.record_agent_run(
        agent_name="copilot",
        cost_usd=Decimal("0.01"),
        input_tokens=500,
        output_tokens=200,
        tool_calls=3,
        duration_ms=1500.0,
    )

    summary = acc.summary()
    assert summary["total_input_tokens"] == 500
    assert summary["total_output_tokens"] == 200
    assert Decimal(summary["total_cost_usd"]) == Decimal("0.01")


# ---------------------------------------------------------------------------
# Flush to Redis
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_accumulator_flush_writes_to_redis_pipeline() -> None:
    """flush() writes all accumulated usage to Redis in a batch."""
    redis = fakeredis.aioredis.FakeRedis()
    acc = _make_accumulator(tenant_id="t1", request_id="req-001")

    acc.record_model_usage(_make_model_usage(
        input_tokens=100,
        output_tokens=50,
        cost_usd=Decimal("0.005"),
    ))
    acc.record_agent_run(
        agent_name="copilot",
        cost_usd=Decimal("0.005"),
        input_tokens=100,
        output_tokens=50,
        tool_calls=2,
        duration_ms=1000.0,
    )

    await acc.flush(redis)

    # Verify something was written to Redis (exact keys depend on implementation)
    # At minimum, the daily cost key should exist
    keys = [k async for k in redis.scan_iter("sidecar:cost:*")]
    assert len(keys) > 0, "flush should write cost data to Redis"


@pytest.mark.anyio
async def test_accumulator_flush_is_idempotent() -> None:
    """Calling flush() twice does not double-write."""
    redis = fakeredis.aioredis.FakeRedis()
    acc = _make_accumulator()

    acc.record_model_usage(_make_model_usage(cost_usd=Decimal("0.005")))

    await acc.flush(redis)
    await acc.flush(redis)

    # After two flushes, the cost should not be doubled
    # The accumulator should clear its state after the first flush
    # or be idempotent in some other way
    from app.observability.cost_tracking import get_daily_cost

    cost = await get_daily_cost(redis, "t1")
    assert cost == Decimal("0.005"), "flush should be idempotent"


def test_accumulator_summary_returns_correct_totals() -> None:
    """summary() returns a dict with all expected fields."""
    acc = _make_accumulator()
    acc.record_model_usage(_make_model_usage(
        input_tokens=100,
        output_tokens=50,
        cost_usd=Decimal("0.005"),
    ))

    summary = acc.summary()

    assert "total_cost_usd" in summary
    assert "total_input_tokens" in summary
    assert "total_output_tokens" in summary
    assert "tenant_id" in summary
    assert "request_id" in summary
    assert summary["tenant_id"] == "t1"
    assert summary["request_id"] == "req-001"


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_middleware_attaches_accumulator_to_request() -> None:
    """CostTrackingMiddleware attaches a RequestCostAccumulator to request.state."""
    from starlette.testclient import TestClient
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    captured_accumulator = []

    async def check_endpoint(request: Request) -> JSONResponse:
        acc = get_cost_accumulator(request)
        captured_accumulator.append(acc)
        return JSONResponse({"ok": True})

    app = Starlette(
        routes=[Route("/test", check_endpoint)],
    )
    app.add_middleware(CostTrackingMiddleware)

    # Need to set up request.state requirements the middleware expects
    # The middleware will look for tenant_id and request_id on the request
    client = TestClient(app)
    response = client.get("/test", headers={"X-Tenant-ID": "t1", "X-Request-ID": "req-1"})

    assert response.status_code == 200
    assert len(captured_accumulator) == 1
    assert captured_accumulator[0] is not None
    assert isinstance(captured_accumulator[0], RequestCostAccumulator)


@pytest.mark.anyio
async def test_middleware_flushes_on_response() -> None:
    """CostTrackingMiddleware flushes the accumulator when the response completes."""
    # This test verifies that flush is called during response finalization.
    # Since the middleware needs Redis on app.state, we use a mock.
    from starlette.testclient import TestClient
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    flush_called = []

    async def endpoint(request: Request) -> JSONResponse:
        acc = get_cost_accumulator(request)
        if acc:
            acc.record_model_usage(_make_model_usage(cost_usd=Decimal("0.01")))
            # Monkey-patch flush to track if it gets called
            original_flush = acc.flush
            async def tracked_flush(redis):
                flush_called.append(True)
                return await original_flush(redis)
            acc.flush = tracked_flush
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/test", endpoint)])
    app.add_middleware(CostTrackingMiddleware)

    # Set up a fake Redis on app.state
    app.state.redis = fakeredis.aioredis.FakeRedis()

    client = TestClient(app)
    client.get("/test", headers={"X-Tenant-ID": "t1", "X-Request-ID": "req-1"})

    assert len(flush_called) > 0, "Middleware should flush accumulator on response"


# ---------------------------------------------------------------------------
# get_cost_accumulator without middleware
# ---------------------------------------------------------------------------


def test_get_cost_accumulator_returns_none_without_middleware() -> None:
    """get_cost_accumulator returns None when no middleware has set it up."""
    mock_request = MagicMock()
    mock_request.state = MagicMock(spec=[])  # no attributes

    result = get_cost_accumulator(mock_request)
    assert result is None
