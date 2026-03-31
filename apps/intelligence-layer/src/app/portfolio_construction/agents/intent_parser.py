"""Intent parser agent — copilot-tier LLM for parsing user portfolio intent."""
from __future__ import annotations

from pydantic_ai import Agent

from app.portfolio_construction.models import ParsedIntent
from app.services.llm_client import get_model

portfolio_intent_parser: Agent[None, ParsedIntent] = Agent(
    model=get_model("copilot"),
    output_type=ParsedIntent,
    retries=2,
    defer_model_check=True,
    system_prompt=(
        "You are a portfolio intent parser for wealth advisors. "
        "Parse the user's message into structured investment intent: "
        "themes, anti-goals, factor preferences, constraints, and flags. "
        "Refine vague themes into specific investable themes. "
        "Infer factor preferences from language cues. "
        "Preserve explicit tickers and exclusions verbatim. "
        "Emit ambiguity_flags when intent is underspecified."
    ),
)
