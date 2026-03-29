"""
Concentration risk scorer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from app.analytics.registry import (
    ModelCategory,
    ModelKind,
    ModelMetadata,
)


@dataclass
class PositionFlag:
    ticker: str
    weight: float
    threshold: float
    excess: float
    flag_type: str  # "single_stock" | "sector" | "geography"


class ConcentrationRiskScorer:
    """
    Deterministic model: compute HHI, flag single-stock and
    sector concentrations, and score geographic exposure.
    """

    metadata = ModelMetadata(
        name="concentration_risk",
        version="1.0.0",
        owner="portfolio-analytics",
        category=ModelCategory.PORTFOLIO,
        kind=ModelKind.DETERMINISTIC,
        description=(
            "HHI calculation, single-stock and sector "
            "threshold checks, and geographic exposure "
            "scoring."
        ),
        use_case=(
            "Identify concentrated positions that may "
            "warrant rebalancing."
        ),
        input_freshness_seconds=86_400,
        known_limitations=(
            "Sector and geography classifications depend "
            "on upstream data quality.",
            "Does not account for derivative overlays "
            "or hedges.",
        ),
    )

    def __init__(
        self,
        single_stock_threshold: float = 0.10,
        sector_threshold: float = 0.30,
        geo_threshold: float = 0.50,
    ) -> None:
        self._stock_thresh = single_stock_threshold
        self._sector_thresh = sector_threshold
        self._geo_thresh = geo_threshold

    def score(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        inputs:
            holdings: list[dict]  — each has:
                ticker, market_value, sector, country
            total_portfolio_value: float
            as_of: str
        """
        holdings = inputs["holdings"]
        total_value = float(inputs["total_portfolio_value"])
        as_of = inputs["as_of"]

        if total_value <= 0 or not holdings:
            return {
                "as_of": as_of,
                "hhi": 0.0,
                "severity": "info",
                "flags": [],
                "sector_weights": {},
                "geo_weights": {},
            }

        # --- Position-level weights and HHI ---
        values = np.array(
            [float(h["market_value"]) for h in holdings]
        )
        weights = values / total_value
        hhi = float(np.sum(weights**2))

        flags: list[PositionFlag] = []

        # --- Single-stock check ---
        for holding, w in zip(holdings, weights, strict=False):
            if w > self._stock_thresh:
                flags.append(
                    PositionFlag(
                        ticker=holding["ticker"],
                        weight=round(float(w), 4),
                        threshold=self._stock_thresh,
                        excess=round(
                            float(w) - self._stock_thresh,
                            4,
                        ),
                        flag_type="single_stock",
                    )
                )

        # --- Sector aggregation ---
        sector_values: dict[str, float] = {}
        for holding in holdings:
            sector = holding.get("sector", "Unknown")
            sector_values[sector] = (
                sector_values.get(sector, 0.0)
                + float(holding["market_value"])
            )

        sector_weights: dict[str, float] = {
            s: round(v / total_value, 4)
            for s, v in sector_values.items()
        }

        for sector, sw in sector_weights.items():
            if sw > self._sector_thresh:
                flags.append(
                    PositionFlag(
                        ticker=sector,
                        weight=sw,
                        threshold=self._sector_thresh,
                        excess=round(
                            sw - self._sector_thresh, 4
                        ),
                        flag_type="sector",
                    )
                )

        # --- Geographic aggregation ---
        geo_values: dict[str, float] = {}
        for holding in holdings:
            country = holding.get("country", "US")
            geo_values[country] = (
                geo_values.get(country, 0.0)
                + float(holding["market_value"])
            )

        geo_weights: dict[str, float] = {
            g: round(v / total_value, 4)
            for g, v in geo_values.items()
        }

        for geo, gw in geo_weights.items():
            if gw > self._geo_thresh:
                flags.append(
                    PositionFlag(
                        ticker=geo,
                        weight=gw,
                        threshold=self._geo_thresh,
                        excess=round(
                            gw - self._geo_thresh, 4
                        ),
                        flag_type="geography",
                    )
                )

        # --- HHI interpretation ---
        effective_positions = (
            1.0 / hhi if hhi > 0 else len(holdings)
        )

        severity = self._compute_severity(hhi, flags)

        # --- Overall concentration score (0-100) ---
        hhi_score = min(hhi / 0.25, 1.0) * 50
        flag_score = min(len(flags) / 5, 1.0) * 30
        excess_score = (
            min(
                sum(f.excess for f in flags) / 0.50, 1.0
            )
            * 20
        )
        concentration_score = round(
            hhi_score + flag_score + excess_score, 2
        )

        return {
            "as_of": as_of,
            "hhi": round(hhi, 6),
            "effective_positions": round(
                effective_positions, 1
            ),
            "concentration_score": concentration_score,
            "severity": severity,
            "flags": [
                {
                    "ticker": f.ticker,
                    "weight": f.weight,
                    "threshold": f.threshold,
                    "excess": f.excess,
                    "flag_type": f.flag_type,
                }
                for f in flags
            ],
            "sector_weights": sector_weights,
            "geo_weights": geo_weights,
            "thresholds": {
                "single_stock": self._stock_thresh,
                "sector": self._sector_thresh,
                "geography": self._geo_thresh,
            },
        }

    @staticmethod
    def _compute_severity(
        hhi: float, flags: list[PositionFlag]
    ) -> str:
        if hhi > 0.18 or any(
            f.excess > 0.15 for f in flags
        ):
            return "action_needed"
        if hhi > 0.10 or flags:
            return "warning"
        return "info"
