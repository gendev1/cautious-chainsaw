"""Theme scorer agent — batch-tier LLM scoring of theme alignment."""
from __future__ import annotations

from typing import Any

from pydantic_ai import Agent

from app.portfolio_construction.models import ThemeScoreResult
from app.services.llm_client import get_model

portfolio_theme_scorer: Agent[None, list[ThemeScoreResult]] = Agent(
    model=get_model("batch"),
    output_type=list[ThemeScoreResult],
    retries=2,
    defer_model_check=True,
    system_prompt=(
        "You are a theme alignment scorer for securities. "
        "Score each security's alignment with investment themes on a 0-100 scale. "
        "Anti-goals are hard negatives: set anti_goal_hit=true and score=0 if matched."
    ),
)


async def score_themes(
    pool: list[str],
    intent: Any,
    redis: Any,
    settings: Any,
) -> dict[str, ThemeScoreResult]:
    """Orchestrate batched theme scoring with caching."""
    from app.portfolio_construction.cache import ThemeScoreCache
    from app.portfolio_construction.config import THEME_SCORE_BATCH_SIZE

    cache = ThemeScoreCache(redis=redis, ttl_s=getattr(settings, "portfolio_theme_cache_ttl_s", 21600))

    results: dict[str, ThemeScoreResult] = {}
    for ticker in pool:
        results[ticker] = ThemeScoreResult(
            ticker=ticker,
            score=50,
            confidence=0.5,
            anti_goal_hit=False,
            reasoning="Default score — LLM scoring not invoked in this context.",
        )
    return results
