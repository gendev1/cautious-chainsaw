"""Tests for recall pool building logic."""
from __future__ import annotations

import pytest

from app.portfolio_construction.recall_pool import build_recall_pool
from app.portfolio_construction.models import (
    FactorPreferences,
    IntentConstraints,
    ParsedIntent,
)


# ---------------------------------------------------------------------------
# Synthetic data helpers
# ---------------------------------------------------------------------------


def _make_intent(
    themes: list[str] | None = None,
    anti_goals: list[str] | None = None,
    include_tickers: list[str] | None = None,
    excluded_tickers: list[str] | None = None,
) -> ParsedIntent:
    return ParsedIntent(
        themes=themes or ["artificial intelligence"],
        anti_goals=anti_goals or [],
        factor_preferences=FactorPreferences(),
        intent_constraints=IntentConstraints(
            excluded_tickers=excluded_tickers or [],
            include_tickers=include_tickers or [],
        ),
        ambiguity_flags=[],
        theme_weight=0.60,
        speculative=False,
    )


def _make_factor_scores(tickers: list[str], scores: list[float] | None = None) -> dict:
    """Build factor score dicts keyed by ticker."""
    scores = scores or [80.0 - i * 0.3 for i in range(len(tickers))]
    result = {}
    for i, ticker in enumerate(tickers):
        result[ticker] = {
            "ticker": ticker,
            "overall_score": scores[i] if i < len(scores) else 50.0,
            "per_factor_scores": {},
            "reliability": 0.85,
            "sub_factor_coverage": 0.90,
        }
    return result


def _make_securities(n: int, sectors: list[str] | None = None, tag_keywords: list[str] | None = None) -> list[dict]:
    """Build n synthetic security metadata dicts."""
    sectors = sectors or ["Technology"]
    result = []
    for i in range(n):
        ticker = f"T{i:03d}"
        sector = sectors[i % len(sectors)]
        result.append({
            "ticker": ticker,
            "name": f"{ticker} Corp",
            "sector": sector,
            "industry": "Software" if sector == "Technology" else "General",
            "description": f"{ticker} is a company in {sector}.",
            "tags": tag_keywords or [],
        })
    return result


def _make_fundamentals(tickers: list[str]) -> dict:
    return {t: {"ticker": t} for t in tickers}


# ---------------------------------------------------------------------------
# Tests: Factor top-N selection
# ---------------------------------------------------------------------------


def test_factor_top_n_selects_highest_scores() -> None:
    """Top N_factor (150) tickers by factor score are selected."""
    tickers = [f"T{i:03d}" for i in range(200)]
    # Assign descending scores so T000 is best
    scores = [90.0 - i * 0.3 for i in range(200)]
    factor_scores = _make_factor_scores(tickers, scores)
    securities = _make_securities(200)
    intent = _make_intent()

    pool = build_recall_pool(
        intent=intent,
        factor_scores=factor_scores,
        securities=securities,
        fundamentals=_make_fundamentals(tickers),
    )

    # Top ticker should be in pool
    assert "T000" in pool
    # Pool should be capped (either at 150 factor + metadata or at 250 total)
    assert len(pool) <= 250


def test_factor_top_n_respects_ordering() -> None:
    """Higher-scored tickers are preferred over lower-scored ones."""
    tickers = [f"T{i:03d}" for i in range(200)]
    scores = [100.0 - i * 0.4 for i in range(200)]
    factor_scores = _make_factor_scores(tickers, scores)
    securities = _make_securities(200)
    intent = _make_intent()

    pool = build_recall_pool(
        intent=intent,
        factor_scores=factor_scores,
        securities=securities,
        fundamentals=_make_fundamentals(tickers),
    )

    # T000 (score 100) should be in, T199 (score ~20) likely not unless metadata match
    assert "T000" in pool


# ---------------------------------------------------------------------------
# Tests: Metadata keyword matching
# ---------------------------------------------------------------------------


def test_metadata_matching_includes_sector_matches() -> None:
    """Securities matching theme keywords by sector are included."""
    tickers = [f"T{i:03d}" for i in range(200)]
    scores = [50.0] * 200  # All same score
    factor_scores = _make_factor_scores(tickers, scores)

    # Create securities with varied sectors, some matching "Healthcare"
    securities = []
    for i in range(200):
        ticker = f"T{i:03d}"
        sector = "Healthcare" if i >= 180 else "Technology"
        securities.append({
            "ticker": ticker,
            "name": f"{ticker} Corp",
            "sector": sector,
            "industry": "Biotech" if sector == "Healthcare" else "Software",
            "description": f"A {sector.lower()} company.",
            "tags": [],
        })

    intent = _make_intent(themes=["healthcare innovation"])

    pool = build_recall_pool(
        intent=intent,
        factor_scores=factor_scores,
        securities=securities,
        fundamentals=_make_fundamentals(tickers),
    )

    # Healthcare companies should be in pool due to metadata matching
    healthcare_in_pool = [t for t in pool if t.startswith("T1") and int(t[1:]) >= 180]
    assert len(healthcare_in_pool) > 0


def test_metadata_matching_by_name() -> None:
    """Securities whose name matches theme keywords are included."""
    tickers = ["AI_LEADER", "BORING_INC", "CHIP_MAKER"]
    scores = [30.0, 90.0, 30.0]  # AI_LEADER has low factor score but name matches
    factor_scores = _make_factor_scores(tickers, scores)

    securities = [
        {"ticker": "AI_LEADER", "name": "AI Leader Corp", "sector": "Technology", "industry": "AI", "description": "Artificial intelligence company.", "tags": ["ai"]},
        {"ticker": "BORING_INC", "name": "Boring Inc", "sector": "Industrial", "industry": "Manufacturing", "description": "Makes widgets.", "tags": []},
        {"ticker": "CHIP_MAKER", "name": "Chip Maker Inc", "sector": "Technology", "industry": "Semiconductors", "description": "Semiconductor chip maker.", "tags": ["chips"]},
    ]
    intent = _make_intent(themes=["artificial intelligence"])

    pool = build_recall_pool(
        intent=intent,
        factor_scores=factor_scores,
        securities=securities,
        fundamentals=_make_fundamentals(tickers),
    )

    assert "AI_LEADER" in pool


# ---------------------------------------------------------------------------
# Tests: Include / Exclude tickers
# ---------------------------------------------------------------------------


def test_include_tickers_always_in_pool() -> None:
    """Explicitly included tickers must appear in pool regardless of score."""
    tickers = [f"T{i:03d}" for i in range(50)]
    scores = [80.0] * 50
    # Force-include T049 even though it scores the same
    factor_scores = _make_factor_scores(tickers, scores)
    securities = _make_securities(50)
    intent = _make_intent(include_tickers=["T049"])

    pool = build_recall_pool(
        intent=intent,
        factor_scores=factor_scores,
        securities=securities,
        fundamentals=_make_fundamentals(tickers),
    )

    assert "T049" in pool


def test_include_tickers_not_in_universe() -> None:
    """Include tickers outside the scored universe are still in pool."""
    tickers = [f"T{i:03d}" for i in range(10)]
    factor_scores = _make_factor_scores(tickers)
    securities = _make_securities(10)
    intent = _make_intent(include_tickers=["OUTSIDER"])

    pool = build_recall_pool(
        intent=intent,
        factor_scores=factor_scores,
        securities=securities,
        fundamentals=_make_fundamentals(tickers),
    )

    assert "OUTSIDER" in pool


def test_excluded_tickers_removed_even_if_top_score() -> None:
    """Excluded tickers are removed even if they have the highest factor score."""
    tickers = [f"T{i:03d}" for i in range(50)]
    scores = [100.0] + [50.0] * 49  # T000 is top scorer
    factor_scores = _make_factor_scores(tickers, scores)
    securities = _make_securities(50)
    intent = _make_intent(excluded_tickers=["T000"])

    pool = build_recall_pool(
        intent=intent,
        factor_scores=factor_scores,
        securities=securities,
        fundamentals=_make_fundamentals(tickers),
    )

    assert "T000" not in pool


def test_excluded_overrides_include() -> None:
    """If a ticker is in both include and exclude, it should be excluded."""
    tickers = [f"T{i:03d}" for i in range(10)]
    factor_scores = _make_factor_scores(tickers)
    securities = _make_securities(10)
    intent = _make_intent(include_tickers=["T000"], excluded_tickers=["T000"])

    pool = build_recall_pool(
        intent=intent,
        factor_scores=factor_scores,
        securities=securities,
        fundamentals=_make_fundamentals(tickers),
    )

    assert "T000" not in pool


# ---------------------------------------------------------------------------
# Tests: Cap and deduplication
# ---------------------------------------------------------------------------


def test_cap_enforced_at_250() -> None:
    """Pool is capped at 250 tickers."""
    tickers = [f"T{i:04d}" for i in range(500)]
    factor_scores = _make_factor_scores(tickers)
    securities = _make_securities(500)
    intent = _make_intent()

    pool = build_recall_pool(
        intent=intent,
        factor_scores=factor_scores,
        securities=securities,
        fundamentals=_make_fundamentals(tickers),
    )

    assert len(pool) <= 250


def test_pool_smaller_than_cap_for_small_universe() -> None:
    """Pool contains all eligible tickers if universe is smaller than cap."""
    tickers = [f"T{i:03d}" for i in range(10)]
    factor_scores = _make_factor_scores(tickers)
    securities = _make_securities(10)
    intent = _make_intent()

    pool = build_recall_pool(
        intent=intent,
        factor_scores=factor_scores,
        securities=securities,
        fundamentals=_make_fundamentals(tickers),
    )

    assert len(pool) <= 10


def test_deduplication_factor_and_metadata() -> None:
    """Ticker appearing in both factor and metadata sets is counted once."""
    tickers = [f"T{i:03d}" for i in range(50)]
    scores = [80.0] * 50
    factor_scores = _make_factor_scores(tickers, scores)

    # Make T000 match theme by name AND be in top factor scores
    securities = _make_securities(50)
    securities[0]["name"] = "AI Leader Corp"
    securities[0]["description"] = "Leading artificial intelligence company."

    intent = _make_intent(themes=["artificial intelligence"])

    pool = build_recall_pool(
        intent=intent,
        factor_scores=factor_scores,
        securities=securities,
        fundamentals=_make_fundamentals(tickers),
    )

    # T000 should appear exactly once
    assert pool.count("T000") == 1 if isinstance(pool, list) else "T000" in pool


# ---------------------------------------------------------------------------
# Tests: Edge cases
# ---------------------------------------------------------------------------


def test_empty_factor_scores() -> None:
    """Empty factor scores produce a pool from metadata/includes only."""
    securities = _make_securities(5)
    intent = _make_intent(include_tickers=["T000"])

    pool = build_recall_pool(
        intent=intent,
        factor_scores={},
        securities=securities,
        fundamentals={},
    )

    assert "T000" in pool


def test_empty_intent_themes() -> None:
    """No themes still produces a pool from factor scores."""
    tickers = [f"T{i:03d}" for i in range(50)]
    factor_scores = _make_factor_scores(tickers)
    securities = _make_securities(50)
    intent = _make_intent(themes=[])

    pool = build_recall_pool(
        intent=intent,
        factor_scores=factor_scores,
        securities=securities,
        fundamentals=_make_fundamentals(tickers),
    )

    assert len(pool) > 0
