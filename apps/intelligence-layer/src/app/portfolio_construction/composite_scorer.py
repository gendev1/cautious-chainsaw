"""Composite scorer: seven-step pipeline combining factor and theme scores."""
from __future__ import annotations

import math
from typing import Any

from app.portfolio_construction.config import DEFAULT_COMPOSITE_PARAMS
from app.portfolio_construction.models import CompositeScoreResult, ParsedIntent


def score_composite(
    factor_scores: dict[str, dict],
    theme_scores: dict[str, dict],
    intent: ParsedIntent,
    params: dict[str, float] | None = None,
) -> list[CompositeScoreResult]:
    """
    Score all tickers through the seven-step composite pipeline.

    Steps:
    1. Hard exclusion gate
    2. Anti-goal gate
    3. Eligibility gates (factor floor, theme floor)
    4. Uncertainty adjustment
    5. Weighted geometric mean
    6. Coherence bonus / weak-link penalty
    7. Clamp to [0, 100]

    Returns results sorted by composite_score descending.
    """
    p = dict(DEFAULT_COMPOSITE_PARAMS)
    if params:
        p.update(params)

    theme_weight = intent.theme_weight if intent.theme_weight else p["theme_weight"]
    factor_floor = p["factor_floor"]
    min_theme_score = p["min_theme_score"]
    theme_confidence_floor = p["theme_confidence_floor"]
    interaction_bonus = p["interaction_bonus"]
    weak_link_gap = p["weak_link_gap"]
    weak_link_penalty = p["weak_link_penalty"]

    # Speculative intent overrides
    if intent.speculative:
        factor_floor = min(factor_floor, 10.0)
        min_theme_score = max(min_theme_score, 35.0)

    excluded = set(intent.intent_constraints.excluded_tickers)

    # Only process tickers that have theme scores (recall pool).
    # Tickers outside the recall pool get composite=0 and are not eligible.
    # This prevents random non-theme stocks from sneaking into the portfolio.
    theme_scored_tickers = set(theme_scores.keys())
    all_tickers = set(factor_scores.keys()) | theme_scored_tickers

    results: list[CompositeScoreResult] = []

    for ticker in all_tickers:
        fs_data = factor_scores.get(ticker, {})
        ts_data = theme_scores.get(ticker, {})

        factor_score = fs_data.get("overall_score", 0.0) if isinstance(fs_data, dict) else 0.0
        reliability = fs_data.get("reliability", 1.0) if isinstance(fs_data, dict) else 1.0

        # Tickers not in the recall pool (no theme score) get zeroed out
        if ticker not in theme_scored_tickers:
            results.append(CompositeScoreResult(
                ticker=ticker,
                composite_score=0.0,
                factor_score=factor_score,
                theme_score=0.0,
                gated=True,
                gate_reason="Not in recall pool",
                coherence_bonus=0.0,
                weak_link_penalty=0.0,
            ))
            continue

        theme_score_raw = ts_data.get("score", 0.0) if isinstance(ts_data, dict) else 0.0
        confidence = ts_data.get("confidence", 1.0) if isinstance(ts_data, dict) else 1.0
        anti_goal_hit = ts_data.get("anti_goal_hit", False) if isinstance(ts_data, dict) else False

        # Step 1: Hard exclusion gate
        if ticker in excluded:
            results.append(CompositeScoreResult(
                ticker=ticker,
                composite_score=0.0,
                factor_score=factor_score,
                theme_score=theme_score_raw,
                gated=True,
                gate_reason="Excluded ticker",
                coherence_bonus=0.0,
                weak_link_penalty=0.0,
            ))
            continue

        # Step 2: Anti-goal gate
        if anti_goal_hit:
            results.append(CompositeScoreResult(
                ticker=ticker,
                composite_score=0.0,
                factor_score=factor_score,
                theme_score=theme_score_raw,
                gated=True,
                gate_reason="Anti-goal hit",
                coherence_bonus=0.0,
                weak_link_penalty=0.0,
            ))
            continue

        # Step 3: Eligibility gates
        if factor_score < factor_floor:
            results.append(CompositeScoreResult(
                ticker=ticker,
                composite_score=0.0,
                factor_score=factor_score,
                theme_score=theme_score_raw,
                gated=True,
                gate_reason=f"Below factor floor ({factor_score:.1f} < {factor_floor:.1f})",
                coherence_bonus=0.0,
                weak_link_penalty=0.0,
            ))
            continue

        if theme_score_raw < min_theme_score and ticker in theme_scores:
            results.append(CompositeScoreResult(
                ticker=ticker,
                composite_score=0.0,
                factor_score=factor_score,
                theme_score=theme_score_raw,
                gated=True,
                gate_reason=f"Below theme floor ({theme_score_raw:.1f} < {min_theme_score:.1f})",
                coherence_bonus=0.0,
                weak_link_penalty=0.0,
            ))
            continue

        # Step 4: Uncertainty adjustment
        adjusted_theme = theme_score_raw
        if confidence < theme_confidence_floor:
            # Shrink toward 50
            shrink_factor = confidence / theme_confidence_floor
            adjusted_theme = 50.0 + shrink_factor * (theme_score_raw - 50.0)

        adjusted_factor = factor_score
        if reliability < 0.50:
            shrink_factor = reliability / 0.50
            adjusted_factor = 50.0 + shrink_factor * (factor_score - 50.0)

        # Step 5: Weighted geometric mean
        safe_factor = max(adjusted_factor, 0.01)
        safe_theme = max(adjusted_theme, 0.01)
        factor_weight = 1.0 - theme_weight
        composite = math.pow(safe_factor, factor_weight) * math.pow(safe_theme, theme_weight)

        # Step 6: Coherence bonus and weak-link penalty
        bonus = 0.0
        penalty = 0.0

        if adjusted_factor >= 70.0 and adjusted_theme >= 70.0:
            bonus = interaction_bonus

        gap = abs(adjusted_factor - adjusted_theme)
        if gap >= weak_link_gap:
            penalty = weak_link_penalty

        composite = composite + bonus - penalty

        # Step 7: Clamp to [0, 100]
        composite = max(0.0, min(100.0, composite))

        results.append(CompositeScoreResult(
            ticker=ticker,
            composite_score=round(composite, 2),
            factor_score=factor_score,
            theme_score=theme_score_raw,
            gated=False,
            gate_reason=None,
            coherence_bonus=bonus,
            weak_link_penalty=penalty,
        ))

    # Sort by composite_score descending
    results.sort(key=lambda r: r.composite_score, reverse=True)

    return results
