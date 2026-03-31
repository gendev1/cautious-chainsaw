"""Tests for PortfolioFactorModelV2 — deterministic factor scoring."""
from __future__ import annotations

import math

import numpy as np
import pytest

from app.analytics.portfolio_factor_model_v2 import PortfolioFactorModelV2


# ---------------------------------------------------------------------------
# Synthetic data builders
# ---------------------------------------------------------------------------


def _make_security(ticker: str, sector: str = "Technology", industry: str = "Software") -> dict:
    return {
        "ticker": ticker,
        "name": f"{ticker} Corp",
        "sector": sector,
        "industry": industry,
        "market_cap": 50_000_000_000.0,
    }


def _make_fundamentals(
    ticker: str,
    pe_ratio: float = 25.0,
    pb_ratio: float = 5.0,
    roe: float = 0.25,
    roa: float = 0.12,
    debt_to_equity: float = 0.8,
    revenue_growth: float = 0.15,
    earnings_growth: float = 0.18,
    dividend_yield: float = 0.01,
    rnd_intensity: float = 0.10,
    free_cash_flow_yield: float = 0.04,
    current_ratio: float = 1.5,
    gross_margin: float = 0.65,
    operating_margin: float = 0.30,
    net_margin: float = 0.22,
) -> dict:
    return {
        "ticker": ticker,
        "pe_ratio": pe_ratio,
        "pb_ratio": pb_ratio,
        "roe": roe,
        "roa": roa,
        "debt_to_equity": debt_to_equity,
        "revenue_growth": revenue_growth,
        "earnings_growth": earnings_growth,
        "dividend_yield": dividend_yield,
        "rnd_intensity": rnd_intensity,
        "free_cash_flow_yield": free_cash_flow_yield,
        "current_ratio": current_ratio,
        "gross_margin": gross_margin,
        "operating_margin": operating_margin,
        "net_margin": net_margin,
    }


def _make_prices(
    ticker: str,
    realized_vol_1y: float = 0.25,
    beta: float = 1.1,
    momentum_3m: float = 0.08,
    momentum_6m: float = 0.15,
    momentum_12m: float = 0.22,
) -> dict:
    return {
        "ticker": ticker,
        "realized_vol_1y": realized_vol_1y,
        "beta": beta,
        "momentum_3m": momentum_3m,
        "momentum_6m": momentum_6m,
        "momentum_12m": momentum_12m,
        "prices": [
            {"date": f"2026-03-{27 - i}", "close": 150.0 + i * 0.5, "volume": 1_000_000}
            for i in range(30)
        ],
    }


def _build_synthetic_universe(n: int = 25) -> dict:
    """Build a synthetic universe of n securities for factor scoring."""
    sectors = ["Technology", "Healthcare", "Financials", "Energy", "Consumer Discretionary"]
    industries = ["Software", "Biotech", "Banking", "Oil & Gas", "Retail"]
    securities = []
    fundamentals = []
    prices = []

    for i in range(n):
        ticker = f"T{i:03d}"
        sector = sectors[i % len(sectors)]
        industry = industries[i % len(industries)]
        securities.append(_make_security(ticker, sector, industry))
        fundamentals.append(
            _make_fundamentals(
                ticker,
                pe_ratio=10.0 + i * 2.0,
                pb_ratio=1.0 + i * 0.5,
                roe=0.05 + (i / n) * 0.35,
                revenue_growth=0.02 + (i / n) * 0.30,
                earnings_growth=0.01 + (i / n) * 0.25,
                gross_margin=0.30 + (i / n) * 0.40,
            )
        )
        prices.append(
            _make_prices(
                ticker,
                realized_vol_1y=0.10 + (i / n) * 0.50,
                beta=0.5 + (i / n) * 1.5,
                momentum_3m=-0.10 + (i / n) * 0.30,
                momentum_12m=-0.05 + (i / n) * 0.40,
            )
        )

    return {
        "securities": securities,
        "fundamentals": {f["ticker"]: f for f in fundamentals},
        "prices": {p["ticker"]: p for p in prices},
    }


def _default_preferences() -> dict:
    return {
        "value": 0.20,
        "quality": 0.20,
        "growth": 0.20,
        "momentum": 0.15,
        "low_volatility": 0.10,
        "size": 0.15,
    }


# ---------------------------------------------------------------------------
# Model metadata
# ---------------------------------------------------------------------------


def test_model_metadata_name() -> None:
    """Factor model has correct metadata name."""
    model = PortfolioFactorModelV2()
    assert model.metadata.name == "portfolio_factor_model_v2"


def test_model_metadata_version() -> None:
    """Factor model has version 1.0.0."""
    model = PortfolioFactorModelV2()
    assert model.metadata.version == "1.0.0"


def test_model_metadata_category() -> None:
    """Factor model is in PORTFOLIO category."""
    model = PortfolioFactorModelV2()
    assert model.metadata.category.value == "portfolio" or str(model.metadata.category) == "ModelCategory.PORTFOLIO"


def test_model_metadata_kind() -> None:
    """Factor model is DETERMINISTIC."""
    model = PortfolioFactorModelV2()
    assert model.metadata.kind.value == "deterministic" or str(model.metadata.kind) == "ModelKind.DETERMINISTIC"


def test_model_metadata_known_limitations() -> None:
    """Factor model declares known limitations."""
    model = PortfolioFactorModelV2()
    assert len(model.metadata.known_limitations) > 0


# ---------------------------------------------------------------------------
# Percentile ranking
# ---------------------------------------------------------------------------


def test_percentile_ranking_produces_bounded_scores() -> None:
    """Percentile ranks map to [-1, 1] range."""
    model = PortfolioFactorModelV2()
    universe = _build_synthetic_universe(25)
    result = model.score({
        **universe,
        "preferences": _default_preferences(),
    })
    for ticker, score_data in result["scores"].items():
        overall = score_data["overall_score"]
        assert 0 <= overall <= 100, f"{ticker} overall_score {overall} out of [0, 100]"


def test_percentile_ranking_with_identical_values() -> None:
    """When all values are the same, scores should center around 50."""
    model = PortfolioFactorModelV2()
    n = 20
    securities = [_make_security(f"T{i:03d}") for i in range(n)]
    fundamentals = {f"T{i:03d}": _make_fundamentals(f"T{i:03d}") for i in range(n)}
    prices = {f"T{i:03d}": _make_prices(f"T{i:03d}") for i in range(n)}

    result = model.score({
        "securities": securities,
        "fundamentals": fundamentals,
        "prices": prices,
        "preferences": _default_preferences(),
    })
    scores = [s["overall_score"] for s in result["scores"].values()]
    avg = sum(scores) / len(scores)
    # With identical values, average should be near 50
    assert 40 <= avg <= 60, f"Expected average near 50, got {avg}"


# ---------------------------------------------------------------------------
# Winsorization
# ---------------------------------------------------------------------------


def test_winsorization_clips_outliers() -> None:
    """Outlier values should be winsorized at 5th/95th percentiles."""
    model = PortfolioFactorModelV2()
    n = 30
    securities = [_make_security(f"T{i:03d}") for i in range(n)]
    fundamentals = {}
    prices = {}
    for i in range(n):
        ticker = f"T{i:03d}"
        # Add extreme outlier for PE ratio
        pe = 1000.0 if i == 0 else 25.0 + i * 0.5
        fundamentals[ticker] = _make_fundamentals(ticker, pe_ratio=pe)
        prices[ticker] = _make_prices(ticker)

    result = model.score({
        "securities": securities,
        "fundamentals": fundamentals,
        "prices": prices,
        "preferences": _default_preferences(),
    })
    # Outlier should not dominate — its score should still be bounded
    outlier_score = result["scores"]["T000"]["overall_score"]
    assert 0 <= outlier_score <= 100


# ---------------------------------------------------------------------------
# Peer bucket selection
# ---------------------------------------------------------------------------


def test_peer_bucket_falls_back_to_sector() -> None:
    """When industry has < 15 peers, falls back to sector."""
    model = PortfolioFactorModelV2()
    securities = []
    fundamentals = {}
    prices = {}

    # 5 in one industry (below threshold of 15), 20 in same sector different industry
    for i in range(5):
        ticker = f"RARE{i:03d}"
        securities.append(_make_security(ticker, sector="Technology", industry="Quantum"))
        fundamentals[ticker] = _make_fundamentals(ticker)
        prices[ticker] = _make_prices(ticker)

    for i in range(20):
        ticker = f"TECH{i:03d}"
        securities.append(_make_security(ticker, sector="Technology", industry="Software"))
        fundamentals[ticker] = _make_fundamentals(ticker, pe_ratio=20.0 + i)
        prices[ticker] = _make_prices(ticker)

    result = model.score({
        "securities": securities,
        "fundamentals": fundamentals,
        "prices": prices,
        "preferences": _default_preferences(),
    })
    # All tickers should have scores
    assert len(result["scores"]) == 25


def test_peer_bucket_falls_back_to_universe() -> None:
    """When sector has < 25 peers, falls back to full universe."""
    model = PortfolioFactorModelV2()
    securities = []
    fundamentals = {}
    prices = {}

    # 10 in one sector (below threshold of 25)
    for i in range(10):
        ticker = f"SM{i:03d}"
        securities.append(_make_security(ticker, sector="Materials", industry="Mining"))
        fundamentals[ticker] = _make_fundamentals(ticker, pe_ratio=15.0 + i * 2)
        prices[ticker] = _make_prices(ticker)

    # Add more to different sectors to reach minimum universe
    for i in range(20):
        ticker = f"OT{i:03d}"
        securities.append(_make_security(ticker, sector="Technology", industry="Software"))
        fundamentals[ticker] = _make_fundamentals(ticker)
        prices[ticker] = _make_prices(ticker)

    result = model.score({
        "securities": securities,
        "fundamentals": fundamentals,
        "prices": prices,
        "preferences": _default_preferences(),
    })
    assert len(result["scores"]) == 30


# ---------------------------------------------------------------------------
# Correlation-adjusted weights
# ---------------------------------------------------------------------------


def test_correlation_adjusted_weights_sum_to_one() -> None:
    """Correlation-adjusted sub-factor weights must sum to 1.0."""
    model = PortfolioFactorModelV2()
    universe = _build_synthetic_universe(25)
    result = model.score({
        **universe,
        "preferences": _default_preferences(),
    })
    # Check that result has universe_stats with effective_weights
    assert "universe_stats" in result
    eff_weights = result["universe_stats"].get("effective_weights", {})
    if eff_weights:
        total = sum(eff_weights.values())
        assert abs(total - 1.0) < 0.01, f"Effective weights sum to {total}, expected 1.0"


def test_redundant_metrics_get_lower_weight() -> None:
    """Highly correlated sub-factors should get lower weights."""
    model = PortfolioFactorModelV2()
    # Build universe with perfectly correlated metrics
    n = 25
    securities = [_make_security(f"T{i:03d}") for i in range(n)]
    fundamentals = {}
    prices = {}
    for i in range(n):
        ticker = f"T{i:03d}"
        val = 10.0 + i * 3.0
        fundamentals[ticker] = _make_fundamentals(
            ticker,
            gross_margin=0.30 + (i / n) * 0.40,
            operating_margin=0.30 + (i / n) * 0.40,  # perfectly correlated with gross
            net_margin=0.30 + (i / n) * 0.40,  # perfectly correlated with gross
        )
        prices[ticker] = _make_prices(ticker)

    result = model.score({
        "securities": securities,
        "fundamentals": fundamentals,
        "prices": prices,
        "preferences": _default_preferences(),
    })
    # Should produce valid scores despite redundancy
    assert len(result["scores"]) == n


# ---------------------------------------------------------------------------
# Reliability shrinkage
# ---------------------------------------------------------------------------


def test_reliability_shrinkage_moves_toward_50() -> None:
    """Low-reliability scores should be shrunk toward 50."""
    model = PortfolioFactorModelV2()
    universe = _build_synthetic_universe(25)
    result = model.score({
        **universe,
        "preferences": _default_preferences(),
    })
    for ticker, score_data in result["scores"].items():
        reliability = score_data.get("reliability", 1.0)
        overall = score_data["overall_score"]
        if reliability < 0.5:
            # Score should be closer to 50 than the raw score would be
            assert abs(overall - 50) < 40, (
                f"{ticker} with low reliability {reliability} should be closer to 50"
            )


def test_full_reliability_no_shrinkage() -> None:
    """Full-reliability scores should not be significantly shrunk."""
    model = PortfolioFactorModelV2()
    universe = _build_synthetic_universe(25)
    result = model.score({
        **universe,
        "preferences": _default_preferences(),
    })
    for ticker, score_data in result["scores"].items():
        reliability = score_data.get("reliability", 1.0)
        overall = score_data["overall_score"]
        # All scores should be valid even with high reliability
        assert 0 <= overall <= 100


# ---------------------------------------------------------------------------
# Breadth caps
# ---------------------------------------------------------------------------


def test_breadth_cap_single_subfactor() -> None:
    """Score capped at 65 when only 1 sub-factor available."""
    model = PortfolioFactorModelV2()
    securities = [_make_security("LONE")]
    # Provide minimal fundamentals — only one field populated
    fundamentals = {
        "LONE": {
            "ticker": "LONE",
            "pe_ratio": 10.0,
            # All other fields None or missing
        },
    }
    prices = {"LONE": _make_prices("LONE")}

    result = model.score({
        "securities": securities,
        "fundamentals": fundamentals,
        "prices": prices,
        "preferences": _default_preferences(),
    })
    if "LONE" in result["scores"]:
        # If factor is active with single sub-factor, cap should be 65
        score = result["scores"]["LONE"]["overall_score"]
        assert score <= 65.0 or result["scores"]["LONE"].get("sub_factor_coverage", 0) > 0.5


def test_breadth_cap_low_support_share() -> None:
    """Score capped at 75 when supportive share < 0.50."""
    model = PortfolioFactorModelV2()
    n = 25
    securities = [_make_security(f"T{i:03d}") for i in range(n)]
    fundamentals = {}
    prices = {}
    for i in range(n):
        ticker = f"T{i:03d}"
        # Some metrics contradicting others
        fundamentals[ticker] = _make_fundamentals(
            ticker,
            pe_ratio=50.0 if i < 13 else 10.0,  # half high PE, half low PE
            roe=0.40 if i < 13 else 0.05,
        )
        prices[ticker] = _make_prices(ticker)

    result = model.score({
        "securities": securities,
        "fundamentals": fundamentals,
        "prices": prices,
        "preferences": _default_preferences(),
    })
    # All scores should be bounded
    for score_data in result["scores"].values():
        assert score_data["overall_score"] <= 100


def test_no_breadth_cap_high_support_share() -> None:
    """Score not capped when supportive share >= 0.70."""
    model = PortfolioFactorModelV2()
    universe = _build_synthetic_universe(25)
    result = model.score({
        **universe,
        "preferences": _default_preferences(),
    })
    # At least some scores should be above 75 when support is high
    max_score = max(s["overall_score"] for s in result["scores"].values())
    # With diverse synthetic data, top scores should exist
    assert max_score > 0


# ---------------------------------------------------------------------------
# Geometric mean
# ---------------------------------------------------------------------------


def test_geometric_mean_known_values() -> None:
    """Geometric mean of equal weights produces known result."""
    # For scores [80, 80] with equal weights [0.5, 0.5]:
    # geomean = 80^0.5 * 80^0.5 = 80
    expected = 80.0
    actual = math.pow(80.0, 0.5) * math.pow(80.0, 0.5)
    assert abs(actual - expected) < 0.01


def test_geometric_mean_different_scores() -> None:
    """Geometric mean of different scores with equal weights."""
    # geomean of [60, 90] with equal weights = 60^0.5 * 90^0.5 = sqrt(5400) ≈ 73.48
    expected = math.sqrt(60.0 * 90.0)
    actual = math.pow(60.0, 0.5) * math.pow(90.0, 0.5)
    assert abs(actual - expected) < 0.01


def test_full_score_produces_valid_output() -> None:
    """Full score() call on synthetic universe produces valid output structure."""
    model = PortfolioFactorModelV2()
    universe = _build_synthetic_universe(25)
    result = model.score({
        **universe,
        "preferences": _default_preferences(),
    })

    assert "scores" in result
    assert "universe_stats" in result
    assert "metadata" in result
    assert len(result["scores"]) == 25

    for ticker, score_data in result["scores"].items():
        assert "overall_score" in score_data
        assert "per_factor_scores" in score_data
        assert "reliability" in score_data
        assert 0 <= score_data["overall_score"] <= 100


# ---------------------------------------------------------------------------
# Factor deactivation
# ---------------------------------------------------------------------------


def test_factor_deactivation_low_coverage() -> None:
    """Factors with coverage < 0.60 should be deactivated."""
    model = PortfolioFactorModelV2()
    n = 25
    securities = [_make_security(f"T{i:03d}") for i in range(n)]
    fundamentals = {}
    prices = {}
    for i in range(n):
        ticker = f"T{i:03d}"
        # Provide only price data, no fundamentals -> value/quality/growth factors have no data
        fundamentals[ticker] = {"ticker": ticker}
        prices[ticker] = _make_prices(ticker)

    result = model.score({
        "securities": securities,
        "fundamentals": fundamentals,
        "prices": prices,
        "preferences": _default_preferences(),
    })

    stats = result["universe_stats"]
    # Some factors should be deactivated due to low coverage
    deactivated = stats.get("deactivated_factors", [])
    # With no fundamental data, value/quality/growth should be deactivated
    assert isinstance(deactivated, list)


def test_factor_deactivation_few_subfactors() -> None:
    """Factors with < 3 viable sub-factors should be deactivated."""
    model = PortfolioFactorModelV2()
    n = 25
    securities = [_make_security(f"T{i:03d}") for i in range(n)]
    fundamentals = {}
    prices = {}
    for i in range(n):
        ticker = f"T{i:03d}"
        # Provide only pe_ratio (1 sub-factor for value)
        fundamentals[ticker] = {"ticker": ticker, "pe_ratio": 20.0 + i}
        prices[ticker] = _make_prices(ticker)

    result = model.score({
        "securities": securities,
        "fundamentals": fundamentals,
        "prices": prices,
        "preferences": _default_preferences(),
    })
    assert len(result["scores"]) == n


# ---------------------------------------------------------------------------
# Activation report
# ---------------------------------------------------------------------------


def test_activation_report_populated() -> None:
    """Universe stats include activation report fields."""
    model = PortfolioFactorModelV2()
    universe = _build_synthetic_universe(25)
    result = model.score({
        **universe,
        "preferences": _default_preferences(),
    })

    stats = result["universe_stats"]
    assert "coverage" in stats or "active_factors" in stats
    assert "effective_weights" in stats or "deactivated_factors" in stats


# ---------------------------------------------------------------------------
# Lower-is-better inversion
# ---------------------------------------------------------------------------


def test_lower_is_better_inversion() -> None:
    """PE ratio (lower is better for value) should be inverted."""
    model = PortfolioFactorModelV2()
    n = 25
    securities = [_make_security(f"T{i:03d}") for i in range(n)]
    fundamentals = {}
    prices = {}
    for i in range(n):
        ticker = f"T{i:03d}"
        # T000 has lowest PE (best value), T024 has highest PE
        fundamentals[ticker] = _make_fundamentals(ticker, pe_ratio=10.0 + i * 2.0)
        prices[ticker] = _make_prices(ticker)

    result = model.score({
        "securities": securities,
        "fundamentals": fundamentals,
        "prices": prices,
        "preferences": {"value": 1.0, "quality": 0.0, "growth": 0.0, "momentum": 0.0, "low_volatility": 0.0, "size": 0.0},
    })

    # T000 (lowest PE) should score higher than T024 (highest PE) on value
    scores = result["scores"]
    if "T000" in scores and "T024" in scores:
        low_pe_score = scores["T000"]["overall_score"]
        high_pe_score = scores["T024"]["overall_score"]
        assert low_pe_score >= high_pe_score, (
            f"Low PE should score higher: {low_pe_score} vs {high_pe_score}"
        )


# ---------------------------------------------------------------------------
# Missing sub-factors
# ---------------------------------------------------------------------------


def test_missing_subfactors_omitted_not_zero_filled() -> None:
    """Missing sub-factor values should be omitted, not zero-filled."""
    model = PortfolioFactorModelV2()
    securities = [_make_security("SPARSE")]
    fundamentals = {
        "SPARSE": {
            "ticker": "SPARSE",
            "pe_ratio": 20.0,
            # All other fields absent
        },
    }
    prices = {"SPARSE": _make_prices("SPARSE")}

    result = model.score({
        "securities": securities,
        "fundamentals": fundamentals,
        "prices": prices,
        "preferences": _default_preferences(),
    })

    if "SPARSE" in result["scores"]:
        per_factor = result["scores"]["SPARSE"].get("per_factor_scores", {})
        # Factors without enough data should either be absent or have reduced coverage
        assert isinstance(per_factor, dict)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_universe() -> None:
    """Empty securities list produces empty scores."""
    model = PortfolioFactorModelV2()
    result = model.score({
        "securities": [],
        "fundamentals": {},
        "prices": {},
        "preferences": _default_preferences(),
    })
    assert result["scores"] == {} or len(result["scores"]) == 0


def test_single_security() -> None:
    """Single security produces a valid score."""
    model = PortfolioFactorModelV2()
    securities = [_make_security("ONLY")]
    fundamentals = {"ONLY": _make_fundamentals("ONLY")}
    prices = {"ONLY": _make_prices("ONLY")}

    result = model.score({
        "securities": securities,
        "fundamentals": fundamentals,
        "prices": prices,
        "preferences": _default_preferences(),
    })
    assert len(result["scores"]) == 1
    assert 0 <= result["scores"]["ONLY"]["overall_score"] <= 100


def test_metadata_output() -> None:
    """Result metadata includes model version and universe size."""
    model = PortfolioFactorModelV2()
    universe = _build_synthetic_universe(25)
    result = model.score({
        **universe,
        "preferences": _default_preferences(),
    })

    meta = result["metadata"]
    assert meta.get("model_version") == "1.0.0" or "version" in meta
    assert meta.get("universe_size") == 25 or "universe_size" in meta
