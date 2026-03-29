"""Tests for ConcentrationRiskScorer."""
from __future__ import annotations

from app.analytics.concentration_risk import (
    ConcentrationRiskScorer,
)


def test_concentrated_position_flagged() -> None:
    scorer = ConcentrationRiskScorer()
    result = scorer.score(
        {
            "holdings": [
                {
                    "ticker": "AAPL",
                    "market_value": 60000,
                    "sector": "Technology",
                },
                {
                    "ticker": "MSFT",
                    "market_value": 20000,
                    "sector": "Technology",
                },
                {
                    "ticker": "XOM",
                    "market_value": 20000,
                    "sector": "Energy",
                },
            ],
            "total_portfolio_value": 100000,
            "as_of": "2026-03-28",
        }
    )
    # AAPL is 60% — should produce flags
    assert len(result["flags"]) > 0


def test_diversified_portfolio_no_flags() -> None:
    scorer = ConcentrationRiskScorer()
    geos = ["US", "EU", "Asia", "EM"]
    holdings = [
        {
            "ticker": f"T{i}",
            "market_value": 5000,
            "sector": f"Sector{i}",
            "geography": geos[i % len(geos)],
        }
        for i in range(20)
    ]
    result = scorer.score(
        {
            "holdings": holdings,
            "total_portfolio_value": 100000,
            "as_of": "2026-03-28",
        }
    )
    # No position or sector flags (geo may flag due to defaults)
    position_sector = [
        f
        for f in result["flags"]
        if f["flag_type"] in ("position", "sector")
    ]
    assert len(position_sector) == 0


def test_hhi_computed() -> None:
    scorer = ConcentrationRiskScorer()
    result = scorer.score(
        {
            "holdings": [
                {
                    "ticker": "A",
                    "market_value": 50000,
                    "sector": "Tech",
                },
                {
                    "ticker": "B",
                    "market_value": 50000,
                    "sector": "Health",
                },
            ],
            "total_portfolio_value": 100000,
            "as_of": "2026-03-28",
        }
    )
    assert "hhi" in result
    assert result["hhi"] > 0
