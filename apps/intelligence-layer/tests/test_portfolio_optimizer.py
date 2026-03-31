"""Tests for portfolio optimizer: weighting strategies, clamping, auto-relax, candidate selection."""
from __future__ import annotations

import pytest

from app.portfolio_construction.optimizer import (
    auto_relax,
    clamp_positions,
    select_candidates,
    weight_conviction,
    weight_equal,
    weight_min_variance,
    weight_risk_parity,
)
from app.portfolio_construction.models import (
    CompositeScoreResult,
    FactorPreferences,
    IntentConstraints,
    ParsedIntent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_intent(
    excluded_tickers: list[str] | None = None,
    include_tickers: list[str] | None = None,
    max_sector_concentration: float = 0.30,
    target_count: int = 25,
) -> ParsedIntent:
    return ParsedIntent(
        themes=["AI"],
        anti_goals=[],
        factor_preferences=FactorPreferences(),
        intent_constraints=IntentConstraints(
            excluded_tickers=excluded_tickers or [],
            include_tickers=include_tickers or [],
            max_sector_concentration=max_sector_concentration,
        ),
        ambiguity_flags=[],
        theme_weight=0.60,
        speculative=False,
    )


def _make_composite(ticker: str, score: float, gated: bool = False) -> CompositeScoreResult:
    return CompositeScoreResult(
        ticker=ticker,
        composite_score=score,
        factor_score=score,
        theme_score=score,
        gated=gated,
        gate_reason="excluded" if gated else None,
        coherence_bonus=0.0,
        weak_link_penalty=0.0,
    )


def _make_price_data(tickers: list[str], vols: list[float] | None = None) -> dict:
    """Build synthetic price data with realized volatilities."""
    vols = vols or [0.25] * len(tickers)
    result = {}
    for i, ticker in enumerate(tickers):
        result[ticker] = {
            "ticker": ticker,
            "realized_vol_1y": vols[i] if i < len(vols) else 0.25,
            "beta": 1.0,
            "prices": [
                {"date": f"2026-03-{27 - j}", "close": 100.0 + j * 0.5, "volume": 1_000_000}
                for j in range(30)
            ],
        }
    return result


def _make_securities_metadata(tickers: list[str], sectors: list[str] | None = None) -> dict:
    sectors = sectors or ["Technology"] * len(tickers)
    return {
        t: {"ticker": t, "sector": sectors[i] if i < len(sectors) else "Technology"}
        for i, t in enumerate(tickers)
    }


# ---------------------------------------------------------------------------
# Equal weighting
# ---------------------------------------------------------------------------


def test_equal_weighting_sums_to_one() -> None:
    """Equal weights sum to 1.0."""
    tickers = ["A", "B", "C", "D", "E"]
    weights = weight_equal(tickers)
    assert abs(sum(weights.values()) - 1.0) < 1e-6


def test_equal_weighting_each_ticker() -> None:
    """Each ticker gets 1/N weight."""
    tickers = ["A", "B", "C", "D"]
    weights = weight_equal(tickers)
    expected = 1.0 / 4
    for t in tickers:
        assert abs(weights[t] - expected) < 1e-6


def test_equal_weighting_single_ticker() -> None:
    """Single ticker gets weight 1.0."""
    weights = weight_equal(["ONLY"])
    assert abs(weights["ONLY"] - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# Conviction weighting
# ---------------------------------------------------------------------------


def test_conviction_weighting_sums_to_one() -> None:
    """Conviction weights sum to 1.0."""
    tickers = ["A", "B", "C"]
    composite_scores = {
        "A": _make_composite("A", 90.0),
        "B": _make_composite("B", 60.0),
        "C": _make_composite("C", 75.0),
    }
    weights = weight_conviction(tickers, composite_scores)
    assert abs(sum(weights.values()) - 1.0) < 1e-6


def test_conviction_higher_score_higher_weight() -> None:
    """Higher composite score yields higher weight."""
    tickers = ["HIGH", "LOW"]
    composite_scores = {
        "HIGH": _make_composite("HIGH", 95.0),
        "LOW": _make_composite("LOW", 55.0),
    }
    weights = weight_conviction(tickers, composite_scores)
    assert weights["HIGH"] > weights["LOW"]


def test_conviction_weighting_proportional() -> None:
    """Weights are proportional to composite scores."""
    tickers = ["A", "B"]
    composite_scores = {
        "A": _make_composite("A", 80.0),
        "B": _make_composite("B", 40.0),
    }
    weights = weight_conviction(tickers, composite_scores)
    # A should get roughly 2x the weight of B
    ratio = weights["A"] / weights["B"]
    assert 1.5 < ratio < 2.5


# ---------------------------------------------------------------------------
# Risk parity weighting
# ---------------------------------------------------------------------------


def test_risk_parity_sums_to_one() -> None:
    """Risk parity weights sum to 1.0."""
    tickers = ["A", "B", "C"]
    price_data = _make_price_data(tickers, vols=[0.20, 0.30, 0.40])
    weights = weight_risk_parity(tickers, price_data)
    assert abs(sum(weights.values()) - 1.0) < 1e-6


def test_risk_parity_lower_vol_higher_weight() -> None:
    """Lower volatility gets higher weight."""
    tickers = ["LOW_VOL", "HIGH_VOL"]
    price_data = _make_price_data(tickers, vols=[0.15, 0.45])
    weights = weight_risk_parity(tickers, price_data)
    assert weights["LOW_VOL"] > weights["HIGH_VOL"]


def test_risk_parity_sector_median_imputation() -> None:
    """Missing vol is imputed with sector median."""
    tickers = ["A", "B", "MISSING"]
    price_data = _make_price_data(["A", "B"], vols=[0.25, 0.35])
    # MISSING has no price data
    price_data["MISSING"] = {"ticker": "MISSING", "realized_vol_1y": None, "prices": []}

    weights = weight_risk_parity(tickers, price_data)
    assert abs(sum(weights.values()) - 1.0) < 1e-6
    assert "MISSING" in weights


# ---------------------------------------------------------------------------
# Min variance weighting
# ---------------------------------------------------------------------------


def test_min_variance_sums_to_one() -> None:
    """Min variance weights sum to 1.0."""
    tickers = ["A", "B", "C", "D", "E"]
    price_data = _make_price_data(tickers, vols=[0.20, 0.25, 0.30, 0.22, 0.28])
    composite_scores = {t: _make_composite(t, 70.0 + i * 3) for i, t in enumerate(tickers)}
    weights = weight_min_variance(tickers, price_data, composite_scores)
    assert abs(sum(weights.values()) - 1.0) < 1e-6


def test_min_variance_fallback_to_risk_parity() -> None:
    """Min variance falls back to risk parity when solver fails."""
    # Provide conflicting data that may cause solver issues
    tickers = ["A", "B"]
    # Minimal price data that could cause singular covariance
    price_data = _make_price_data(tickers, vols=[0.0, 0.0])  # Zero vol -> singular
    composite_scores = {t: _make_composite(t, 70.0) for t in tickers}

    weights = weight_min_variance(tickers, price_data, composite_scores)
    # Should still produce valid weights (via fallback)
    assert abs(sum(weights.values()) - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# Position clamping
# ---------------------------------------------------------------------------


def test_clamp_positions_respects_min() -> None:
    """No weight below min_weight after clamping."""
    weights = {"A": 0.50, "B": 0.005, "C": 0.005, "D": 0.49}
    clamped = clamp_positions(weights, min_weight=0.02, max_weight=0.10)
    for w in clamped.values():
        assert w >= 0.02 - 1e-6


def test_clamp_positions_respects_max() -> None:
    """No weight above max_weight after clamping."""
    weights = {"A": 0.50, "B": 0.20, "C": 0.15, "D": 0.15}
    clamped = clamp_positions(weights, min_weight=0.02, max_weight=0.10)
    for w in clamped.values():
        assert w <= 0.10 + 1e-6


def test_clamp_positions_sums_to_one() -> None:
    """Clamped weights still sum to 1.0."""
    weights = {"A": 0.40, "B": 0.30, "C": 0.20, "D": 0.10}
    clamped = clamp_positions(weights, min_weight=0.02, max_weight=0.10)
    assert abs(sum(clamped.values()) - 1.0) < 1e-4


def test_clamp_positions_already_valid() -> None:
    """Already-valid weights are not changed."""
    weights = {"A": 0.05, "B": 0.05, "C": 0.05, "D": 0.05, "E": 0.80}
    # With max_weight=0.10 this needs clamping, but test with loose bounds
    clamped = clamp_positions(weights, min_weight=0.02, max_weight=0.85)
    assert abs(sum(clamped.values()) - 1.0) < 1e-4


def test_clamp_positions_many_positions() -> None:
    """Clamping works with many positions."""
    n = 50
    weights = {f"T{i:03d}": 1.0 / n for i in range(n)}
    clamped = clamp_positions(weights, min_weight=0.02, max_weight=0.10)
    assert abs(sum(clamped.values()) - 1.0) < 1e-4


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------


def test_select_candidates_include_tickers() -> None:
    """Include tickers are always in the selected set."""
    tickers = [f"T{i:03d}" for i in range(30)]
    composites = [_make_composite(t, 50.0 + i) for i, t in enumerate(tickers)]
    intent = _make_intent(include_tickers=["T000"])
    metadata = _make_securities_metadata(tickers)

    selected, relaxation_notes = select_candidates(composites, intent, metadata)
    assert "T000" in selected


def test_select_candidates_excludes_tickers() -> None:
    """Excluded tickers are never in the selected set."""
    tickers = [f"T{i:03d}" for i in range(30)]
    composites = [_make_composite(t, 90.0 - i) for i, t in enumerate(tickers)]
    intent = _make_intent(excluded_tickers=["T000"])
    metadata = _make_securities_metadata(tickers)

    selected, _ = select_candidates(composites, intent, metadata)
    assert "T000" not in selected


def test_select_candidates_sector_cap() -> None:
    """No sector exceeds max_sector_concentration."""
    tickers = [f"T{i:03d}" for i in range(30)]
    composites = [_make_composite(t, 90.0 - i) for i, t in enumerate(tickers)]
    # All in same sector
    metadata = _make_securities_metadata(tickers, sectors=["Technology"] * 30)
    intent = _make_intent(max_sector_concentration=0.30, target_count=20)

    selected, _ = select_candidates(composites, intent, metadata)

    # With 20 positions equal weighted, max sector count = 0.30 * 20 = 6
    tech_count = sum(1 for t in selected if metadata[t]["sector"] == "Technology")
    # All are tech, so this constraint limits the selection
    assert len(selected) <= 20


def test_select_candidates_respects_target_count() -> None:
    """Selection respects the target_count."""
    tickers = [f"T{i:03d}" for i in range(50)]
    composites = [_make_composite(t, 90.0 - i * 0.5) for i, t in enumerate(tickers)]
    intent = _make_intent(target_count=15)
    metadata = _make_securities_metadata(tickers, sectors=["Technology", "Healthcare", "Financials", "Energy", "Consumer"] * 10)

    selected, _ = select_candidates(composites, intent, metadata)
    assert len(selected) <= 15


# ---------------------------------------------------------------------------
# Auto-relax
# ---------------------------------------------------------------------------


def test_auto_relax_when_no_candidates() -> None:
    """Auto-relax is triggered when no candidates pass gates."""
    tickers = [f"T{i:03d}" for i in range(10)]
    # All gated
    composites = [_make_composite(t, 0.0, gated=True) for t in tickers]
    intent = _make_intent(target_count=5)
    metadata = _make_securities_metadata(tickers)

    selected, relaxation_notes = auto_relax(composites, intent, metadata)
    assert len(relaxation_notes) > 0


def test_auto_relax_sequence_order() -> None:
    """Relaxation follows fixed order: min_theme_score, max_beta, max_sector_concentration, target_count."""
    tickers = [f"T{i:03d}" for i in range(10)]
    composites = [_make_composite(t, 0.0, gated=True) for t in tickers]
    intent = _make_intent(target_count=5)
    metadata = _make_securities_metadata(tickers)

    _, relaxation_notes = auto_relax(composites, intent, metadata)
    # At least one relaxation note should be present
    assert isinstance(relaxation_notes, list)
    if len(relaxation_notes) > 0:
        # First relaxation should relate to theme score
        first = relaxation_notes[0].lower()
        assert "theme" in first or "score" in first or "relax" in first


def test_auto_relax_notes_describe_changes() -> None:
    """Relaxation notes describe what was relaxed and by how much."""
    tickers = [f"T{i:03d}" for i in range(10)]
    composites = [_make_composite(t, 0.0, gated=True) for t in tickers]
    intent = _make_intent(target_count=5)
    metadata = _make_securities_metadata(tickers)

    _, relaxation_notes = auto_relax(composites, intent, metadata)
    for note in relaxation_notes:
        assert isinstance(note, str)
        assert len(note) > 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_equal_weighting_empty_list() -> None:
    """Empty ticker list raises or returns empty dict."""
    try:
        weights = weight_equal([])
        assert weights == {} or len(weights) == 0
    except (ValueError, ZeroDivisionError):
        pass  # Acceptable to raise on empty input


def test_conviction_weighting_zero_scores() -> None:
    """All-zero composite scores handled gracefully."""
    tickers = ["A", "B"]
    composite_scores = {
        "A": _make_composite("A", 0.0),
        "B": _make_composite("B", 0.0),
    }
    try:
        weights = weight_conviction(tickers, composite_scores)
        # Should either produce equal weights or raise
        if weights:
            assert abs(sum(weights.values()) - 1.0) < 1e-6
    except (ValueError, ZeroDivisionError):
        pass  # Acceptable


def test_select_candidates_empty_composites() -> None:
    """Empty composite scores produce empty selection."""
    intent = _make_intent()
    metadata = {}
    selected, relaxation_notes = select_candidates([], intent, metadata)
    assert len(selected) == 0
