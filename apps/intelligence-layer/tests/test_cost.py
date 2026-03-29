"""Tests for cost computation."""
from __future__ import annotations

from decimal import Decimal

from app.observability.cost import compute_request_cost


def test_known_model_cost() -> None:
    """Sonnet cost computed correctly."""
    cost = compute_request_cost(
        "anthropic:claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=500,
    )
    # input: 1000/1000 * 0.003 = 0.003
    # output: 500/1000 * 0.015 = 0.0075
    assert cost == Decimal("0.0105")


def test_unknown_model_uses_default() -> None:
    """Unknown model falls back to default rate."""
    cost = compute_request_cost(
        "unknown:model",
        input_tokens=1000,
        output_tokens=1000,
    )
    # default: 0.003 + 0.015 = 0.018
    assert cost == Decimal("0.018")


def test_zero_tokens_zero_cost() -> None:
    """Zero tokens produces zero cost."""
    cost = compute_request_cost(
        "anthropic:claude-sonnet-4-6",
        input_tokens=0,
        output_tokens=0,
    )
    assert cost == Decimal("0")
