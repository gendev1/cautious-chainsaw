"""Tests for the hook system — lifecycle event registry and dispatch."""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from app.services.hooks import (
    HookCallback,
    HookContext,
    HookEvent,
    HookRegistry,
    get_hook_registry,
    reset_hook_registry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(**overrides) -> HookContext:
    defaults = {
        "agent_name": "copilot",
        "tenant_id": "t1",
        "conversation_id": "conv1",
    }
    defaults.update(overrides)
    return HookContext(**defaults)


# ---------------------------------------------------------------------------
# Registration and firing
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_register_and_fire_hook() -> None:
    """A registered hook fires when the matching event is emitted."""
    registry = HookRegistry(timeout_s=5.0)
    callback = AsyncMock()
    registry.register(HookEvent.PRE_AGENT_RUN, callback)

    ctx = _make_context()
    await registry.fire(HookEvent.PRE_AGENT_RUN, ctx)

    callback.assert_awaited_once_with(ctx)


@pytest.mark.anyio
async def test_fire_runs_multiple_hooks_concurrently() -> None:
    """Multiple hooks for the same event run concurrently, not sequentially."""
    registry = HookRegistry(timeout_s=5.0)

    call_order: list[int] = []

    async def hook_a(ctx: HookContext) -> None:
        call_order.append(1)
        await asyncio.sleep(0.01)
        call_order.append(2)

    async def hook_b(ctx: HookContext) -> None:
        call_order.append(3)
        await asyncio.sleep(0.01)
        call_order.append(4)

    registry.register(HookEvent.POST_AGENT_RUN, hook_a)
    registry.register(HookEvent.POST_AGENT_RUN, hook_b)

    await registry.fire(HookEvent.POST_AGENT_RUN, _make_context())

    # Both hooks started before either finished (concurrent)
    assert len(call_order) == 4
    # 1 and 3 should both appear before 2 and 4
    assert set(call_order[:2]) == {1, 3}


@pytest.mark.anyio
async def test_hook_failure_does_not_propagate() -> None:
    """A failing hook is caught and logged, not propagated to the caller."""
    registry = HookRegistry(timeout_s=5.0)

    async def bad_hook(ctx: HookContext) -> None:
        raise ValueError("hook exploded")

    registry.register(HookEvent.ON_ERROR, bad_hook)

    # Should NOT raise
    await registry.fire(HookEvent.ON_ERROR, _make_context())


@pytest.mark.anyio
async def test_hook_timeout_does_not_block() -> None:
    """A slow hook is cancelled after the timeout and does not block the caller."""
    registry = HookRegistry(timeout_s=0.05)  # 50ms timeout

    timed_out = False

    async def slow_hook(ctx: HookContext) -> None:
        nonlocal timed_out
        try:
            await asyncio.sleep(10)  # way beyond timeout
        except asyncio.CancelledError:
            timed_out = True
            raise

    registry.register(HookEvent.PRE_TOOL_CALL, slow_hook)

    # Should complete quickly (within ~100ms), not wait 10 seconds
    await asyncio.wait_for(
        registry.fire(HookEvent.PRE_TOOL_CALL, _make_context()),
        timeout=1.0,
    )


@pytest.mark.anyio
async def test_fire_with_no_registered_hooks_is_noop() -> None:
    """Firing an event with no registered hooks completes without error."""
    registry = HookRegistry(timeout_s=5.0)
    # Should not raise
    await registry.fire(HookEvent.PRE_AGENT_RUN, _make_context())


# ---------------------------------------------------------------------------
# Clear
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_clear_removes_hooks_for_event() -> None:
    """Clearing a specific event removes only hooks for that event."""
    registry = HookRegistry(timeout_s=5.0)
    hook_a = AsyncMock()
    hook_b = AsyncMock()

    registry.register(HookEvent.PRE_AGENT_RUN, hook_a)
    registry.register(HookEvent.POST_AGENT_RUN, hook_b)

    registry.clear(HookEvent.PRE_AGENT_RUN)

    await registry.fire(HookEvent.PRE_AGENT_RUN, _make_context())
    await registry.fire(HookEvent.POST_AGENT_RUN, _make_context())

    hook_a.assert_not_awaited()
    hook_b.assert_awaited_once()


@pytest.mark.anyio
async def test_clear_all_removes_all_hooks() -> None:
    """Clearing with no event argument removes all hooks."""
    registry = HookRegistry(timeout_s=5.0)
    hook_a = AsyncMock()
    hook_b = AsyncMock()

    registry.register(HookEvent.PRE_AGENT_RUN, hook_a)
    registry.register(HookEvent.POST_AGENT_RUN, hook_b)

    registry.clear()  # clear all

    await registry.fire(HookEvent.PRE_AGENT_RUN, _make_context())
    await registry.fire(HookEvent.POST_AGENT_RUN, _make_context())

    hook_a.assert_not_awaited()
    hook_b.assert_not_awaited()


# ---------------------------------------------------------------------------
# Context and metadata
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hook_receives_correct_context() -> None:
    """Hook callback receives the HookContext with all fields populated."""
    registry = HookRegistry(timeout_s=5.0)
    received_ctx: list[HookContext] = []

    async def capture_hook(ctx: HookContext) -> None:
        received_ctx.append(ctx)

    registry.register(HookEvent.PRE_TOOL_CALL, capture_hook)

    ctx = _make_context(
        tool_name="search_documents",
        tool_args={"query": "test"},
        timing_ms=42.5,
    )
    await registry.fire(HookEvent.PRE_TOOL_CALL, ctx)

    assert len(received_ctx) == 1
    assert received_ctx[0].agent_name == "copilot"
    assert received_ctx[0].tenant_id == "t1"
    assert received_ctx[0].tool_name == "search_documents"
    assert received_ctx[0].tool_args == {"query": "test"}
    assert received_ctx[0].timing_ms == 42.5


def test_hook_count_reflects_registrations() -> None:
    """hook_count returns the number of hooks per event."""
    registry = HookRegistry(timeout_s=5.0)

    async def noop(ctx: HookContext) -> None:
        pass

    registry.register(HookEvent.PRE_AGENT_RUN, noop)
    registry.register(HookEvent.PRE_AGENT_RUN, noop)
    registry.register(HookEvent.POST_AGENT_RUN, noop)

    counts = registry.hook_count
    assert counts[HookEvent.PRE_AGENT_RUN] == 2
    assert counts[HookEvent.POST_AGENT_RUN] == 1


# ---------------------------------------------------------------------------
# Module-level registry
# ---------------------------------------------------------------------------


def test_reset_hook_registry_creates_fresh_instance() -> None:
    """reset_hook_registry replaces the default registry with a new one."""
    reg1 = get_hook_registry()
    reset_hook_registry()
    reg2 = get_hook_registry()

    assert reg1 is not reg2
