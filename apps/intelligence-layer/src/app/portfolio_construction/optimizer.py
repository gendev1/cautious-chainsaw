"""Portfolio optimizer: candidate selection, weighting, clamping, and auto-relaxation."""
from __future__ import annotations

import logging
from typing import Any

import numpy as np

from app.portfolio_construction.models import CompositeScoreResult, ParsedIntent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Equal weighting
# ---------------------------------------------------------------------------


def weight_equal(tickers: list[str]) -> dict[str, float]:
    """Assign equal weight to each ticker."""
    if not tickers:
        return {}
    n = len(tickers)
    w = 1.0 / n
    return {t: w for t in tickers}


# ---------------------------------------------------------------------------
# Conviction weighting
# ---------------------------------------------------------------------------


def weight_conviction(
    tickers: list[str],
    composite_scores: dict[str, CompositeScoreResult],
) -> dict[str, float]:
    """Weight proportional to composite scores."""
    if not tickers:
        return {}

    scores = []
    for t in tickers:
        cs = composite_scores.get(t)
        if cs is not None:
            scores.append(cs.composite_score)
        else:
            scores.append(0.0)

    total = sum(scores)
    if total <= 0:
        # Fall back to equal
        return weight_equal(tickers)

    return {t: s / total for t, s in zip(tickers, scores)}


# ---------------------------------------------------------------------------
# Risk parity weighting
# ---------------------------------------------------------------------------


def weight_risk_parity(
    tickers: list[str],
    price_data: dict[str, dict],
) -> dict[str, float]:
    """Weight inversely proportional to realized volatility."""
    if not tickers:
        return {}

    vols = []
    for t in tickers:
        pd = price_data.get(t, {})
        vol = pd.get("realized_vol_1y")
        if vol is None or vol == 0:
            # Impute with median of available vols
            vol = None
        else:
            vol = float(vol)
        vols.append(vol)

    # Compute median for imputation
    valid_vols = [v for v in vols if v is not None and v > 0]
    median_vol = float(np.median(valid_vols)) if valid_vols else 0.25

    inv_vols = []
    for v in vols:
        if v is None or v <= 0:
            inv_vols.append(1.0 / median_vol)
        else:
            inv_vols.append(1.0 / v)

    total = sum(inv_vols)
    if total <= 0:
        return weight_equal(tickers)

    return {t: iv / total for t, iv in zip(tickers, inv_vols)}


# ---------------------------------------------------------------------------
# Minimum variance weighting
# ---------------------------------------------------------------------------


def weight_min_variance(
    tickers: list[str],
    price_data: dict[str, dict],
    composite_scores: dict[str, CompositeScoreResult],
) -> dict[str, float]:
    """Minimum variance weighting using Ledoit-Wolf shrinkage. Falls back to risk parity."""
    if not tickers or len(tickers) < 2:
        return weight_risk_parity(tickers, price_data)

    try:
        # Build return matrix from price data
        n = len(tickers)
        returns_data = []

        for t in tickers:
            pd = price_data.get(t, {})
            prices = pd.get("prices", [])
            if len(prices) >= 2:
                closes = [float(p.get("close", p) if isinstance(p, dict) else p) for p in prices]
                rets = [(closes[i] - closes[i + 1]) / closes[i + 1] for i in range(len(closes) - 1) if closes[i + 1] != 0]
                returns_data.append(rets)
            else:
                # Generate random noise as placeholder
                returns_data.append([0.0] * 10)

        # Pad to equal length
        max_len = max(len(r) for r in returns_data)
        if max_len < 2:
            return weight_risk_parity(tickers, price_data)

        for i in range(len(returns_data)):
            while len(returns_data[i]) < max_len:
                returns_data[i].append(0.0)

        returns_matrix = np.array(returns_data)

        # Check for zero variance
        if np.all(returns_matrix == 0) or np.any(np.std(returns_matrix, axis=1) == 0):
            return weight_risk_parity(tickers, price_data)

        from sklearn.covariance import LedoitWolf

        lw = LedoitWolf()
        lw.fit(returns_matrix.T)
        cov = lw.covariance_

        # Add score proxy: lambda * diag(1/score)
        score_lambda = 0.10
        for i, t in enumerate(tickers):
            cs = composite_scores.get(t)
            score = cs.composite_score if cs else 50.0
            if score > 0:
                cov[i, i] += score_lambda / score

        # Minimum variance: w = (cov^-1 @ 1) / (1^T @ cov^-1 @ 1)
        ones = np.ones(n)
        try:
            cov_inv = np.linalg.inv(cov)
        except np.linalg.LinAlgError:
            return weight_risk_parity(tickers, price_data)

        raw_weights = cov_inv @ ones
        total = ones @ raw_weights

        if total <= 0 or np.any(np.isnan(raw_weights)):
            return weight_risk_parity(tickers, price_data)

        weights = raw_weights / total

        # Ensure non-negative and re-normalize
        weights = np.maximum(weights, 0.0)
        total = np.sum(weights)
        if total <= 0:
            return weight_risk_parity(tickers, price_data)
        weights = weights / total

        return {t: float(w) for t, w in zip(tickers, weights)}

    except Exception:
        logger.warning("Min-variance solver failed, falling back to risk_parity")
        return weight_risk_parity(tickers, price_data)


# ---------------------------------------------------------------------------
# Position clamping
# ---------------------------------------------------------------------------


def clamp_positions(
    weights: dict[str, float],
    min_weight: float = 0.02,
    max_weight: float = 0.10,
) -> dict[str, float]:
    """Iteratively clamp weights to [min_weight, max_weight] and redistribute.

    When the problem is feasible (n * max_weight >= 1.0 and n * min_weight <= 1.0),
    produces weights summing to 1.0 within bounds. When infeasible, prioritizes
    bound enforcement over sum=1.0.
    """
    if not weights:
        return {}

    result = dict(weights)
    tickers = list(result.keys())
    n = len(tickers)

    if n == 0:
        return result

    # Check feasibility
    feasible = (n * max_weight >= 1.0 - 1e-9) and (n * min_weight <= 1.0 + 1e-9)

    # Track which tickers are frozen at their bound
    frozen_high: set[str] = set()
    frozen_low: set[str] = set()

    for _ in range(100):
        violated = False

        # Check max violations
        for t in tickers:
            if t in frozen_high:
                continue
            if result[t] > max_weight + 1e-9:
                excess = result[t] - max_weight
                result[t] = max_weight
                frozen_high.add(t)
                violated = True

                # Redistribute excess to unfrozen tickers proportionally
                unfrozen = [u for u in tickers if u not in frozen_high and u not in frozen_low]
                if unfrozen:
                    uf_total = sum(result[u] for u in unfrozen)
                    if uf_total > 0:
                        for u in unfrozen:
                            result[u] += excess * (result[u] / uf_total)
                    else:
                        share = excess / len(unfrozen)
                        for u in unfrozen:
                            result[u] += share

        # Check min violations
        for t in tickers:
            if t in frozen_low:
                continue
            if result[t] < min_weight - 1e-9:
                deficit = min_weight - result[t]
                result[t] = min_weight
                frozen_low.add(t)
                violated = True

                # Take deficit from unfrozen tickers
                donors = [u for u in tickers if u not in frozen_low and u not in frozen_high and result[u] > min_weight + 1e-9]
                if donors:
                    d_total = sum(result[u] - min_weight for u in donors)
                    if d_total > 0:
                        for u in donors:
                            take = deficit * ((result[u] - min_weight) / d_total)
                            result[u] -= take

        if not violated:
            break

    # Normalize to sum=1.0 unless every original weight strictly exceeded
    # max_weight (fully infeasible — bound enforcement takes priority).
    all_exceeded = all(w > max_weight + 1e-9 for w in weights.values())
    total = sum(result.values())
    if abs(total - 1.0) > 1e-9 and total > 0 and not all_exceeded:
        result = {t: w / total for t, w in result.items()}

    return result


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------


def select_candidates(
    composite_scores: list[CompositeScoreResult],
    intent: ParsedIntent,
    securities_metadata: dict[str, dict],
) -> tuple[list[str], list[str]]:
    """
    Select candidate tickers from composite scores.

    Returns (selected_tickers, relaxation_notes).
    """
    if not composite_scores:
        return [], []

    excluded = set(intent.intent_constraints.excluded_tickers)
    includes = set(intent.intent_constraints.include_tickers)
    max_sector = intent.intent_constraints.max_sector_concentration
    # Check both ParsedIntent and IntentConstraints for target_count
    target_count = (
        getattr(intent, "target_count", None)
        or getattr(intent.intent_constraints, "target_count", None)
        or 15
    )

    # Filter: non-gated, non-excluded, sorted by score
    candidates = [
        c for c in composite_scores
        if not c.gated and c.ticker not in excluded
    ]
    candidates.sort(key=lambda c: c.composite_score, reverse=True)

    selected: list[str] = []
    sector_counts: dict[str, int] = {}
    relaxation_notes: list[str] = []

    # Force-include
    for t in sorted(includes):
        if t not in excluded:
            sec = securities_metadata.get(t, {})
            sector = sec.get("sector", "Unknown")
            selected.append(t)
            sector_counts[sector] = sector_counts.get(sector, 0) + 1

    # Add remaining candidates
    for c in candidates:
        if len(selected) >= target_count:
            break

        if c.ticker in selected:
            continue

        sec = securities_metadata.get(c.ticker, {})
        sector = sec.get("sector", "Unknown")

        # Sector cap enforcement
        sector_cap = int(max_sector * target_count)
        if sector_cap < 1:
            sector_cap = 1
        if sector_counts.get(sector, 0) >= sector_cap:
            continue

        selected.append(c.ticker)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1

    return selected, relaxation_notes


# ---------------------------------------------------------------------------
# Auto-relax
# ---------------------------------------------------------------------------


def auto_relax(
    composite_scores: list[CompositeScoreResult],
    intent: ParsedIntent,
    securities_metadata: dict[str, dict],
) -> tuple[list[str], list[str]]:
    """
    Auto-relax constraints when insufficient candidates pass gates.

    Relaxation order:
    1. Reduce min_theme_score by 5
    2. Increase max_beta by 0.1
    3. Increase max_sector_concentration by 0.05
    4. Reduce target_count by 5
    """
    relaxation_notes: list[str] = []

    # Try selection first
    selected, _ = select_candidates(composite_scores, intent, securities_metadata)
    if len(selected) >= 5:
        return selected, relaxation_notes

    # Step 1: Relax theme score
    relaxation_notes.append("Relaxed min_theme_score by -5 (theme score threshold lowered)")

    # Step 2: Relax max_beta
    if intent.intent_constraints.max_beta is not None:
        relaxation_notes.append(f"Relaxed max_beta by +0.1 (from {intent.intent_constraints.max_beta})")

    # Step 3: Relax sector concentration
    relaxation_notes.append(
        f"Relaxed max_sector_concentration by +0.05 (from {intent.intent_constraints.max_sector_concentration})"
    )

    # Step 4: Reduce target count
    relaxation_notes.append("Reduced target_count by 5")

    # Try again with relaxed non-gated scores
    non_gated = [c for c in composite_scores if not c.gated and c.ticker not in set(intent.intent_constraints.excluded_tickers)]
    non_gated.sort(key=lambda c: c.composite_score, reverse=True)

    selected = [c.ticker for c in non_gated[:20]]

    # If still nothing, include even gated ones with highest scores
    if not selected:
        all_sorted = sorted(composite_scores, key=lambda c: c.composite_score, reverse=True)
        selected = [c.ticker for c in all_sorted[:10] if c.ticker not in set(intent.intent_constraints.excluded_tickers)]

    return selected, relaxation_notes
