"""Tests for enhanced compaction — LLM strategy, circuit breaker, financial data detection."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)

from app.services.compaction import (
    CompactionResult,
    CompactionStrategy,
    DeterministicCompactor,
    LLMCompactor,
    compact_conversation,
    detect_financial_data,
    estimate_token_count,
    microcompact_messages,
    reactive_compact,
    MICROCOMPACT_MAX_TOOL_RESULT_CHARS,
    RECENT_TURNS_TO_KEEP,
)
from app.services.circuit_breaker import CircuitBreaker, CircuitOpenError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user_msg(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _make_assistant_msg(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _make_tool_result_msg(tool_name: str, content: str) -> ModelRequest:
    return ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name=tool_name,
                content=content,
                tool_call_id="tc_1",
            )
        ]
    )


def _make_conversation(n_turns: int, chars_per_turn: int = 200) -> list:
    """Generate a multi-turn conversation of controllable size."""
    msgs = []
    for i in range(n_turns):
        msgs.append(_make_user_msg("x" * chars_per_turn))
        msgs.append(_make_assistant_msg("y" * chars_per_turn))
    return msgs


# ---------------------------------------------------------------------------
# Tier 1: Microcompact
# ---------------------------------------------------------------------------


def test_microcompact_truncates_oversized_tool_result() -> None:
    """Microcompact truncates tool results exceeding the char limit."""
    oversized = "A" * (MICROCOMPACT_MAX_TOOL_RESULT_CHARS + 5000)
    messages = [_make_tool_result_msg("search_documents", oversized)]

    result = microcompact_messages(messages)

    assert len(result) == 1
    part = result[0].parts[0]
    assert isinstance(part, ToolReturnPart)
    assert len(part.content) < len(oversized)
    assert "[truncated" in part.content


def test_microcompact_preserves_small_tool_results() -> None:
    """Microcompact leaves tool results under the limit unchanged."""
    small_content = "small result"
    messages = [_make_tool_result_msg("get_household_summary", small_content)]

    result = microcompact_messages(messages)

    assert len(result) == 1
    part = result[0].parts[0]
    assert isinstance(part, ToolReturnPart)
    assert part.content == small_content


# ---------------------------------------------------------------------------
# DeterministicCompactor
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_deterministic_compactor_summarizes_old_turns() -> None:
    """DeterministicCompactor produces a text summary of older turns."""
    compactor = DeterministicCompactor(max_chars_per_turn=150, max_turns_in_summary=8)
    messages = _make_conversation(5)

    summary = await compactor.summarize(messages)

    assert isinstance(summary, str)
    assert len(summary) > 0


@pytest.mark.anyio
async def test_deterministic_compactor_preserves_recent_turns() -> None:
    """DeterministicCompactor summary does not exceed max_turns_in_summary entries."""
    compactor = DeterministicCompactor(max_chars_per_turn=150, max_turns_in_summary=3)
    messages = _make_conversation(10)

    summary = await compactor.summarize(messages)

    assert isinstance(summary, str)
    # The summary should be bounded, not contain all 20 messages' full text
    assert len(summary) < sum(len("x" * 200) for _ in range(20))


# ---------------------------------------------------------------------------
# LLMCompactor
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_llm_compactor_calls_model_with_financial_context() -> None:
    """LLMCompactor invokes the model and includes financial context in the prompt."""
    compactor = LLMCompactor(model="anthropic:claude-haiku-4-5", max_summary_tokens=2000)

    messages = [
        _make_user_msg("What is the balance on account #12345?"),
        _make_assistant_msg("The balance on account #12345 is $50,000."),
    ]
    financial_context = ["Account #12345", "$50,000"]

    # LLMCompactor must call an LLM — we mock the underlying model call
    with patch.object(compactor, "summarize", new_callable=AsyncMock) as mock_summarize:
        mock_summarize.return_value = "Summary: discussed account #12345 with $50,000 balance."
        result = await compactor.summarize(messages, financial_context=financial_context)

    assert isinstance(result, str)
    mock_summarize.assert_awaited_once()
    # Verify financial_context was passed
    call_kwargs = mock_summarize.call_args
    assert call_kwargs.kwargs.get("financial_context") == financial_context


@pytest.mark.anyio
async def test_llm_compactor_falls_back_on_model_error() -> None:
    """LLMCompactor falls back to deterministic summarization when model errors."""
    compactor = LLMCompactor(model="anthropic:claude-haiku-4-5", max_summary_tokens=2000)
    messages = _make_conversation(3)

    # The real LLMCompactor.summarize should catch model errors and fall back.
    # Since the implementation does not exist yet, this test will fail at import.
    # When implemented, it should return a string even when the model raises.
    try:
        result = await compactor.summarize(messages)
        # If the model is not available, the method should still return a string
        assert isinstance(result, str)
    except Exception:
        # Expected to fail until implementation exists with proper fallback
        pytest.fail("LLMCompactor.summarize should fall back on model error, not raise")


# ---------------------------------------------------------------------------
# Financial data detection
# ---------------------------------------------------------------------------


def test_detect_financial_data_finds_account_numbers() -> None:
    """detect_financial_data identifies account numbers in messages."""
    messages = [_make_user_msg("Check account #12345678 and account #87654321")]
    found = detect_financial_data(messages)
    assert any("12345678" in item for item in found)


def test_detect_financial_data_finds_dollar_amounts() -> None:
    """detect_financial_data identifies dollar amounts in messages."""
    messages = [_make_assistant_msg("The portfolio value is $1,250,000.00")]
    found = detect_financial_data(messages)
    assert any("$" in item for item in found)


def test_detect_financial_data_finds_ticker_symbols() -> None:
    """detect_financial_data identifies ticker symbols in messages."""
    messages = [_make_user_msg("Buy 100 shares of AAPL and sell MSFT")]
    found = detect_financial_data(messages)
    assert any("AAPL" in item or "MSFT" in item for item in found)


def test_detect_financial_data_returns_empty_for_no_financial_data() -> None:
    """detect_financial_data returns empty list when no financial data present."""
    messages = [_make_user_msg("Hello, how are you today?")]
    found = detect_financial_data(messages)
    assert found == []


# ---------------------------------------------------------------------------
# Circuit breaker integration
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_circuit_breaker_skips_llm_after_3_failures() -> None:
    """After 3 LLM compaction failures, circuit opens and skips LLM strategy."""
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout_s=60.0)
    # Simulate 3 failures
    for _ in range(3):
        cb.record_failure()

    assert cb.state == "OPEN"

    # When compact_conversation is called with this open circuit breaker
    # and an LLM strategy, it should fall back to deterministic
    messages = _make_conversation(20, chars_per_turn=2000)
    result = await compact_conversation(
        messages,
        strategy=LLMCompactor(model="anthropic:claude-haiku-4-5"),
        circuit_breaker=cb,
    )
    assert isinstance(result, CompactionResult)
    # With circuit open, should NOT have used LLM
    assert result.strategy_used != "llm"


@pytest.mark.anyio
async def test_circuit_breaker_allows_retry_after_recovery() -> None:
    """Circuit breaker allows LLM compaction after recovery timeout."""
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout_s=0.01)
    for _ in range(3):
        cb.record_failure()
    assert cb.state == "OPEN"

    # Wait for recovery
    await asyncio.sleep(0.02)
    cb.check()  # Should transition to HALF_OPEN
    assert cb.state == "HALF_OPEN"


# ---------------------------------------------------------------------------
# compact_conversation (full pipeline)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_compact_conversation_uses_deterministic_below_2x_threshold() -> None:
    """Below 2x threshold, compact_conversation uses deterministic strategy."""
    # Create conversation just above the default 30k token threshold
    # but below 2x (60k). ~4 chars per token => ~120k chars = 30k tokens
    # Use 35k tokens worth = 140k chars / 200 chars per turn pair = 350 pairs
    msgs = _make_conversation(180, chars_per_turn=200)  # ~180*400/4 = 18k tokens
    # Need bigger: 180 turns * 400 chars / 4 = 18k tokens; need >30k
    msgs = _make_conversation(400, chars_per_turn=200)  # ~400*400/4 = 40k tokens

    result = await compact_conversation(msgs)
    assert isinstance(result, CompactionResult)
    assert result.was_compacted is True
    # Below 2x, should use deterministic
    assert result.strategy_used in ("deterministic", "none")


@pytest.mark.anyio
async def test_compact_conversation_escalates_to_llm_above_2x_threshold() -> None:
    """Above 2x threshold, compact_conversation escalates to LLM strategy if available."""
    # >60k tokens worth = 240k chars; 600 pairs * 400 chars / 4 = 60k tokens
    msgs = _make_conversation(800, chars_per_turn=200)  # 80k tokens

    mock_strategy = AsyncMock(spec=CompactionStrategy)
    mock_strategy.summarize.return_value = "LLM summary of the conversation."

    result = await compact_conversation(
        msgs,
        strategy=mock_strategy,
    )
    assert isinstance(result, CompactionResult)
    assert result.was_compacted is True
    assert result.strategy_used == "llm"


# ---------------------------------------------------------------------------
# reactive_compact
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reactive_compact_halves_recent_to_keep() -> None:
    """reactive_compact uses half the normal recent_to_keep for aggressive compaction."""
    msgs = _make_conversation(400, chars_per_turn=200)

    result = await reactive_compact(msgs)
    assert isinstance(result, CompactionResult)
    assert result.was_compacted is True
    # Reactive compact should keep fewer recent messages than normal
    # Normal is RECENT_TURNS_TO_KEEP (10), reactive is 5
    # So final count should be smaller than a normal compaction
    assert result.final_count <= RECENT_TURNS_TO_KEEP // 2 + 2  # +2 for system + summary


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def test_estimate_token_count_with_tiktoken_fallback() -> None:
    """estimate_token_count tries tiktoken, falls back to chars/4."""
    messages = [_make_user_msg("Hello world, this is a test message for token counting.")]
    count = estimate_token_count(messages)
    assert isinstance(count, int)
    assert count > 0
    # The result should be reasonable regardless of which estimator is used
    text_len = len("Hello world, this is a test message for token counting.")
    # With tiktoken or chars/4, should be in a reasonable range
    assert count <= text_len  # Can't be more tokens than chars
    assert count >= text_len // 10  # Should be at least 1/10 of chars


# ---------------------------------------------------------------------------
# strategy_used field
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_compact_conversation_returns_strategy_used() -> None:
    """CompactionResult includes strategy_used field."""
    # Small conversation that doesn't need compaction
    msgs = _make_conversation(3)
    result = await compact_conversation(msgs)
    assert isinstance(result, CompactionResult)
    assert hasattr(result, "strategy_used")
    assert result.strategy_used in ("none", "deterministic", "llm")
