"""Critic agent — copilot-tier LLM for portfolio critique and review."""
from __future__ import annotations

from pydantic_ai import Agent

from app.portfolio_construction.models import CriticFeedback
from app.services.llm_client import get_model

portfolio_critic: Agent[None, CriticFeedback] = Agent(
    model=get_model("copilot"),
    output_type=CriticFeedback,
    retries=2,
    defer_model_check=True,
    system_prompt=(
        "You are a portfolio critic. Review proposed portfolios for: "
        "theme alignment, anti-goal compliance, diversification, factor coherence, "
        "obvious core name inclusion, and account-aware turnover realism. "
        "Output APPROVED or NEEDS_REVISION with structured adjustment fields. "
        "Hard rules: never override user exclusions, never force constraint-violating inclusions."
    ),
)
