"""
app/observability/cost.py — Per-model cost computation.
"""
from __future__ import annotations

from decimal import Decimal

MODEL_RATES: dict[str, tuple[Decimal, Decimal]] = {
    "anthropic:claude-opus-4-6": (
        Decimal("0.015"),
        Decimal("0.075"),
    ),
    "anthropic:claude-sonnet-4-6": (
        Decimal("0.003"),
        Decimal("0.015"),
    ),
    "anthropic:claude-haiku-4-5": (
        Decimal("0.0008"),
        Decimal("0.004"),
    ),
    "openai:gpt-4o": (
        Decimal("0.0025"),
        Decimal("0.010"),
    ),
    "together:meta-llama/Llama-3.3-70B": (
        Decimal("0.0009"),
        Decimal("0.0009"),
    ),
}

DEFAULT_RATE = (Decimal("0.003"), Decimal("0.015"))


def compute_request_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> Decimal:
    """Compute cost in USD for a single request."""
    input_rate, output_rate = MODEL_RATES.get(
        model, DEFAULT_RATE
    )
    return (
        input_rate * Decimal(input_tokens) / Decimal(1000)
        + output_rate
        * Decimal(output_tokens)
        / Decimal(1000)
    )
