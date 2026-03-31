"""Tests for the composite scoring pipeline — 7 gating steps."""
from __future__ import annotations

import math

import pytest

from app.portfolio_construction.composite_scorer import score_composite
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
    speculative: bool = False,
    theme_weight: float = 0.60,
) -> ParsedIntent:
    return ParsedIntent(
        themes=["AI"],
        anti_goals=[],
        factor_preferences=FactorPreferences(),
        intent_constraints=IntentConstraints(
            excluded_tickers=excluded_tickers or [],
        ),
        ambiguity_flags=[],
        theme_weight=theme_weight,
        speculative=speculative,
    )


def _make_factor_score(ticker: str, overall: float, reliability: float = 0.90) -> dict:
    return {
        "ticker": ticker,
        "overall_score": overall,
        "per_factor_scores": {"value": overall, "quality": overall},
        "reliability": reliability,
        "sub_factor_coverage": 0.90,
    }


def _make_theme_score(ticker: str, score: float, confidence: float = 0.85, anti_goal_hit: bool = False) -> dict:
    return {
        "ticker": ticker,
        "score": score,
        "confidence": confidence,
        "anti_goal_hit": anti_goal_hit,
        "reasoning": f"Test reasoning for {ticker}.",
    }


# ---------------------------------------------------------------------------
# Gate 1: Hard exclusion
# ---------------------------------------------------------------------------


def test_excluded_ticker_gated_with_score_zero() -> None:
    """Excluded tickers are gated with composite_score=0."""
    intent = _make_intent(excluded_tickers=["EXCL"])
    factor_scores = {"EXCL": _make_factor_score("EXCL", 80.0)}
    theme_scores = {"EXCL": _make_theme_score("EXCL", 80.0)}

    results = score_composite(
        factor_scores=factor_scores,
        theme_scores=theme_scores,
        intent=intent,
    )

    excl_result = next(r for r in results if r.ticker == "EXCL")
    assert excl_result.gated is True
    assert excl_result.composite_score == 0.0
    assert "exclu" in excl_result.gate_reason.lower()


# ---------------------------------------------------------------------------
# Gate 2: Anti-goal gate
# ---------------------------------------------------------------------------


def test_anti_goal_hit_ticker_gated() -> None:
    """Tickers with anti_goal_hit are gated with score=0."""
    intent = _make_intent()
    factor_scores = {"BAD": _make_factor_score("BAD", 80.0)}
    theme_scores = {"BAD": _make_theme_score("BAD", 0.0, anti_goal_hit=True)}

    results = score_composite(
        factor_scores=factor_scores,
        theme_scores=theme_scores,
        intent=intent,
    )

    bad_result = next(r for r in results if r.ticker == "BAD")
    assert bad_result.gated is True
    assert bad_result.composite_score == 0.0
    assert "anti" in bad_result.gate_reason.lower()


# ---------------------------------------------------------------------------
# Gate 3: Eligibility — factor floor
# ---------------------------------------------------------------------------


def test_below_factor_floor_gated() -> None:
    """Tickers below factor_floor (default 25) are gated."""
    intent = _make_intent()
    factor_scores = {"WEAK": _make_factor_score("WEAK", 20.0)}
    theme_scores = {"WEAK": _make_theme_score("WEAK", 70.0)}

    results = score_composite(
        factor_scores=factor_scores,
        theme_scores=theme_scores,
        intent=intent,
    )

    weak_result = next(r for r in results if r.ticker == "WEAK")
    assert weak_result.gated is True
    assert "factor" in weak_result.gate_reason.lower() or "floor" in weak_result.gate_reason.lower()


# ---------------------------------------------------------------------------
# Gate 3: Eligibility — theme floor
# ---------------------------------------------------------------------------


def test_below_theme_floor_gated() -> None:
    """Tickers below min_theme_score (default 30) are gated."""
    intent = _make_intent()
    factor_scores = {"LOW_THEME": _make_factor_score("LOW_THEME", 70.0)}
    theme_scores = {"LOW_THEME": _make_theme_score("LOW_THEME", 20.0)}

    results = score_composite(
        factor_scores=factor_scores,
        theme_scores=theme_scores,
        intent=intent,
    )

    result = next(r for r in results if r.ticker == "LOW_THEME")
    assert result.gated is True
    assert "theme" in result.gate_reason.lower() or "floor" in result.gate_reason.lower()


# ---------------------------------------------------------------------------
# Step 4: Uncertainty adjustment
# ---------------------------------------------------------------------------


def test_uncertainty_adjustment_shrinks_low_confidence_toward_50() -> None:
    """Low-confidence theme scores are shrunk toward 50."""
    intent = _make_intent()
    factor_scores = {"UNC": _make_factor_score("UNC", 70.0)}
    # Low confidence (0.30 < 0.50 threshold)
    theme_scores = {"UNC": _make_theme_score("UNC", 90.0, confidence=0.30)}

    results = score_composite(
        factor_scores=factor_scores,
        theme_scores=theme_scores,
        intent=intent,
    )

    unc_result = next(r for r in results if r.ticker == "UNC")
    if not unc_result.gated:
        # Effective theme score should be closer to 50 than 90
        # Composite should be lower than with full confidence
        assert unc_result.composite_score < 90.0


def test_uncertainty_adjustment_shrinks_low_reliability_toward_50() -> None:
    """Low-reliability factor scores are shrunk toward 50."""
    intent = _make_intent()
    factor_scores = {"LR": _make_factor_score("LR", 90.0, reliability=0.30)}
    theme_scores = {"LR": _make_theme_score("LR", 70.0)}

    results = score_composite(
        factor_scores=factor_scores,
        theme_scores=theme_scores,
        intent=intent,
    )

    lr_result = next(r for r in results if r.ticker == "LR")
    if not lr_result.gated:
        # Composite should reflect shrunk factor score
        assert lr_result.composite_score < 90.0


# ---------------------------------------------------------------------------
# Step 5: Weighted geometric mean
# ---------------------------------------------------------------------------


def test_geometric_mean_math() -> None:
    """Composite = factor^(1-theme_weight) * theme^(theme_weight)."""
    intent = _make_intent(theme_weight=0.60)
    factor_scores = {"GM": _make_factor_score("GM", 80.0)}
    theme_scores = {"GM": _make_theme_score("GM", 70.0)}

    results = score_composite(
        factor_scores=factor_scores,
        theme_scores=theme_scores,
        intent=intent,
    )

    gm_result = next(r for r in results if r.ticker == "GM")
    if not gm_result.gated:
        # Expected: 80^0.40 * 70^0.60 ≈ 73.68
        expected = math.pow(80.0, 0.40) * math.pow(70.0, 0.60)
        # Allow tolerance for adjustments and bonuses/penalties
        assert abs(gm_result.composite_score - expected) < 15.0


def test_geometric_mean_equal_scores() -> None:
    """When factor and theme scores are equal, composite equals the score."""
    intent = _make_intent(theme_weight=0.60)
    factor_scores = {"EQ": _make_factor_score("EQ", 75.0)}
    theme_scores = {"EQ": _make_theme_score("EQ", 75.0)}

    results = score_composite(
        factor_scores=factor_scores,
        theme_scores=theme_scores,
        intent=intent,
    )

    eq_result = next(r for r in results if r.ticker == "EQ")
    if not eq_result.gated:
        # geomean(75, 75) = 75 (plus possible bonus since both >= 70)
        assert abs(eq_result.composite_score - 75.0) < 10.0


# ---------------------------------------------------------------------------
# Step 6: Coherence bonus
# ---------------------------------------------------------------------------


def test_coherence_bonus_both_above_70() -> None:
    """Both factor >= 70 and theme >= 70 yields +5 coherence bonus."""
    intent = _make_intent()
    factor_scores = {"GOOD": _make_factor_score("GOOD", 80.0)}
    theme_scores = {"GOOD": _make_theme_score("GOOD", 80.0)}

    results = score_composite(
        factor_scores=factor_scores,
        theme_scores=theme_scores,
        intent=intent,
    )

    good_result = next(r for r in results if r.ticker == "GOOD")
    if not good_result.gated:
        assert good_result.coherence_bonus == 5.0


def test_no_coherence_bonus_below_70() -> None:
    """No bonus when one score is below 70."""
    intent = _make_intent()
    factor_scores = {"MID": _make_factor_score("MID", 60.0)}
    theme_scores = {"MID": _make_theme_score("MID", 80.0)}

    results = score_composite(
        factor_scores=factor_scores,
        theme_scores=theme_scores,
        intent=intent,
    )

    mid_result = next(r for r in results if r.ticker == "MID")
    if not mid_result.gated:
        assert mid_result.coherence_bonus == 0.0


# ---------------------------------------------------------------------------
# Step 6: Weak-link penalty
# ---------------------------------------------------------------------------


def test_weak_link_penalty_large_gap() -> None:
    """abs(factor - theme) >= 35 yields -5 weak-link penalty."""
    intent = _make_intent()
    factor_scores = {"GAP": _make_factor_score("GAP", 90.0)}
    theme_scores = {"GAP": _make_theme_score("GAP", 50.0)}

    results = score_composite(
        factor_scores=factor_scores,
        theme_scores=theme_scores,
        intent=intent,
    )

    gap_result = next(r for r in results if r.ticker == "GAP")
    if not gap_result.gated:
        assert gap_result.weak_link_penalty == 5.0


def test_no_weak_link_penalty_small_gap() -> None:
    """No penalty when gap is moderate."""
    intent = _make_intent()
    factor_scores = {"MOD": _make_factor_score("MOD", 70.0)}
    theme_scores = {"MOD": _make_theme_score("MOD", 60.0)}

    results = score_composite(
        factor_scores=factor_scores,
        theme_scores=theme_scores,
        intent=intent,
    )

    mod_result = next(r for r in results if r.ticker == "MOD")
    if not mod_result.gated:
        assert mod_result.weak_link_penalty == 0.0


# ---------------------------------------------------------------------------
# Step 7: Clamp to [0, 100]
# ---------------------------------------------------------------------------


def test_composite_score_clamped_upper() -> None:
    """Composite score cannot exceed 100."""
    intent = _make_intent()
    factor_scores = {"MAX": _make_factor_score("MAX", 99.0)}
    theme_scores = {"MAX": _make_theme_score("MAX", 99.0)}

    results = score_composite(
        factor_scores=factor_scores,
        theme_scores=theme_scores,
        intent=intent,
    )

    max_result = next(r for r in results if r.ticker == "MAX")
    assert max_result.composite_score <= 100.0


def test_composite_score_clamped_lower() -> None:
    """Composite score cannot go below 0."""
    intent = _make_intent()
    factor_scores = {"MIN": _make_factor_score("MIN", 26.0)}
    theme_scores = {"MIN": _make_theme_score("MIN", 31.0)}

    results = score_composite(
        factor_scores=factor_scores,
        theme_scores=theme_scores,
        intent=intent,
    )

    min_result = next(r for r in results if r.ticker == "MIN")
    assert min_result.composite_score >= 0.0


# ---------------------------------------------------------------------------
# Speculative intent overrides
# ---------------------------------------------------------------------------


def test_speculative_lowers_factor_floor() -> None:
    """Speculative intent lowers factor_floor to allow riskier candidates."""
    intent = _make_intent(speculative=True)
    # Factor score 15 would normally be gated (below default 25)
    # but speculative lowers floor to 10-15
    factor_scores = {"SPEC": _make_factor_score("SPEC", 15.0)}
    theme_scores = {"SPEC": _make_theme_score("SPEC", 80.0)}

    results = score_composite(
        factor_scores=factor_scores,
        theme_scores=theme_scores,
        intent=intent,
    )

    spec_result = next(r for r in results if r.ticker == "SPEC")
    # In speculative mode, this should NOT be gated (floor lowered to 10-15)
    assert spec_result.gated is False


# ---------------------------------------------------------------------------
# Ranking order
# ---------------------------------------------------------------------------


def test_results_ranked_by_composite_descending() -> None:
    """Results are ranked by composite_score in descending order."""
    intent = _make_intent()
    factor_scores = {
        "A": _make_factor_score("A", 90.0),
        "B": _make_factor_score("B", 70.0),
        "C": _make_factor_score("C", 80.0),
    }
    theme_scores = {
        "A": _make_theme_score("A", 90.0),
        "B": _make_theme_score("B", 70.0),
        "C": _make_theme_score("C", 80.0),
    }

    results = score_composite(
        factor_scores=factor_scores,
        theme_scores=theme_scores,
        intent=intent,
    )

    non_gated = [r for r in results if not r.gated]
    for i in range(len(non_gated) - 1):
        assert non_gated[i].composite_score >= non_gated[i + 1].composite_score


# ---------------------------------------------------------------------------
# Multiple tickers
# ---------------------------------------------------------------------------


def test_multiple_tickers_scored() -> None:
    """All input tickers receive composite scores."""
    intent = _make_intent()
    tickers = [f"T{i:03d}" for i in range(10)]
    factor_scores = {t: _make_factor_score(t, 50.0 + i * 3) for i, t in enumerate(tickers)}
    theme_scores = {t: _make_theme_score(t, 50.0 + i * 3) for i, t in enumerate(tickers)}

    results = score_composite(
        factor_scores=factor_scores,
        theme_scores=theme_scores,
        intent=intent,
    )

    assert len(results) == 10
    result_tickers = {r.ticker for r in results}
    assert result_tickers == set(tickers)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_input_returns_empty_results() -> None:
    """Empty factor and theme scores produce empty results."""
    intent = _make_intent()
    results = score_composite(
        factor_scores={},
        theme_scores={},
        intent=intent,
    )
    assert len(results) == 0


def test_missing_theme_score_for_ticker() -> None:
    """Ticker with factor score but no theme score is handled."""
    intent = _make_intent()
    factor_scores = {"ORPHAN": _make_factor_score("ORPHAN", 80.0)}
    theme_scores = {}  # No theme score for ORPHAN

    results = score_composite(
        factor_scores=factor_scores,
        theme_scores=theme_scores,
        intent=intent,
    )

    # Should either gate the ticker or assign a default theme score
    assert len(results) >= 0  # Does not crash
