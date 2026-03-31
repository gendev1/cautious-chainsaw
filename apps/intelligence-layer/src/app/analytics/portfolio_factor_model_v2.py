"""
Portfolio Factor Model v2 — deterministic multi-factor scoring.

Six canonical factors: Value, Quality, Growth, Momentum, LowVolatility, Size.
Each factor has sub-factors, peer-bucket normalization, correlation-adjusted
aggregation, reliability shrinkage, and breadth-sensitive caps.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from app.analytics.registry import (
    ModelCategory,
    ModelKind,
    ModelMetadata,
)
from app.portfolio_construction.config import FACTOR_DEFINITIONS


class PortfolioFactorModelV2:
    """Deterministic factor scoring model for portfolio construction."""

    metadata = ModelMetadata(
        name="portfolio_factor_model_v2",
        version="1.0.0",
        owner="portfolio-analytics",
        category=ModelCategory.PORTFOLIO,
        kind=ModelKind.DETERMINISTIC,
        description=(
            "Multi-factor scoring with six canonical factors, peer-bucket "
            "normalization, correlation-adjusted sub-factor aggregation, "
            "reliability shrinkage, and breadth-sensitive caps."
        ),
        use_case=(
            "Score securities across fundamental and price-based factors "
            "to support portfolio construction candidate selection."
        ),
        input_freshness_seconds=86_400,
        known_limitations=(
            "Assumes upstream data quality for fundamentals and prices.",
            "Peer bucket selection relies on sector/industry classification accuracy.",
            "Does not model regime changes or structural breaks.",
            "Single-security universe produces degenerate percentile ranks.",
        ),
    )

    # Sub-factor source: "fundamentals" or "prices" or "securities"
    _SUB_FACTOR_SOURCE = {
        "pe_ratio": "fundamentals",
        "pb_ratio": "fundamentals",
        "free_cash_flow_yield": "fundamentals",
        "dividend_yield": "fundamentals",
        "roe": "fundamentals",
        "roa": "fundamentals",
        "gross_margin": "fundamentals",
        "operating_margin": "fundamentals",
        "net_margin": "fundamentals",
        "current_ratio": "fundamentals",
        "debt_to_equity": "fundamentals",
        "revenue_growth": "fundamentals",
        "earnings_growth": "fundamentals",
        "rnd_intensity": "fundamentals",
        "momentum_3m": "prices",
        "momentum_6m": "prices",
        "momentum_12m": "prices",
        "realized_vol_1y": "prices",
        "beta": "prices",
        "market_cap": "securities",
    }

    def __init__(self) -> None:
        pass

    def score(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """
        Score a universe of securities.

        inputs:
            securities: list[dict] — ticker, name, sector, industry, market_cap
            fundamentals: dict[ticker, dict] — fundamental metrics
            prices: dict[ticker, dict] — price metrics
            preferences: dict — FactorPreferences-like weights
        """
        securities = inputs.get("securities", [])
        fundamentals = inputs.get("fundamentals", {})
        prices = inputs.get("prices", {})
        preferences = inputs.get("preferences", {})

        if not securities:
            return {
                "scores": {},
                "universe_stats": {
                    "coverage": 0.0,
                    "active_factors": [],
                    "effective_weights": {},
                    "deactivated_factors": [],
                },
                "metadata": {
                    "model_version": self.metadata.version,
                    "universe_size": 0,
                },
            }

        tickers = [s["ticker"] for s in securities]
        n = len(tickers)

        # Build peer buckets
        sector_map = {s["ticker"]: s.get("sector", "Unknown") for s in securities}
        industry_map = {s["ticker"]: s.get("industry", "Unknown") for s in securities}

        # Collect raw sub-factor values per ticker
        raw_values: dict[str, dict[str, float | None]] = {}
        for ticker in tickers:
            vals: dict[str, float | None] = {}
            fund = fundamentals.get(ticker, {})
            price = prices.get(ticker, {})
            sec = next((s for s in securities if s["ticker"] == ticker), {})

            for sf, source in self._SUB_FACTOR_SOURCE.items():
                data_dict = fund if source == "fundamentals" else (price if source == "prices" else sec)
                v = data_dict.get(sf)
                if v is not None:
                    try:
                        vals[sf] = float(v)
                    except (TypeError, ValueError):
                        vals[sf] = None
                else:
                    vals[sf] = None
            raw_values[ticker] = vals

        # Score each factor
        factor_scores_per_ticker: dict[str, dict[str, float]] = {t: {} for t in tickers}
        factor_reliability: dict[str, dict[str, float]] = {t: {} for t in tickers}
        factor_sub_factor_counts: dict[str, dict[str, int]] = {t: {} for t in tickers}

        active_factors: list[str] = []
        deactivated_factors: list[str] = []

        for factor_name, factor_def in FACTOR_DEFINITIONS.items():
            sub_factors = factor_def["sub_factors"]
            lower_is_better = set(factor_def.get("lower_is_better", []))

            # Check coverage for this factor
            coverage_count = 0
            for ticker in tickers:
                available = sum(1 for sf in sub_factors if raw_values[ticker].get(sf) is not None)
                if available > 0:
                    coverage_count += 1

            coverage = coverage_count / n if n > 0 else 0.0
            viable_sub_factors = self._count_viable_sub_factors(tickers, raw_values, sub_factors)

            if coverage < 0.60 or viable_sub_factors < 3:
                deactivated_factors.append(factor_name)
                continue

            active_factors.append(factor_name)

            # For each sub-factor, compute peer-bucket percentile ranks
            for sf in sub_factors:
                # Get values for tickers that have this sub-factor
                sf_values: dict[str, float] = {}
                for ticker in tickers:
                    v = raw_values[ticker].get(sf)
                    if v is not None:
                        sf_values[ticker] = v

                if len(sf_values) < 2:
                    # Not enough data for this sub-factor
                    continue

                # Peer bucket selection: industry (>=15), sector (>=25), universe
                for ticker in tickers:
                    if ticker not in sf_values:
                        continue

                    peer_values = self._get_peer_values(
                        ticker, sf, sf_values, sector_map, industry_map
                    )

                    # Winsorize at 5th/95th percentile
                    peer_arr = np.array(list(peer_values.values()))
                    p5, p95 = np.percentile(peer_arr, [5, 95]) if len(peer_arr) > 1 else (peer_arr[0], peer_arr[0])
                    clipped_val = max(p5, min(p95, sf_values[ticker]))

                    # Compute percentile rank within peers
                    if len(peer_arr) > 1:
                        # If all values are identical, rank is 0.5
                        if np.all(peer_arr == peer_arr[0]):
                            rank = 0.5
                        else:
                            # Mean rank for tied values
                            below = np.sum(peer_arr < clipped_val)
                            equal = np.sum(peer_arr == clipped_val)
                            rank = (below + 0.5 * equal) / len(peer_arr)
                    else:
                        rank = 0.5

                    # Map to [-1, 1]
                    normalized = 2.0 * rank - 1.0

                    # Invert lower-is-better
                    if sf in lower_is_better:
                        normalized = -normalized

                    # Store as 0-100 scale
                    score_01 = (normalized + 1.0) / 2.0  # 0 to 1
                    factor_scores_per_ticker[ticker].setdefault(f"_sf_{factor_name}_{sf}", score_01 * 100.0)

            # Aggregate sub-factors per factor per ticker
            for ticker in tickers:
                sf_scores = []
                sf_keys = []
                for sf in sub_factors:
                    key = f"_sf_{factor_name}_{sf}"
                    if key in factor_scores_per_ticker[ticker]:
                        sf_scores.append(factor_scores_per_ticker[ticker][key])
                        sf_keys.append(key)

                num_sf = len(sf_scores)
                factor_sub_factor_counts[ticker][factor_name] = num_sf

                if num_sf == 0:
                    factor_scores_per_ticker[ticker][factor_name] = 50.0
                    factor_reliability[ticker][factor_name] = 0.0
                    continue

                # Correlation-adjusted aggregation
                weights = self._correlation_adjusted_weights(ticker, sf_keys, factor_scores_per_ticker, tickers)
                factor_score = sum(w * s for w, s in zip(weights, sf_scores))

                # Reliability from coverage and sub-factor count
                sf_coverage = num_sf / max(len(sub_factors), 1)
                reliability = min(1.0, sf_coverage * (0.5 + 0.5 * min(num_sf / 3.0, 1.0)))

                # Shrink toward 50 based on reliability
                shrunk = 50.0 + reliability * (factor_score - 50.0)

                # Breadth caps
                if num_sf == 1:
                    shrunk = min(shrunk, 65.0)
                else:
                    # Compute supportive share
                    above_50 = sum(1 for s in sf_scores if s >= 50.0)
                    supportive_share = above_50 / num_sf if num_sf > 0 else 0
                    if supportive_share < 0.50:
                        shrunk = min(shrunk, 75.0)

                factor_scores_per_ticker[ticker][factor_name] = shrunk
                factor_reliability[ticker][factor_name] = reliability

        # Compute overall score: weighted geometric mean across active factors
        pref_weights = self._normalize_preferences(preferences, active_factors)

        scores_output: dict[str, dict[str, Any]] = {}
        for ticker in tickers:
            active_scores = []
            active_weights = []
            per_factor = {}

            for factor_name in active_factors:
                fscore = factor_scores_per_ticker[ticker].get(factor_name, 50.0)
                per_factor[factor_name] = round(fscore, 2)
                w = pref_weights.get(factor_name, 0.0)
                if w > 0:
                    active_scores.append(max(fscore, 0.01))  # avoid log(0)
                    active_weights.append(w)

            if active_scores and active_weights:
                # Weighted geometric mean
                total_w = sum(active_weights)
                if total_w > 0:
                    log_sum = sum(w * math.log(s) for w, s in zip(active_weights, active_scores))
                    overall = math.exp(log_sum / total_w)
                else:
                    overall = 50.0
            else:
                overall = 50.0

            overall = max(0.0, min(100.0, overall))

            # Per-ticker reliability
            reliabilities = [factor_reliability[ticker].get(f, 0.0) for f in active_factors]
            avg_reliability = sum(reliabilities) / len(reliabilities) if reliabilities else 0.0

            # Sub-factor coverage
            total_sf = sum(factor_sub_factor_counts[ticker].get(f, 0) for f in active_factors)
            max_sf = sum(len(FACTOR_DEFINITIONS[f]["sub_factors"]) for f in active_factors)
            sf_coverage = total_sf / max_sf if max_sf > 0 else 0.0

            scores_output[ticker] = {
                "ticker": ticker,
                "overall_score": round(overall, 2),
                "per_factor_scores": per_factor,
                "reliability": round(avg_reliability, 4),
                "sub_factor_coverage": round(sf_coverage, 4),
            }

        return {
            "scores": scores_output,
            "universe_stats": {
                "coverage": len(scores_output) / n if n > 0 else 0.0,
                "active_factors": active_factors,
                "effective_weights": {k: round(v, 4) for k, v in pref_weights.items()},
                "deactivated_factors": deactivated_factors,
            },
            "metadata": {
                "model_version": self.metadata.version,
                "universe_size": n,
                "factor_count": len(active_factors),
            },
        }

    def _count_viable_sub_factors(
        self, tickers: list[str], raw_values: dict, sub_factors: list[str]
    ) -> int:
        """Count sub-factors with data for >= 60% of tickers."""
        n = len(tickers)
        count = 0
        for sf in sub_factors:
            available = sum(1 for t in tickers if raw_values[t].get(sf) is not None)
            if available / max(n, 1) >= 0.60:
                count += 1
        return count

    def _get_peer_values(
        self,
        ticker: str,
        sf: str,
        sf_values: dict[str, float],
        sector_map: dict[str, str],
        industry_map: dict[str, str],
    ) -> dict[str, float]:
        """Select peer bucket: industry (>=15), sector (>=25), or full universe."""
        industry = industry_map.get(ticker, "Unknown")
        sector = sector_map.get(ticker, "Unknown")

        # Try industry peers
        industry_peers = {t: v for t, v in sf_values.items() if industry_map.get(t) == industry}
        if len(industry_peers) >= 15:
            return industry_peers

        # Try sector peers
        sector_peers = {t: v for t, v in sf_values.items() if sector_map.get(t) == sector}
        if len(sector_peers) >= 25:
            return sector_peers

        # Fall back to full universe
        return sf_values

    def _correlation_adjusted_weights(
        self,
        ticker: str,
        sf_keys: list[str],
        all_scores: dict[str, dict[str, float]],
        all_tickers: list[str],
    ) -> list[float]:
        """Compute correlation-adjusted weights for sub-factors."""
        n_sf = len(sf_keys)
        if n_sf <= 1:
            return [1.0]

        # Build matrix of sub-factor values across tickers
        matrix = []
        for key in sf_keys:
            col = []
            for t in all_tickers:
                val = all_scores[t].get(key)
                if val is not None:
                    col.append(val)
                else:
                    col.append(50.0)
            matrix.append(col)

        matrix_arr = np.array(matrix)
        if matrix_arr.shape[1] < 2:
            return [1.0 / n_sf] * n_sf

        # Compute pairwise correlations
        try:
            corr = np.corrcoef(matrix_arr)
            if np.any(np.isnan(corr)):
                return [1.0 / n_sf] * n_sf
        except Exception:
            return [1.0 / n_sf] * n_sf

        # Adjust weights: adj_weight_i = 1 / (1 + mean_abs_corr_i)
        adj_weights = []
        for i in range(n_sf):
            others_corr = [abs(corr[i, j]) for j in range(n_sf) if j != i]
            mean_corr = sum(others_corr) / len(others_corr) if others_corr else 0.0
            adj_weights.append(1.0 / (1.0 + mean_corr))

        total = sum(adj_weights)
        if total > 0:
            return [w / total for w in adj_weights]
        return [1.0 / n_sf] * n_sf

    def _normalize_preferences(
        self, preferences: dict[str, float], active_factors: list[str]
    ) -> dict[str, float]:
        """Normalize preferences to only active factors summing to 1."""
        weights = {f: preferences.get(f, FACTOR_DEFINITIONS.get(f, {}).get("weight", 0.0)) for f in active_factors}
        total = sum(weights.values())
        if total > 0:
            return {f: w / total for f, w in weights.items()}
        n = len(active_factors)
        return {f: 1.0 / n for f in active_factors} if n > 0 else {}
