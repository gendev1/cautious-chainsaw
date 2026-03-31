"""Rationale agent — copilot-tier LLM for generating portfolio rationale."""
from __future__ import annotations

from pydantic_ai import Agent

from app.portfolio_construction.models import PortfolioRationale
from app.services.llm_client import get_model

portfolio_rationale: Agent[None, PortfolioRationale] = Agent(
    model=get_model("copilot"),
    output_type=PortfolioRationale,
    retries=2,
    defer_model_check=True,
    system_prompt=(
        "You are a portfolio rationale generator. "
        "Explain the overall thesis in 2-3 sentences. "
        "Provide per-holding justification referencing factor and theme signals. "
        "Classify each holding as core (primary theme exposure) or supporting "
        "(diversification/factor quality)."
    ),
)
