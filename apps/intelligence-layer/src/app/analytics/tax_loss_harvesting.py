"""
Tax-loss harvesting scorer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np  # noqa: F401

from app.analytics.registry import (
    ModelCategory,
    ModelKind,
    ModelMetadata,
)

# -------------------------------------------------------------------
# Replacement map — maps a ticker to approved tax-loss substitutes.
# In production this would be loaded from a config file or
# platform API.
# -------------------------------------------------------------------

DEFAULT_REPLACEMENT_MAP: dict[str, list[str]] = {
    "SPY": ["IVV", "VOO"],
    "IVV": ["SPY", "VOO"],
    "VOO": ["SPY", "IVV"],
    "QQQ": ["QQQM", "VGT"],
    "QQQM": ["QQQ", "VGT"],
    "VGT": ["QQQ", "QQQM"],
    "EFA": ["IEFA", "VEA"],
    "IEFA": ["EFA", "VEA"],
    "VEA": ["EFA", "IEFA"],
    "AGG": ["BND", "SCHZ"],
    "BND": ["AGG", "SCHZ"],
    "XLE": ["VDE", "IYE"],
    "VDE": ["XLE", "IYE"],
    "XLF": ["VFH", "IYF"],
    "VFH": ["XLF", "IYF"],
    "AAPL": [],
    "MSFT": [],
    "GOOGL": [],
    "AMZN": [],
    "TSLA": [],
}


@dataclass
class TaxLot:
    """One tax lot within a holding."""

    lot_id: str
    ticker: str
    shares: float
    cost_basis_per_share: float
    current_price: float
    acquisition_date: date
    account_id: str


@dataclass
class RecentTrade:
    """A buy or sell that occurred in the wash-sale window."""

    ticker: str
    trade_date: date
    direction: str  # "buy" | "sell"
    account_id: str


@dataclass
class HarvestCandidate:
    lot_id: str
    ticker: str
    shares: float
    unrealized_loss: float
    is_long_term: bool
    estimated_tax_saving: float
    wash_sale_blocked: bool
    wash_sale_reason: str | None
    replacement_candidates: list[str]
    score: float  # composite impact score


class TaxLossHarvestingScorer:
    """
    Deterministic model: identify, score, and rank tax-loss
    harvesting opportunities across a set of tax lots.
    """

    metadata = ModelMetadata(
        name="tax_loss_harvesting",
        version="1.0.0",
        owner="portfolio-analytics",
        category=ModelCategory.TAX,
        kind=ModelKind.DETERMINISTIC,
        description=(
            "Scan holdings for unrealized losses, calculate "
            "estimated tax savings, check wash-sale windows, "
            "and find replacement candidates."
        ),
        use_case=(
            "Identify actionable tax-loss harvesting "
            "opportunities for a client."
        ),
        input_freshness_seconds=86_400,
        known_limitations=(
            "Does not model state-level taxes.",
            "Wash-sale check is intra-account only unless "
            "cross-account lots are supplied.",
            "Replacement map is static; does not consider "
            "tracking error.",
        ),
    )

    def __init__(
        self,
        replacement_map: dict[str, list[str]] | None = None,
        min_loss_threshold: float = 100.0,
    ) -> None:
        self._replacements = (
            replacement_map or DEFAULT_REPLACEMENT_MAP
        )
        self._min_loss = min_loss_threshold

    # ---------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------

    def score(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """
        inputs:
            lots: list[dict]        — TaxLot-shaped dicts
            recent_trades: list[dict]
            as_of: str              — ISO date
            federal_bracket: float  — e.g. 0.37
            lt_rate: float          — e.g. 0.20
            realized_gains_ytd: float
        """
        as_of = date.fromisoformat(inputs["as_of"])
        lots = [self._parse_lot(d) for d in inputs["lots"]]
        trades = [
            self._parse_trade(d)
            for d in inputs.get("recent_trades", [])
        ]
        bracket = float(inputs.get("federal_bracket", 0.37))
        lt_rate = float(inputs.get("lt_rate", 0.20))
        realized_gains = float(
            inputs.get("realized_gains_ytd", 0.0)
        )

        candidates: list[HarvestCandidate] = []

        for lot in lots:
            unrealized = (
                lot.current_price - lot.cost_basis_per_share
            ) * lot.shares
            if unrealized >= -self._min_loss:
                continue  # not a meaningful loss

            loss_amount = abs(unrealized)
            holding_days = (as_of - lot.acquisition_date).days
            is_long_term = holding_days >= 366

            # Estimate tax saving
            tax_rate = lt_rate if is_long_term else bracket
            estimated_saving = loss_amount * tax_rate

            # Wash-sale check
            blocked, reason = self._check_wash_sale(
                lot.ticker, lot.account_id, as_of, trades
            )

            # Replacement candidates
            replacements = self._replacements.get(
                lot.ticker, []
            )

            # Score
            score = self._compute_score(
                estimated_saving,
                loss_amount,
                realized_gains,
                blocked,
            )

            candidates.append(
                HarvestCandidate(
                    lot_id=lot.lot_id,
                    ticker=lot.ticker,
                    shares=lot.shares,
                    unrealized_loss=round(-unrealized, 2),
                    is_long_term=is_long_term,
                    estimated_tax_saving=round(
                        estimated_saving, 2
                    ),
                    wash_sale_blocked=blocked,
                    wash_sale_reason=reason,
                    replacement_candidates=replacements,
                    score=round(score, 4),
                )
            )

        # Rank by score descending
        candidates.sort(
            key=lambda c: c.score, reverse=True
        )

        total_potential_saving = sum(
            c.estimated_tax_saving
            for c in candidates
            if not c.wash_sale_blocked
        )
        actionable = [
            c for c in candidates if not c.wash_sale_blocked
        ]

        return {
            "as_of": as_of.isoformat(),
            "total_lots_scanned": len(lots),
            "candidates_found": len(candidates),
            "actionable_candidates": len(actionable),
            "total_potential_saving": round(
                total_potential_saving, 2
            ),
            "candidates": [
                self._candidate_to_dict(c)
                for c in candidates
            ],
            "severity": self._overall_severity(
                total_potential_saving
            ),
            "assumptions": [
                f"Federal ordinary rate: {bracket:.0%}",
                f"Long-term CG rate: {lt_rate:.0%}",
                f"Realized gains YTD: ${realized_gains:,.2f}",
            ],
        }

    # ---------------------------------------------------------------
    # Internals
    # ---------------------------------------------------------------

    def _check_wash_sale(
        self,
        ticker: str,
        account_id: str,
        as_of: date,
        trades: list[RecentTrade],
    ) -> tuple[bool, str | None]:
        """Return (blocked, reason) if wash-sale window active."""
        window_start = as_of - timedelta(days=30)
        window_end = as_of + timedelta(days=30)

        for trade in trades:
            if (
                trade.ticker == ticker
                and trade.direction == "buy"
                and window_start
                <= trade.trade_date
                <= window_end
            ):
                return True, (
                    f"Buy of {ticker} on "
                    f"{trade.trade_date.isoformat()} "
                    f"in account {trade.account_id} "
                    f"is within 30-day window"
                )
        return False, None

    @staticmethod
    def _compute_score(
        estimated_saving: float,
        loss_amount: float,
        realized_gains: float,
        wash_sale_blocked: bool,
    ) -> float:
        """
        Composite score balancing:
          - raw dollar saving (primary)
          - whether gains exist to offset (bonus)
          - penalty if wash-sale blocked
        """
        # Normalize saving to 0-100 scale
        saving_score = (
            min(estimated_saving / 50_000, 1.0) * 60
        )

        # Bonus if realized gains exist
        offset_bonus = 0.0
        if realized_gains > 0:
            offset_ratio = min(
                loss_amount / realized_gains, 1.0
            )
            offset_bonus = offset_ratio * 30

        # Urgency (static for now)
        urgency = 10.0

        raw = saving_score + offset_bonus + urgency
        if wash_sale_blocked:
            raw *= 0.1  # still show it, but rank very low
        return raw

    @staticmethod
    def _overall_severity(total_saving: float) -> str:
        if total_saving >= 10_000:
            return "action_needed"
        if total_saving >= 2_000:
            return "warning"
        return "info"

    @staticmethod
    def _parse_lot(d: dict) -> TaxLot:
        return TaxLot(
            lot_id=d["lot_id"],
            ticker=d["ticker"],
            shares=float(d["shares"]),
            cost_basis_per_share=float(
                d["cost_basis_per_share"]
            ),
            current_price=float(d["current_price"]),
            acquisition_date=date.fromisoformat(
                d["acquisition_date"]
            ),
            account_id=d["account_id"],
        )

    @staticmethod
    def _parse_trade(d: dict) -> RecentTrade:
        return RecentTrade(
            ticker=d["ticker"],
            trade_date=date.fromisoformat(d["trade_date"]),
            direction=d["direction"],
            account_id=d["account_id"],
        )

    @staticmethod
    def _candidate_to_dict(c: HarvestCandidate) -> dict:
        return {
            "lot_id": c.lot_id,
            "ticker": c.ticker,
            "shares": c.shares,
            "unrealized_loss": c.unrealized_loss,
            "is_long_term": c.is_long_term,
            "estimated_tax_saving": c.estimated_tax_saving,
            "wash_sale_blocked": c.wash_sale_blocked,
            "wash_sale_reason": c.wash_sale_reason,
            "replacement_candidates": (
                c.replacement_candidates
            ),
            "score": c.score,
        }
