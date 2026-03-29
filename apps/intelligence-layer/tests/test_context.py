"""Tests for context window builder — token budgeting and truncation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.rag.context import ContextBudget, ContextWindowBuilder


@dataclass
class FakeChunk:
    chunk_id: str = "c_001"
    source_type: str = "document"
    source_id: str = "doc_001"
    chunk_index: int = 0
    text: str = "sample text"
    cosine_distance: float = 0.2
    relevance_score: float = 0.8
    created_at: str = "2026-03-28T00:00:00Z"
    household_id: str | None = None
    client_id: str | None = None
    account_id: str | None = None
    advisor_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def test_context_fits_within_budget() -> None:
    """T6: Chunks are fit within the retrieved context budget."""
    budget = ContextBudget(retrieved_context_limit=500)
    builder = ContextWindowBuilder(budget=budget)
    chunks = [
        FakeChunk(text="word " * 50) for _ in range(5)
    ]
    prompt, history, included = builder.build_context(
        "System prompt", [], chunks
    )
    assert len(included) <= len(chunks)
    assert "Retrieved Context" in prompt


def test_truncates_oldest_history_first() -> None:
    """T7: Oldest history messages are truncated first."""
    budget = ContextBudget(conversation_history_reserve=100)
    builder = ContextWindowBuilder(budget=budget)
    messages = [
        {
            "role": "user",
            "content": f"Message {i} " + "x " * 30,
        }
        for i in range(10)
    ]
    _, truncated, _ = builder.build_context(
        "System", messages, []
    )
    assert len(truncated) < len(messages)
    if truncated:
        last_input = messages[-1]["content"]
        last_output = truncated[-1]["content"]
        assert last_input == last_output


def test_empty_chunks_produces_no_context_message() -> None:
    """No chunks results in 'No relevant context' message."""
    builder = ContextWindowBuilder()
    prompt, _, included = builder.build_context(
        "System", [], []
    )
    assert "No relevant context" in prompt
    assert included == []


def test_budget_available_calculation() -> None:
    """ContextBudget.available_for_context calculates correctly."""
    budget = ContextBudget(
        total_limit=100_000,
        system_prompt_reserve=2_000,
        conversation_history_reserve=8_000,
        response_reserve=4_000,
    )
    assert budget.available_for_context == 86_000
