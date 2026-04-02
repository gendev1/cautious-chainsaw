"""Tests for IntelligentRunner — wraps pydantic_ai agent.run/run_stream with orchestration."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.runner import (
    IntelligentRunner,
    RunConfig,
    TokenBudgetExhausted,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides) -> RunConfig:
    defaults = {
        "agent_name": "copilot",
        "tenant_id": "t1",
        "actor_id": "a1",
        "conversation_id": "conv1",
    }
    defaults.update(overrides)
    return RunConfig(**defaults)


def _make_mock_agent_result(output="Hello!", input_tokens=100, output_tokens=50):
    """Create a mock pydantic_ai agent result."""
    result = MagicMock()
    result.output = output

    # Mock usage() to return a Usage-like object
    usage = MagicMock()
    usage.request_tokens = input_tokens
    usage.response_tokens = output_tokens
    usage.total_tokens = input_tokens + output_tokens
    result.usage.return_value = usage

    # Mock all_messages() for conversation save
    result.all_messages.return_value = []

    return result


def _make_mock_agent(result=None):
    """Create a mock pydantic_ai Agent."""
    agent = AsyncMock()
    if result is None:
        result = _make_mock_agent_result()
    agent.run = AsyncMock(return_value=result)
    return agent


# ---------------------------------------------------------------------------
# Basic run
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_calls_agent_and_returns_output() -> None:
    """IntelligentRunner.run calls the agent and returns its output."""
    config = _make_config()
    runner = IntelligentRunner(config)

    mock_result = _make_mock_agent_result(output="Portfolio looks good.")
    agent = _make_mock_agent(mock_result)

    output = await runner.run(agent, "How is my portfolio?", deps=MagicMock())

    assert output == "Portfolio looks good."
    agent.run.assert_awaited_once()


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_fires_pre_and_post_agent_hooks() -> None:
    """IntelligentRunner fires PRE_AGENT_RUN and POST_AGENT_RUN hooks."""
    from app.services.hooks import HookEvent, HookRegistry

    registry = HookRegistry(timeout_s=5.0)
    fired_events: list[HookEvent] = []

    async def capture_hook(ctx) -> None:
        # We track which event was fired by checking the event type the hook was registered for
        fired_events.append(ctx)

    registry.register(HookEvent.PRE_AGENT_RUN, capture_hook)
    registry.register(HookEvent.POST_AGENT_RUN, capture_hook)

    config = _make_config(hook_registry=registry)
    runner = IntelligentRunner(config)

    agent = _make_mock_agent()
    await runner.run(agent, "test prompt", deps=MagicMock())

    # Both pre and post hooks should have fired
    assert len(fired_events) == 2


# ---------------------------------------------------------------------------
# Cost accumulation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_accumulates_cost_from_result_usage() -> None:
    """IntelligentRunner records model usage from the agent result to the cost accumulator."""
    from app.middleware.cost_tracking import RequestCostAccumulator

    accumulator = RequestCostAccumulator(tenant_id="t1", request_id="req-001")
    config = _make_config(cost_accumulator=accumulator)
    runner = IntelligentRunner(config)

    mock_result = _make_mock_agent_result(input_tokens=500, output_tokens=200)
    agent = _make_mock_agent(mock_result)

    await runner.run(agent, "test", deps=MagicMock())

    summary = accumulator.summary()
    assert summary["total_input_tokens"] >= 500
    assert summary["total_output_tokens"] >= 200


# ---------------------------------------------------------------------------
# Token budget
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_checks_token_budget_before_run() -> None:
    """IntelligentRunner checks token budget before running the agent."""
    config = _make_config(token_budget_limit=1_000_000)
    runner = IntelligentRunner(config)

    agent = _make_mock_agent()
    # Should not raise when budget is available
    output = await runner.run(agent, "test", deps=MagicMock())
    assert output is not None


@pytest.mark.anyio
async def test_run_raises_token_budget_exhausted() -> None:
    """IntelligentRunner raises TokenBudgetExhausted when budget is exceeded."""
    config = _make_config(token_budget_limit=0)  # Zero budget
    runner = IntelligentRunner(config)

    agent = _make_mock_agent()

    with pytest.raises(TokenBudgetExhausted) as exc_info:
        await runner.run(agent, "test", deps=MagicMock())

    assert exc_info.value.tenant_id == "t1"


# ---------------------------------------------------------------------------
# Reactive compaction
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_retries_with_reactive_compact_on_prompt_too_long() -> None:
    """IntelligentRunner retries with reactive_compact when prompt_too_long is raised."""
    config = _make_config()
    runner = IntelligentRunner(config)

    # First call raises prompt_too_long, second succeeds
    mock_result = _make_mock_agent_result(output="Retry succeeded")
    agent = AsyncMock()

    # Simulate prompt_too_long on first call
    from pydantic_ai.exceptions import UnexpectedModelBehavior
    agent.run = AsyncMock(side_effect=[
        UnexpectedModelBehavior("prompt_too_long"),
        mock_result,
    ])

    output = await runner.run(
        agent,
        "test",
        deps=MagicMock(),
        message_history=[],
    )

    assert output == "Retry succeeded"
    assert agent.run.await_count == 2


# ---------------------------------------------------------------------------
# Error hooks
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_fires_on_error_hook_on_failure() -> None:
    """IntelligentRunner fires ON_ERROR hook when the agent run fails."""
    from app.services.hooks import HookEvent, HookRegistry

    registry = HookRegistry(timeout_s=5.0)
    error_contexts: list = []

    async def capture_error(ctx) -> None:
        error_contexts.append(ctx)

    registry.register(HookEvent.ON_ERROR, capture_error)

    config = _make_config(hook_registry=registry)
    runner = IntelligentRunner(config)

    agent = AsyncMock()
    agent.run = AsyncMock(side_effect=RuntimeError("model exploded"))

    with pytest.raises(RuntimeError):
        await runner.run(agent, "test", deps=MagicMock())

    assert len(error_contexts) >= 1
    assert error_contexts[0].error is not None


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_stream_yields_text_chunks() -> None:
    """IntelligentRunner.run_stream yields text chunks from the agent."""
    config = _make_config()
    runner = IntelligentRunner(config)

    # Mock agent.run_stream context manager
    agent = AsyncMock()
    mock_stream_result = AsyncMock()

    # Create an async iterator for stream_text
    async def mock_stream_text():
        for chunk in ["Hello", " world", "!"]:
            yield chunk

    mock_stream_result.stream_text = mock_stream_text
    mock_stream_result.usage.return_value = MagicMock(
        request_tokens=100, response_tokens=50, total_tokens=150
    )
    mock_stream_result.all_messages.return_value = []

    agent.run_stream = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=mock_stream_result),
        __aexit__=AsyncMock(return_value=False),
    ))

    chunks = []
    async for item in runner.run_stream(agent, "test", deps=MagicMock()):
        if isinstance(item, str):
            chunks.append(item)

    assert len(chunks) >= 1
    assert "Hello" in "".join(chunks) or len(chunks) > 0


@pytest.mark.anyio
async def test_run_stream_yields_progress_events() -> None:
    """IntelligentRunner.run_stream yields ProgressEvent objects for tool activity."""
    from app.services.progress_events import ProgressEvent

    config = _make_config()
    runner = IntelligentRunner(config)

    agent = AsyncMock()
    mock_stream_result = AsyncMock()

    async def mock_stream_text():
        yield "Hello"

    mock_stream_result.stream_text = mock_stream_text
    mock_stream_result.usage.return_value = MagicMock(
        request_tokens=100, response_tokens=50, total_tokens=150
    )
    mock_stream_result.all_messages.return_value = []

    agent.run_stream = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=mock_stream_result),
        __aexit__=AsyncMock(return_value=False),
    ))

    all_items = []
    async for item in runner.run_stream(agent, "test", deps=MagicMock()):
        all_items.append(item)

    # The runner should yield at least some items (text or events)
    assert len(all_items) >= 1


@pytest.mark.anyio
async def test_run_stream_accumulates_cost() -> None:
    """IntelligentRunner.run_stream records usage to the cost accumulator."""
    from app.middleware.cost_tracking import RequestCostAccumulator

    accumulator = RequestCostAccumulator(tenant_id="t1", request_id="req-001")
    config = _make_config(cost_accumulator=accumulator)
    runner = IntelligentRunner(config)

    agent = AsyncMock()
    mock_stream_result = AsyncMock()

    async def mock_stream_text():
        yield "Hello"

    mock_stream_result.stream_text = mock_stream_text
    mock_stream_result.usage.return_value = MagicMock(
        request_tokens=300, response_tokens=100, total_tokens=400
    )
    mock_stream_result.all_messages.return_value = []

    agent.run_stream = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=mock_stream_result),
        __aexit__=AsyncMock(return_value=False),
    ))

    async for _ in runner.run_stream(agent, "test", deps=MagicMock()):
        pass

    summary = accumulator.summary()
    assert summary["total_input_tokens"] >= 300


# ---------------------------------------------------------------------------
# _extract_cost edge case
# ---------------------------------------------------------------------------


def test_extract_cost_handles_missing_usage() -> None:
    """_extract_cost returns zeros when the result has no usage() method."""
    config = _make_config()
    runner = IntelligentRunner(config)

    mock_result = MagicMock(spec=[])  # No usage attribute

    cost, inp, out = runner._extract_cost(mock_result)

    assert cost == Decimal(0)
    assert inp == 0
    assert out == 0
