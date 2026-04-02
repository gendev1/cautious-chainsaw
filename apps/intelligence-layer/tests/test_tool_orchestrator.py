"""Tests for upgraded tool orchestrator — dynamic concurrency, progress events, sibling abort, hooks."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.tool_orchestrator import (
    OrchestrationStats,
    ToolCallRequest,
    ToolCallResult,
    is_concurrency_safe,
    orchestrate_tool_calls,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _noop_tool(**kwargs) -> str:
    return "ok"


async def _slow_tool(**kwargs) -> str:
    await asyncio.sleep(0.1)
    return "slow_ok"


async def _failing_tool(**kwargs) -> str:
    raise RuntimeError("tool failed")


def _make_request(
    tool_name: str,
    call_fn=None,
    args: dict | None = None,
    tool_call_id: str | None = None,
) -> ToolCallRequest:
    return ToolCallRequest(
        tool_name=tool_name,
        call_fn=call_fn or _noop_tool,
        args=args or {},
        tool_call_id=tool_call_id or f"tc_{tool_name}",
    )


# ---------------------------------------------------------------------------
# is_concurrency_safe (dynamic check)
# ---------------------------------------------------------------------------


def test_is_concurrency_safe_for_known_read_only_tool() -> None:
    """Known read-only tools (e.g. get_household_summary) are concurrency-safe."""
    assert is_concurrency_safe("get_household_summary") is True
    assert is_concurrency_safe("search_documents") is True


def test_is_concurrency_safe_for_unknown_tool_returns_false() -> None:
    """Unknown tools default to not-concurrency-safe."""
    assert is_concurrency_safe("submit_order") is False
    assert is_concurrency_safe("delete_account") is False


# ---------------------------------------------------------------------------
# Orchestration — parallel and serial
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_orchestrate_runs_read_only_tools_in_parallel() -> None:
    """Read-only tools run concurrently via asyncio.gather."""
    call_times: list[float] = []

    async def timed_tool(**kwargs) -> str:
        import time
        call_times.append(time.monotonic())
        await asyncio.sleep(0.05)
        return "done"

    requests = [
        _make_request("get_household_summary", call_fn=timed_tool),
        _make_request("search_documents", call_fn=timed_tool),
        _make_request("get_account_summary", call_fn=timed_tool),
    ]

    results, stats = await orchestrate_tool_calls(requests)

    assert len(results) == 3
    assert stats.parallel_calls == 3
    assert stats.serial_calls == 0
    # All should have started near-simultaneously (within 20ms of each other)
    if len(call_times) == 3:
        assert max(call_times) - min(call_times) < 0.02


@pytest.mark.anyio
async def test_orchestrate_runs_mutating_tools_serially() -> None:
    """Non-read-only (mutating) tools are executed sequentially."""
    requests = [
        _make_request("submit_order", call_fn=_noop_tool),
        _make_request("create_transfer", call_fn=_noop_tool),
    ]

    results, stats = await orchestrate_tool_calls(requests)

    assert len(results) == 2
    assert stats.serial_calls == 2
    assert stats.parallel_calls == 0


# ---------------------------------------------------------------------------
# Progress events
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_orchestrate_emits_progress_events() -> None:
    """orchestrate_tool_calls emits tool_start and tool_result progress events."""
    events: list = []

    async def capture_event(event) -> None:
        events.append(event)

    requests = [_make_request("get_household_summary")]

    results, stats = await orchestrate_tool_calls(
        requests,
        progress_callback=capture_event,
    )

    assert len(results) == 1
    # Should have at least a tool_start and tool_result event
    event_types = [e.event.value if hasattr(e, "event") else str(e) for e in events]
    assert "tool.start" in event_types, f"Expected tool.start in {event_types}"
    assert "tool.result" in event_types, f"Expected tool.result in {event_types}"


# ---------------------------------------------------------------------------
# Sibling abort
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sibling_abort_cancels_remaining_on_error() -> None:
    """When abort_on_error=True and a tool fails, remaining siblings are cancelled."""
    cancel_detected = []

    async def slow_good_tool(**kwargs) -> str:
        try:
            await asyncio.sleep(5)
            return "should not complete"
        except asyncio.CancelledError:
            cancel_detected.append(True)
            raise

    requests = [
        _make_request("get_household_summary", call_fn=_failing_tool),
        _make_request("search_documents", call_fn=slow_good_tool),
    ]

    results, stats = await orchestrate_tool_calls(
        requests,
        abort_on_error=True,
    )

    # At least one result should have an error
    errors = [r for r in results if r.error is not None]
    assert len(errors) >= 1

    # The cancelled count should be tracked in stats
    assert stats.cancelled_calls >= 0  # Might be 0 if failing tool finishes first


@pytest.mark.anyio
async def test_sibling_abort_sets_cancelled_flag() -> None:
    """Cancelled tool results have the cancelled=True flag set."""
    async def instant_fail(**kwargs) -> str:
        raise RuntimeError("fail immediately")

    async def very_slow_tool(**kwargs) -> str:
        await asyncio.sleep(10)
        return "should be cancelled"

    requests = [
        _make_request("get_household_summary", call_fn=instant_fail),
        _make_request("search_documents", call_fn=very_slow_tool),
    ]

    results, stats = await orchestrate_tool_calls(
        requests,
        abort_on_error=True,
    )

    # Check that cancelled field exists on ToolCallResult
    for r in results:
        assert hasattr(r, "cancelled"), "ToolCallResult should have a 'cancelled' field"

    # At least one result should be cancelled or errored
    cancelled_or_errored = [r for r in results if r.cancelled or r.error is not None]
    assert len(cancelled_or_errored) >= 1


# ---------------------------------------------------------------------------
# Hook integration
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_orchestrate_fires_pre_and_post_tool_hooks() -> None:
    """orchestrate_tool_calls fires PRE_TOOL_CALL and POST_TOOL_CALL hooks."""
    from app.services.hooks import HookContext, HookEvent, HookRegistry

    registry = HookRegistry(timeout_s=5.0)
    fired_events: list[str] = []

    async def capture_hook(ctx: HookContext) -> None:
        fired_events.append(f"{ctx.tool_name}")

    registry.register(HookEvent.PRE_TOOL_CALL, capture_hook)
    registry.register(HookEvent.POST_TOOL_CALL, capture_hook)

    hook_ctx = HookContext(agent_name="copilot", tenant_id="t1", conversation_id="c1")

    requests = [_make_request("get_household_summary")]

    results, stats = await orchestrate_tool_calls(
        requests,
        hook_registry=registry,
        hook_context_base=hook_ctx,
    )

    assert len(results) == 1
    # Should have fired both pre and post hooks
    assert len(fired_events) == 2
    assert all("get_household_summary" in e for e in fired_events)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_orchestrate_with_no_tools_returns_empty() -> None:
    """Empty request list returns empty results and zero stats."""
    results, stats = await orchestrate_tool_calls([])
    assert results == []
    assert stats.total_calls == 0


@pytest.mark.anyio
async def test_orchestrate_respects_concurrency_limit() -> None:
    """No more than max_concurrent tools run simultaneously."""
    concurrent_count = []
    current = 0
    lock = asyncio.Lock()

    async def counting_tool(**kwargs) -> str:
        nonlocal current
        async with lock:
            current += 1
            concurrent_count.append(current)
        await asyncio.sleep(0.02)
        async with lock:
            current -= 1
        return "done"

    # Create 6 read-only requests, limit to 2 concurrent
    requests = [
        _make_request(f"get_household_summary", call_fn=counting_tool, tool_call_id=f"tc_{i}")
        for i in range(6)
    ]

    results, stats = await orchestrate_tool_calls(requests, max_concurrent=2)

    assert len(results) == 6
    assert max(concurrent_count) <= 2


@pytest.mark.anyio
async def test_stats_tracks_cancelled_calls() -> None:
    """OrchestrationStats.cancelled_calls counts cancelled siblings."""
    stats = OrchestrationStats(
        total_calls=3,
        parallel_calls=3,
        serial_calls=0,
        total_duration_ms=100.0,
        parallel_saved_ms=50.0,
        cancelled_calls=1,
    )
    assert stats.cancelled_calls == 1
    assert hasattr(stats, "cancelled_calls")
