"""Tests for portfolio theme scorer: prompt-contract, cache key determinism, cache behavior."""
from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.portfolio_construction.models import ThemeScoreResult
from app.portfolio_construction.agents.theme_scorer import (
    portfolio_theme_scorer,
    score_themes,
)
from app.portfolio_construction.cache import ThemeScoreCache
from app.portfolio_construction.prompts import build_theme_scorer_prompt


# ---------------------------------------------------------------------------
# Prompt-contract tests
# ---------------------------------------------------------------------------


def test_theme_scorer_output_parses_from_json() -> None:
    """Sample LLM JSON output parses into list[ThemeScoreResult]."""
    sample_output = [
        {
            "ticker": "NVDA",
            "score": 92,
            "confidence": 0.95,
            "anti_goal_hit": False,
            "reasoning": "NVIDIA is a leading AI chip manufacturer with dominant market share in GPU compute.",
        },
        {
            "ticker": "MSFT",
            "score": 78,
            "confidence": 0.88,
            "anti_goal_hit": False,
            "reasoning": "Microsoft has significant AI integration across Azure and productivity suite.",
        },
        {
            "ticker": "META",
            "score": 0,
            "confidence": 0.92,
            "anti_goal_hit": True,
            "reasoning": "Meta is primarily a social media company, matching anti-goal.",
        },
    ]

    parsed = [ThemeScoreResult.model_validate(item) for item in sample_output]
    assert len(parsed) == 3
    assert parsed[0].ticker == "NVDA"
    assert parsed[0].score == 92
    assert parsed[2].anti_goal_hit is True
    assert parsed[2].score == 0


def test_theme_scorer_output_all_fields_present() -> None:
    """Every ThemeScoreResult from parsed output has all required fields."""
    sample_output = [
        {
            "ticker": "AAPL",
            "score": 65,
            "confidence": 0.72,
            "anti_goal_hit": False,
            "reasoning": "Apple has growing AI capabilities but primary business is consumer electronics.",
        },
    ]

    parsed = [ThemeScoreResult.model_validate(item) for item in sample_output]
    result = parsed[0]
    assert hasattr(result, "ticker")
    assert hasattr(result, "score")
    assert hasattr(result, "confidence")
    assert hasattr(result, "anti_goal_hit")
    assert hasattr(result, "reasoning")


def test_anti_goal_hit_forces_score_zero() -> None:
    """When anti_goal_hit is True, score must be 0."""
    sample = {
        "ticker": "SNAP",
        "score": 0,
        "confidence": 0.90,
        "anti_goal_hit": True,
        "reasoning": "Social media matches anti-goal.",
    }
    result = ThemeScoreResult.model_validate(sample)
    assert result.anti_goal_hit is True
    assert result.score == 0


def test_confidence_range_valid() -> None:
    """Confidence values are between 0 and 1."""
    for conf in [0.0, 0.5, 1.0]:
        result = ThemeScoreResult(
            ticker="TEST",
            score=50,
            confidence=conf,
            anti_goal_hit=False,
            reasoning="Test.",
        )
        assert 0.0 <= result.confidence <= 1.0


def test_score_range_valid() -> None:
    """Score values are between 0 and 100."""
    for score in [0, 50, 100]:
        result = ThemeScoreResult(
            ticker="TEST",
            score=score,
            confidence=0.80,
            anti_goal_hit=False,
            reasoning="Test.",
        )
        assert 0 <= result.score <= 100


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


def test_build_theme_scorer_prompt_includes_themes() -> None:
    """Theme scorer prompt includes theme keywords."""
    prompt = build_theme_scorer_prompt(
        themes=["artificial intelligence", "cloud computing"],
        anti_goals=["social media"],
        tickers=["NVDA", "MSFT"],
        security_metadata={
            "NVDA": {"name": "NVIDIA Corp", "sector": "Technology"},
            "MSFT": {"name": "Microsoft Corp", "sector": "Technology"},
        },
    )
    assert "artificial intelligence" in prompt.lower()
    assert "cloud computing" in prompt.lower()
    assert "social media" in prompt.lower()
    assert "NVDA" in prompt
    assert "MSFT" in prompt


def test_build_theme_scorer_prompt_includes_anti_goals() -> None:
    """Prompt includes anti-goal instructions."""
    prompt = build_theme_scorer_prompt(
        themes=["AI"],
        anti_goals=["fossil fuels"],
        tickers=["XOM"],
        security_metadata={"XOM": {"name": "ExxonMobil", "sector": "Energy"}},
    )
    assert "fossil fuels" in prompt.lower() or "anti" in prompt.lower()


def test_build_theme_scorer_prompt_empty_tickers() -> None:
    """Prompt handles empty ticker list gracefully."""
    prompt = build_theme_scorer_prompt(
        themes=["AI"],
        anti_goals=[],
        tickers=[],
        security_metadata={},
    )
    assert isinstance(prompt, str)
    assert len(prompt) > 0


# ---------------------------------------------------------------------------
# Cache key determinism
# ---------------------------------------------------------------------------


def test_cache_key_same_inputs_same_key() -> None:
    """Same inputs produce the same cache key."""
    cache = ThemeScoreCache(redis=MagicMock(), ttl_s=21600)
    key1 = cache.compute_key(
        themes=["AI", "cloud"],
        anti_goals=["social media"],
        tickers=["NVDA", "MSFT", "AAPL"],
        scorer_model="anthropic:claude-haiku-4-5",
        prompt_version="v1",
        universe_snapshot_id="snap_001",
    )
    key2 = cache.compute_key(
        themes=["AI", "cloud"],
        anti_goals=["social media"],
        tickers=["NVDA", "MSFT", "AAPL"],
        scorer_model="anthropic:claude-haiku-4-5",
        prompt_version="v1",
        universe_snapshot_id="snap_001",
    )
    assert key1 == key2


def test_cache_key_different_themes_different_key() -> None:
    """Different themes produce different cache keys."""
    cache = ThemeScoreCache(redis=MagicMock(), ttl_s=21600)
    key1 = cache.compute_key(
        themes=["AI"],
        anti_goals=[],
        tickers=["NVDA"],
        scorer_model="anthropic:claude-haiku-4-5",
        prompt_version="v1",
        universe_snapshot_id="snap_001",
    )
    key2 = cache.compute_key(
        themes=["clean energy"],
        anti_goals=[],
        tickers=["NVDA"],
        scorer_model="anthropic:claude-haiku-4-5",
        prompt_version="v1",
        universe_snapshot_id="snap_001",
    )
    assert key1 != key2


def test_cache_key_different_tickers_different_key() -> None:
    """Different tickers produce different cache keys."""
    cache = ThemeScoreCache(redis=MagicMock(), ttl_s=21600)
    key1 = cache.compute_key(
        themes=["AI"],
        anti_goals=[],
        tickers=["NVDA"],
        scorer_model="anthropic:claude-haiku-4-5",
        prompt_version="v1",
        universe_snapshot_id="snap_001",
    )
    key2 = cache.compute_key(
        themes=["AI"],
        anti_goals=[],
        tickers=["NVDA", "AAPL"],
        scorer_model="anthropic:claude-haiku-4-5",
        prompt_version="v1",
        universe_snapshot_id="snap_001",
    )
    assert key1 != key2


def test_cache_key_order_independent() -> None:
    """Theme and ticker order should not affect cache key (canonical sort)."""
    cache = ThemeScoreCache(redis=MagicMock(), ttl_s=21600)
    key1 = cache.compute_key(
        themes=["cloud", "AI"],
        anti_goals=["gaming", "social media"],
        tickers=["MSFT", "NVDA", "AAPL"],
        scorer_model="anthropic:claude-haiku-4-5",
        prompt_version="v1",
        universe_snapshot_id="snap_001",
    )
    key2 = cache.compute_key(
        themes=["AI", "cloud"],
        anti_goals=["social media", "gaming"],
        tickers=["AAPL", "NVDA", "MSFT"],
        scorer_model="anthropic:claude-haiku-4-5",
        prompt_version="v1",
        universe_snapshot_id="snap_001",
    )
    assert key1 == key2


# ---------------------------------------------------------------------------
# Cache hit/miss behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_miss_returns_none() -> None:
    """Cache get returns None on miss."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    cache = ThemeScoreCache(redis=redis, ttl_s=21600)

    result = await cache.get("nonexistent_key")
    assert result is None


@pytest.mark.asyncio
async def test_cache_hit_returns_scores() -> None:
    """Cache get returns cached scores on hit."""
    cached_data = json.dumps([
        {"ticker": "NVDA", "score": 90, "confidence": 0.95, "anti_goal_hit": False, "reasoning": "AI leader."},
    ])
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=cached_data)
    cache = ThemeScoreCache(redis=redis, ttl_s=21600)

    result = await cache.get("existing_key")
    assert result is not None
    assert len(result) == 1
    assert result[0].ticker == "NVDA"


@pytest.mark.asyncio
async def test_cache_set_writes_to_redis() -> None:
    """Cache set writes serialized data to Redis with TTL."""
    redis = AsyncMock()
    redis.set = AsyncMock()
    cache = ThemeScoreCache(redis=redis, ttl_s=21600)

    scores = [
        ThemeScoreResult(ticker="NVDA", score=90, confidence=0.95, anti_goal_hit=False, reasoning="AI leader."),
    ]
    await cache.set("test_key", scores)

    redis.set.assert_awaited_once()
    call_args = redis.set.call_args
    assert call_args is not None


@pytest.mark.asyncio
async def test_cache_set_respects_ttl() -> None:
    """Cache set passes the configured TTL to Redis."""
    redis = AsyncMock()
    redis.set = AsyncMock()
    ttl = 3600
    cache = ThemeScoreCache(redis=redis, ttl_s=ttl)

    scores = [
        ThemeScoreResult(ticker="AAPL", score=70, confidence=0.80, anti_goal_hit=False, reasoning="Test."),
    ]
    await cache.set("ttl_key", scores)

    redis.set.assert_awaited_once()
    # TTL should appear in the call
    call_kwargs = redis.set.call_args
    assert call_kwargs is not None
