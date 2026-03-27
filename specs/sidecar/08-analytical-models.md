# 08 — Analytical Modeling Layer: Implementation Guide

> Deterministic and heuristic models that power portfolio analytics, tax planning,
> and firm-wide reporting inside the Python sidecar.

**Parent spec:** `python-sidecar.md` sections 4.0, 12, Features 10--12

---

## Table of Contents

1. [Model Registry](#1-model-registry)
2. [Tax-Loss Harvesting Scorer](#2-tax-loss-harvesting-scorer)
3. [Concentration Risk Scorer](#3-concentration-risk-scorer)
4. [Drift Detection](#4-drift-detection)
5. [RMD Calculator](#5-rmd-calculator)
6. [Tax Scenario Engine](#6-tax-scenario-engine)
7. [Firm-Wide Opportunity Ranker](#7-firm-wide-opportunity-ranker)
8. [Beneficiary Completeness Audit](#8-beneficiary-completeness-audit)
9. [Cash Drag Detector](#9-cash-drag-detector)
10. [Style Profile Extractor](#10-style-profile-extractor)
11. [Model Governance](#11-model-governance)

---

## 1. Model Registry

Every analytical model in the sidecar is registered centrally. The registry
provides versioned lookup, invocation dispatch, and governance metadata for
every scoring or analytical pipeline.

### 1.1 Design goals

- Single entry point for discovering available models.
- Version pinning: callers request a model by name; the registry resolves to
  the active version. Prior versions remain available for audit replay.
- Every model declares `ModelMetadata` (see section 11) at registration time.
- Thread-safe: the registry is populated at process startup and is read-only
  at request time.

### 1.2 Implementation

```python
"""
sidecar/app/analytics/registry.py

Central model registry for all analytical scoring and scenario models.
"""
from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Protocol, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Model classification
# ---------------------------------------------------------------------------

class ModelKind(str, Enum):
    DETERMINISTIC = "deterministic"
    HEURISTIC = "heuristic"
    LEARNED = "learned"


class ModelCategory(str, Enum):
    TAX = "tax"
    PORTFOLIO = "portfolio"
    COMPLIANCE = "compliance"
    PERSONALIZATION = "personalization"
    FIRM_ANALYTICS = "firm_analytics"


# ---------------------------------------------------------------------------
# Governance metadata — every model must declare this
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelMetadata:
    """Governance declaration attached to every registered model."""
    name: str
    version: str
    owner: str                          # team or individual
    category: ModelCategory
    kind: ModelKind
    description: str
    use_case: str                       # intended decision-support use case
    input_freshness_seconds: int        # max age of inputs before stale
    known_limitations: tuple[str, ...]
    reviewable: bool = True             # can an advisor inspect outputs?
    registered_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


# ---------------------------------------------------------------------------
# Model protocol — the contract every model must satisfy
# ---------------------------------------------------------------------------

class AnalyticalModel(Protocol):
    """Structural protocol that every analytical model must implement."""

    metadata: ModelMetadata

    def score(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Run the model and return a structured result dict."""
        ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class ModelRegistry:
    """
    Singleton-style registry.  Populated at startup via `register()`;
    queried at request time via `get()` or `invoke()`.
    """

    def __init__(self) -> None:
        # key = (name, version), value = model instance
        self._models: dict[tuple[str, str], AnalyticalModel] = {}
        # key = name, value = latest version string
        self._latest: dict[str, str] = {}

    # -- registration ------------------------------------------------------

    def register(self, model: AnalyticalModel) -> None:
        meta = model.metadata
        key = (meta.name, meta.version)
        if key in self._models:
            raise ValueError(f"Model {key} already registered")
        self._models[key] = model
        # Track latest by lexicographic version (semver-friendly)
        if meta.name not in self._latest or meta.version > self._latest[meta.name]:
            self._latest[meta.name] = meta.version
        logger.info("Registered model %s v%s (%s)", meta.name, meta.version, meta.kind.value)

    # -- lookup ------------------------------------------------------------

    def get(self, name: str, version: str | None = None) -> AnalyticalModel:
        """Resolve a model by name and optional version (defaults to latest)."""
        ver = version or self._latest.get(name)
        if ver is None:
            raise KeyError(f"No model registered with name '{name}'")
        key = (name, ver)
        if key not in self._models:
            raise KeyError(f"Model '{name}' version '{ver}' not found")
        return self._models[key]

    # -- invocation --------------------------------------------------------

    def invoke(
        self,
        name: str,
        inputs: dict[str, Any],
        *,
        version: str | None = None,
    ) -> dict[str, Any]:
        """Resolve and score in one call.  Adds `_model` and `_version` to output."""
        model = self.get(name, version)
        result = model.score(inputs)
        result["_model"] = model.metadata.name
        result["_version"] = model.metadata.version
        result["_scored_at"] = datetime.now(timezone.utc).isoformat()
        return result

    # -- introspection -----------------------------------------------------

    def list_models(self) -> list[ModelMetadata]:
        """Return metadata for every registered model (latest versions only)."""
        return [
            self._models[(name, ver)].metadata
            for name, ver in self._latest.items()
        ]

    def list_all_versions(self, name: str) -> list[str]:
        return sorted(
            ver for (n, ver) in self._models if n == name
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry = ModelRegistry()


def get_registry() -> ModelRegistry:
    return _registry
```

### 1.3 Startup wiring

```python
"""
sidecar/app/analytics/startup.py

Called from FastAPI lifespan to populate the registry.
"""
from app.analytics.registry import get_registry
from app.analytics.tax_loss_harvesting import TaxLossHarvestingScorer
from app.analytics.concentration_risk import ConcentrationRiskScorer
from app.analytics.drift_detection import DriftDetector
from app.analytics.rmd_calculator import RMDCalculator
from app.analytics.tax_scenario_engine import TaxScenarioEngine
from app.analytics.firm_ranker import FirmWideOpportunityRanker
from app.analytics.beneficiary_audit import BeneficiaryCompletenessAudit
from app.analytics.cash_drag import CashDragDetector
from app.analytics.style_profile import StyleProfileExtractor


def register_all_models() -> None:
    registry = get_registry()
    registry.register(TaxLossHarvestingScorer())
    registry.register(ConcentrationRiskScorer())
    registry.register(DriftDetector())
    registry.register(RMDCalculator())
    registry.register(TaxScenarioEngine())
    registry.register(FirmWideOpportunityRanker())
    registry.register(BeneficiaryCompletenessAudit())
    registry.register(CashDragDetector())
    registry.register(StyleProfileExtractor())
```

---

## 2. Tax-Loss Harvesting Scorer

Scans holdings for unrealized losses, calculates estimated tax savings at
short-term and long-term capital gains rates, enforces the 30-day wash-sale
window, and identifies replacement candidates from an approved substitution
map.

### 2.1 Financial background

- Short-term capital gains (held < 1 year) are taxed at the ordinary income
  rate (up to 37 %).
- Long-term capital gains (held >= 1 year) are taxed at preferential rates
  (0 / 15 / 20 %).
- Wash-sale rule: if a taxpayer sells a security at a loss and buys a
  "substantially identical" security within 30 calendar days before or after
  the sale, the loss is disallowed.
- Harvested losses can offset realized gains dollar-for-dollar and up to
  $3,000 of ordinary income per year. Remaining losses carry forward.

### 2.2 Implementation

```python
"""
sidecar/app/analytics/tax_loss_harvesting.py
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np

from app.analytics.registry import (
    AnalyticalModel,
    ModelCategory,
    ModelKind,
    ModelMetadata,
)


# ---------------------------------------------------------------------------
# Replacement map — maps a ticker to approved tax-loss substitutes.
# In production this would be loaded from a config file or platform API.
# ---------------------------------------------------------------------------

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
    "AAPL": [],   # single stocks have no "substantially identical" substitute
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
    direction: str          # "buy" | "sell"
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
    score: float            # composite impact score


class TaxLossHarvestingScorer:
    """
    Deterministic model: identify, score, and rank tax-loss harvesting
    opportunities across a set of tax lots.
    """

    metadata = ModelMetadata(
        name="tax_loss_harvesting",
        version="1.0.0",
        owner="portfolio-analytics",
        category=ModelCategory.TAX,
        kind=ModelKind.DETERMINISTIC,
        description=(
            "Scan holdings for unrealized losses, calculate estimated tax "
            "savings, check wash-sale windows, and find replacement candidates."
        ),
        use_case="Identify actionable tax-loss harvesting opportunities for a client.",
        input_freshness_seconds=86_400,         # lots should be < 1 day old
        known_limitations=(
            "Does not model state-level taxes.",
            "Wash-sale check is intra-account only unless cross-account lots are supplied.",
            "Replacement map is static; does not consider tracking error.",
        ),
    )

    def __init__(
        self,
        replacement_map: dict[str, list[str]] | None = None,
        min_loss_threshold: float = 100.0,
    ) -> None:
        self._replacements = replacement_map or DEFAULT_REPLACEMENT_MAP
        self._min_loss = min_loss_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """
        inputs:
            lots: list[dict]        — TaxLot-shaped dicts
            recent_trades: list[dict] — RecentTrade-shaped dicts (last 60 days)
            as_of: str              — ISO date
            federal_bracket: float  — e.g. 0.37
            lt_rate: float          — e.g. 0.20
            realized_gains_ytd: float — gains already realized this year
        """
        as_of = date.fromisoformat(inputs["as_of"])
        lots = [self._parse_lot(d) for d in inputs["lots"]]
        trades = [self._parse_trade(d) for d in inputs.get("recent_trades", [])]
        bracket = float(inputs.get("federal_bracket", 0.37))
        lt_rate = float(inputs.get("lt_rate", 0.20))
        realized_gains = float(inputs.get("realized_gains_ytd", 0.0))

        candidates: list[HarvestCandidate] = []

        for lot in lots:
            unrealized = (lot.current_price - lot.cost_basis_per_share) * lot.shares
            if unrealized >= -self._min_loss:
                continue  # not a meaningful loss

            loss_amount = abs(unrealized)
            holding_days = (as_of - lot.acquisition_date).days
            is_long_term = holding_days >= 366

            # Estimate tax saving
            tax_rate = lt_rate if is_long_term else bracket
            estimated_saving = loss_amount * tax_rate

            # Wash-sale check: look for buys of the same ticker within
            # 30 days before or after as_of
            blocked, reason = self._check_wash_sale(
                lot.ticker, lot.account_id, as_of, trades
            )

            # Replacement candidates
            replacements = self._replacements.get(lot.ticker, [])

            # Score: dollar impact, adjusted down if wash-sale blocked
            score = self._compute_score(
                estimated_saving, loss_amount, realized_gains, blocked
            )

            candidates.append(HarvestCandidate(
                lot_id=lot.lot_id,
                ticker=lot.ticker,
                shares=lot.shares,
                unrealized_loss=round(-unrealized, 2),
                is_long_term=is_long_term,
                estimated_tax_saving=round(estimated_saving, 2),
                wash_sale_blocked=blocked,
                wash_sale_reason=reason,
                replacement_candidates=replacements,
                score=round(score, 4),
            ))

        # Rank by score descending
        candidates.sort(key=lambda c: c.score, reverse=True)

        total_potential_saving = sum(c.estimated_tax_saving for c in candidates if not c.wash_sale_blocked)
        actionable = [c for c in candidates if not c.wash_sale_blocked]

        return {
            "as_of": as_of.isoformat(),
            "total_lots_scanned": len(lots),
            "candidates_found": len(candidates),
            "actionable_candidates": len(actionable),
            "total_potential_saving": round(total_potential_saving, 2),
            "candidates": [self._candidate_to_dict(c) for c in candidates],
            "severity": self._overall_severity(total_potential_saving),
            "assumptions": [
                f"Federal ordinary rate: {bracket:.0%}",
                f"Long-term CG rate: {lt_rate:.0%}",
                f"Realized gains YTD: ${realized_gains:,.2f}",
            ],
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _check_wash_sale(
        self,
        ticker: str,
        account_id: str,
        as_of: date,
        trades: list[RecentTrade],
    ) -> tuple[bool, str | None]:
        """Return (blocked, reason) if a wash-sale window is active."""
        window_start = as_of - timedelta(days=30)
        window_end = as_of + timedelta(days=30)

        for trade in trades:
            if (
                trade.ticker == ticker
                and trade.direction == "buy"
                and window_start <= trade.trade_date <= window_end
            ):
                return True, (
                    f"Buy of {ticker} on {trade.trade_date.isoformat()} "
                    f"in account {trade.account_id} is within 30-day window"
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
        # Normalize saving to a 0-100 scale (assume $50K is "max" typical)
        saving_score = min(estimated_saving / 50_000, 1.0) * 60

        # Bonus if realized gains exist that this loss can offset
        offset_bonus = 0.0
        if realized_gains > 0:
            offset_ratio = min(loss_amount / realized_gains, 1.0)
            offset_bonus = offset_ratio * 30

        # Urgency: year-end proximity could be added here (static for now)
        urgency = 10.0

        raw = saving_score + offset_bonus + urgency
        if wash_sale_blocked:
            raw *= 0.1  # still show it, but rank it very low
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
            cost_basis_per_share=float(d["cost_basis_per_share"]),
            current_price=float(d["current_price"]),
            acquisition_date=date.fromisoformat(d["acquisition_date"]),
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
            "replacement_candidates": c.replacement_candidates,
            "score": c.score,
        }
```

### 2.3 Usage example

```python
result = registry.invoke("tax_loss_harvesting", {
    "lots": [
        {
            "lot_id": "L001",
            "ticker": "XLE",
            "shares": 500,
            "cost_basis_per_share": 92.00,
            "current_price": 78.50,
            "acquisition_date": "2025-03-10",
            "account_id": "ACCT-1",
        },
    ],
    "recent_trades": [],
    "as_of": "2026-03-26",
    "federal_bracket": 0.37,
    "lt_rate": 0.20,
    "realized_gains_ytd": 25000.0,
})
# result["total_potential_saving"] -> 1350.00  (500 * 13.50 * 0.20)
```

---

## 3. Concentration Risk Scorer

Measures portfolio concentration via the Herfindahl-Hirschman Index (HHI),
single-position thresholds, sector-level thresholds, and geographic exposure.

### 3.1 Financial background

- **HHI** = sum of squared portfolio weight fractions. A fully diversified
  100-stock equal-weight portfolio has HHI = 0.01; a single-stock portfolio
  has HHI = 1.0.
- Regulatory and compliance teams often flag single positions above 10 % of
  portfolio value and single sectors above 30 %.

### 3.2 Implementation

```python
"""
sidecar/app/analytics/concentration_risk.py
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from app.analytics.registry import (
    AnalyticalModel,
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
    flag_type: str          # "single_stock" | "sector" | "geography"


class ConcentrationRiskScorer:
    """
    Deterministic model: compute HHI, flag single-stock and sector
    concentrations, and score geographic exposure.
    """

    metadata = ModelMetadata(
        name="concentration_risk",
        version="1.0.0",
        owner="portfolio-analytics",
        category=ModelCategory.PORTFOLIO,
        kind=ModelKind.DETERMINISTIC,
        description=(
            "HHI calculation, single-stock and sector threshold checks, "
            "and geographic exposure scoring."
        ),
        use_case="Identify concentrated positions that may warrant rebalancing.",
        input_freshness_seconds=86_400,
        known_limitations=(
            "Sector and geography classifications depend on upstream data quality.",
            "Does not account for derivative overlays or hedges.",
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

    def score(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """
        inputs:
            holdings: list[dict]  — each has: ticker, market_value, sector, country
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
        values = np.array([float(h["market_value"]) for h in holdings])
        weights = values / total_value
        hhi = float(np.sum(weights ** 2))

        flags: list[PositionFlag] = []

        # --- Single-stock check ---
        for holding, w in zip(holdings, weights):
            if w > self._stock_thresh:
                flags.append(PositionFlag(
                    ticker=holding["ticker"],
                    weight=round(float(w), 4),
                    threshold=self._stock_thresh,
                    excess=round(float(w) - self._stock_thresh, 4),
                    flag_type="single_stock",
                ))

        # --- Sector aggregation ---
        sector_values: dict[str, float] = {}
        for holding in holdings:
            sector = holding.get("sector", "Unknown")
            sector_values[sector] = sector_values.get(sector, 0.0) + float(holding["market_value"])

        sector_weights: dict[str, float] = {
            s: round(v / total_value, 4) for s, v in sector_values.items()
        }

        for sector, sw in sector_weights.items():
            if sw > self._sector_thresh:
                flags.append(PositionFlag(
                    ticker=sector,
                    weight=sw,
                    threshold=self._sector_thresh,
                    excess=round(sw - self._sector_thresh, 4),
                    flag_type="sector",
                ))

        # --- Geographic aggregation ---
        geo_values: dict[str, float] = {}
        for holding in holdings:
            country = holding.get("country", "US")
            geo_values[country] = geo_values.get(country, 0.0) + float(holding["market_value"])

        geo_weights: dict[str, float] = {
            g: round(v / total_value, 4) for g, v in geo_values.items()
        }

        for geo, gw in geo_weights.items():
            if gw > self._geo_thresh:
                flags.append(PositionFlag(
                    ticker=geo,
                    weight=gw,
                    threshold=self._geo_thresh,
                    excess=round(gw - self._geo_thresh, 4),
                    flag_type="geography",
                ))

        # --- HHI interpretation ---
        # Effective number of positions = 1/HHI
        effective_positions = 1.0 / hhi if hhi > 0 else len(holdings)

        severity = self._compute_severity(hhi, flags)

        # --- Overall concentration score (0-100, higher = more concentrated) ---
        # Blend HHI contribution and flag contribution
        hhi_score = min(hhi / 0.25, 1.0) * 50      # HHI of 0.25 = max
        flag_score = min(len(flags) / 5, 1.0) * 30  # 5+ flags = max
        excess_score = min(
            sum(f.excess for f in flags) / 0.50, 1.0
        ) * 20
        concentration_score = round(hhi_score + flag_score + excess_score, 2)

        return {
            "as_of": as_of,
            "hhi": round(hhi, 6),
            "effective_positions": round(effective_positions, 1),
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
    def _compute_severity(hhi: float, flags: list[PositionFlag]) -> str:
        if hhi > 0.18 or any(f.excess > 0.15 for f in flags):
            return "action_needed"
        if hhi > 0.10 or flags:
            return "warning"
        return "info"
```

### 3.3 Interpreting the output

| Field | Meaning |
|-------|---------|
| `hhi` | Raw Herfindahl-Hirschman Index (0--1). Below 0.06 is well diversified. |
| `effective_positions` | 1/HHI. A value of 10 means the portfolio behaves as if it held 10 equal-weight positions. |
| `concentration_score` | Composite 0--100 metric blending HHI, flag count, and excess weight. |
| `flags` | Individual positions or sectors that exceed configured thresholds. |

---

## 4. Drift Detection

Compares current portfolio allocation against a target model allocation,
computes per-asset-class and overall drift, and assigns severity.

### 4.1 Implementation

```python
"""
sidecar/app/analytics/drift_detection.py
"""
from __future__ import annotations

from typing import Any

import numpy as np

from app.analytics.registry import (
    AnalyticalModel,
    ModelCategory,
    ModelKind,
    ModelMetadata,
)


class DriftDetector:
    """
    Deterministic model: measure allocation drift vs a target model.
    """

    metadata = ModelMetadata(
        name="drift_detection",
        version="1.0.0",
        owner="portfolio-analytics",
        category=ModelCategory.PORTFOLIO,
        kind=ModelKind.DETERMINISTIC,
        description=(
            "Compare current allocation vs model target, compute per-bucket "
            "and overall drift, and assign severity."
        ),
        use_case="Detect when a portfolio has drifted beyond tolerance bands.",
        input_freshness_seconds=86_400,
        known_limitations=(
            "Assumes asset class mapping is pre-computed upstream.",
            "Does not model intra-day price movements.",
        ),
    )

    def __init__(
        self,
        default_threshold_pct: float = 5.0,
        severe_threshold_pct: float = 10.0,
    ) -> None:
        self._default_thresh = default_threshold_pct
        self._severe_thresh = severe_threshold_pct

    def score(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """
        inputs:
            current_allocation: dict[str, float]
                Maps asset class label -> current weight (0-100 pct).
            target_allocation: dict[str, float]
                Maps asset class label -> target weight (0-100 pct).
            thresholds: dict[str, float] | None
                Optional per-asset-class drift threshold overrides (in pct points).
            as_of: str
        """
        current = inputs["current_allocation"]
        target = inputs["target_allocation"]
        custom_thresholds = inputs.get("thresholds") or {}
        as_of = inputs["as_of"]

        # Union of all asset classes
        all_classes = sorted(set(current.keys()) | set(target.keys()))

        position_drifts: list[dict[str, Any]] = []
        abs_drifts: list[float] = []

        for ac in all_classes:
            cur_w = float(current.get(ac, 0.0))
            tgt_w = float(target.get(ac, 0.0))
            drift_pct = cur_w - tgt_w
            abs_drift = abs(drift_pct)
            abs_drifts.append(abs_drift)

            threshold = float(custom_thresholds.get(ac, self._default_thresh))

            if abs_drift >= self._severe_thresh:
                severity = "action_needed"
            elif abs_drift >= threshold:
                severity = "warning"
            else:
                severity = "ok"

            position_drifts.append({
                "asset_class": ac,
                "current_weight": round(cur_w, 2),
                "target_weight": round(tgt_w, 2),
                "drift_pct": round(drift_pct, 2),
                "abs_drift_pct": round(abs_drift, 2),
                "threshold": threshold,
                "severity": severity,
            })

        # Overall drift metrics
        drift_array = np.array(abs_drifts)
        max_drift = float(np.max(drift_array)) if len(drift_array) > 0 else 0.0
        mean_drift = float(np.mean(drift_array)) if len(drift_array) > 0 else 0.0

        # Root-mean-square drift — single scalar summary
        rms_drift = float(np.sqrt(np.mean(drift_array ** 2))) if len(drift_array) > 0 else 0.0

        # Overall severity
        if max_drift >= self._severe_thresh:
            overall_severity = "action_needed"
        elif max_drift >= self._default_thresh:
            overall_severity = "warning"
        else:
            overall_severity = "ok"

        # Drift score: 0-100 (higher = more drifted)
        drift_score = min(rms_drift / self._severe_thresh, 1.0) * 100

        return {
            "as_of": as_of,
            "overall_severity": overall_severity,
            "drift_score": round(drift_score, 2),
            "max_drift_pct": round(max_drift, 2),
            "mean_drift_pct": round(mean_drift, 2),
            "rms_drift_pct": round(rms_drift, 2),
            "position_drifts": position_drifts,
            "positions_breaching": sum(
                1 for pd in position_drifts if pd["severity"] != "ok"
            ),
        }
```

### 4.2 Threshold configuration

The model supports two levels of threshold:

| Level | Default | Meaning |
|-------|---------|---------|
| `default_threshold_pct` | 5.0 | Per-bucket drift that triggers a `warning`. |
| `severe_threshold_pct` | 10.0 | Per-bucket drift that triggers `action_needed`. |

Both can be overridden per-asset-class via the `thresholds` input dict.

---

## 5. RMD Calculator

Calculates Required Minimum Distributions for Traditional IRA (and other
qualified retirement account) holders using the IRS Uniform Lifetime Table.

### 5.1 Regulatory background

- Beginning in 2023 (SECURE 2.0), the RMD starting age is **73**.
- The Uniform Lifetime Table provides a "distribution period" divisor for
  each age from 72 to 120+.
- RMD = prior year-end balance / distribution period.
- Clients who turn 73 must take their first RMD by April 1 of the following
  year; subsequent RMDs are due December 31.

### 5.2 Implementation

```python
"""
sidecar/app/analytics/rmd_calculator.py
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.analytics.registry import (
    AnalyticalModel,
    ModelCategory,
    ModelKind,
    ModelMetadata,
)


# ---------------------------------------------------------------------------
# IRS Uniform Lifetime Table (2024 revision, effective for 2022+)
# Maps age -> distribution period
# ---------------------------------------------------------------------------

UNIFORM_LIFETIME_TABLE: dict[int, float] = {
    72: 27.4,  73: 26.5,  74: 25.5,  75: 24.6,  76: 23.7,
    77: 22.9,  78: 22.0,  79: 21.1,  80: 20.2,  81: 19.4,
    82: 18.5,  83: 17.7,  84: 16.8,  85: 16.0,  86: 15.2,
    87: 14.4,  88: 13.7,  89: 12.9,  90: 12.2,  91: 11.5,
    92: 10.8,  93: 10.1,  94:  9.5,  95:  8.9,  96:  8.4,
    97:  7.8,  98:  7.3,  99:  6.8, 100:  6.4, 101:  6.0,
    102:  5.6, 103:  5.2, 104:  4.9, 105:  4.6, 106:  4.3,
    107:  4.1, 108:  3.9, 109:  3.7, 110:  3.5, 111:  3.4,
    112:  3.3, 113:  3.1, 114:  3.0, 115:  2.9, 116:  2.8,
    117:  2.7, 118:  2.5, 119:  2.3, 120:  2.0,
}

# Account types subject to RMD
RMD_ACCOUNT_TYPES = {
    "traditional_ira",
    "sep_ira",
    "simple_ira",
    "401k",
    "403b",
    "457b",
    "inherited_ira",
}


class RMDCalculator:
    """
    Deterministic model: calculate required minimum distributions and
    flag clients approaching the RMD start age.
    """

    metadata = ModelMetadata(
        name="rmd_calculator",
        version="1.0.0",
        owner="portfolio-analytics",
        category=ModelCategory.TAX,
        kind=ModelKind.DETERMINISTIC,
        description=(
            "Calculate RMD amounts using the IRS Uniform Lifetime Table. "
            "Flag clients approaching age 73."
        ),
        use_case="Ensure clients take timely RMDs and avoid IRS penalties.",
        input_freshness_seconds=86_400,
        known_limitations=(
            "Uses Uniform Lifetime Table only; does not handle Joint Life Table "
            "for spouses more than 10 years younger.",
            "Does not track whether RMD has already been partially satisfied.",
            "Inherited IRA RMD rules (10-year rule) require separate handling.",
        ),
    )

    def score(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """
        inputs:
            accounts: list[dict]
                Each has: account_id, account_type, prior_year_end_balance,
                          owner_date_of_birth, owner_name
            as_of: str — ISO date
        """
        as_of = date.fromisoformat(inputs["as_of"])
        accounts = inputs["accounts"]
        results: list[dict[str, Any]] = []

        for acct in accounts:
            account_type = acct["account_type"].lower().replace(" ", "_")
            if account_type not in RMD_ACCOUNT_TYPES:
                continue

            dob = date.fromisoformat(acct["owner_date_of_birth"])
            age = self._age_at_year_end(dob, as_of.year)
            balance = float(acct["prior_year_end_balance"])

            rmd_required = age >= 73
            approaching = 70 <= age < 73

            rmd_amount = 0.0
            distribution_period = None
            deadline = None

            if rmd_required:
                # Use age as of Dec 31 of distribution year
                clamped_age = min(age, 120)
                distribution_period = UNIFORM_LIFETIME_TABLE.get(clamped_age, 2.0)
                rmd_amount = balance / distribution_period

                # First RMD year: deadline is April 1 of following year
                first_rmd_age = 73
                first_rmd_year = dob.year + first_rmd_age
                if as_of.year == first_rmd_year:
                    deadline = f"{first_rmd_year + 1}-04-01"
                else:
                    deadline = f"{as_of.year}-12-31"

            # Severity
            if rmd_required and rmd_amount > 0:
                days_to_deadline = (
                    date.fromisoformat(deadline) - as_of
                ).days if deadline else 365
                if days_to_deadline <= 30:
                    severity = "action_needed"
                elif days_to_deadline <= 90:
                    severity = "warning"
                else:
                    severity = "info"
            elif approaching:
                severity = "info"
            else:
                continue  # not relevant

            results.append({
                "account_id": acct["account_id"],
                "account_type": account_type,
                "owner_name": acct.get("owner_name", ""),
                "owner_age": age,
                "prior_year_end_balance": balance,
                "rmd_required": rmd_required,
                "approaching_rmd_age": approaching,
                "rmd_amount": round(rmd_amount, 2),
                "distribution_period": distribution_period,
                "deadline": deadline,
                "severity": severity,
            })

        # Sort by severity (action_needed first), then by rmd_amount descending
        severity_order = {"action_needed": 0, "warning": 1, "info": 2}
        results.sort(key=lambda r: (severity_order.get(r["severity"], 9), -r["rmd_amount"]))

        total_rmd = sum(r["rmd_amount"] for r in results if r["rmd_required"])
        action_needed_count = sum(1 for r in results if r["severity"] == "action_needed")

        return {
            "as_of": as_of.isoformat(),
            "accounts_evaluated": len(results),
            "total_rmd_due": round(total_rmd, 2),
            "action_needed_count": action_needed_count,
            "severity": "action_needed" if action_needed_count > 0 else (
                "warning" if any(r["severity"] == "warning" for r in results) else "info"
            ),
            "accounts": results,
        }

    @staticmethod
    def _age_at_year_end(dob: date, year: int) -> int:
        """Age the person will be on December 31 of the given year."""
        return year - dob.year
```

### 5.3 Penalty context

The IRS penalty for missing an RMD was reduced from 50 % to **25 %** of the
shortfall under SECURE 2.0 (and can be further reduced to 10 % if corrected
within a correction window). The model flags approaching deadlines so
advisors can act before penalties apply.

---

## 6. Tax Scenario Engine

What-if modeling: given a proposed action (Roth conversion, loss harvest,
charitable gift), project the tax liability delta against the baseline.

### 6.1 Implementation

```python
"""
sidecar/app/analytics/tax_scenario_engine.py
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.analytics.registry import (
    AnalyticalModel,
    ModelCategory,
    ModelKind,
    ModelMetadata,
)


# ---------------------------------------------------------------------------
# 2025/2026 Federal tax brackets (Married Filing Jointly, simplified)
# ---------------------------------------------------------------------------

MFJ_BRACKETS_2026: list[tuple[float, float]] = [
    (23_850,  0.10),
    (96_950,  0.12),
    (206_700, 0.22),
    (394_600, 0.24),
    (501_050, 0.32),
    (751_600, 0.35),
    (float("inf"), 0.37),
]

SINGLE_BRACKETS_2026: list[tuple[float, float]] = [
    (11_925,  0.10),
    (48_475,  0.12),
    (103_350, 0.22),
    (197_300, 0.24),
    (250_525, 0.32),
    (626_350, 0.35),
    (float("inf"), 0.37),
]

BRACKET_TABLES = {
    "mfj": MFJ_BRACKETS_2026,
    "single": SINGLE_BRACKETS_2026,
    "married_filing_jointly": MFJ_BRACKETS_2026,
    "married_filing_separately": SINGLE_BRACKETS_2026,  # simplified
    "head_of_household": SINGLE_BRACKETS_2026,          # simplified
}

# Long-term capital gains brackets (MFJ, 2026 est.)
LTCG_BRACKETS_MFJ: list[tuple[float, float]] = [
    (94_050,  0.00),
    (583_750, 0.15),
    (float("inf"), 0.20),
]

# Net Investment Income Tax threshold
NIIT_THRESHOLD_MFJ = 250_000
NIIT_RATE = 0.038


@dataclass
class ScenarioAction:
    """A single proposed action within a what-if scenario."""
    action_type: str        # "roth_conversion", "harvest_loss", "charitable_gift", "realize_gain"
    amount: float           # dollar amount
    details: dict           # action-specific parameters


class TaxScenarioEngine:
    """
    Heuristic model: project federal tax liability under baseline and
    one or more proposed actions.
    """

    metadata = ModelMetadata(
        name="tax_scenario_engine",
        version="1.0.0",
        owner="tax-planning",
        category=ModelCategory.TAX,
        kind=ModelKind.HEURISTIC,
        description=(
            "What-if tax modeling: project federal tax liability delta for "
            "proposed actions such as Roth conversions, loss harvesting, "
            "charitable gifts, and gain realization."
        ),
        use_case="Compare tax outcomes of proposed planning actions against baseline.",
        input_freshness_seconds=604_800,  # weekly refresh is acceptable
        known_limitations=(
            "Uses simplified federal brackets; does not model AMT.",
            "State taxes are not included.",
            "NIIT is approximated; does not model all investment income components.",
            "Charitable deduction limited to simplified AGI caps.",
            "Multi-year projections assume constant tax rates.",
        ),
    )

    def score(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """
        inputs:
            filing_status: str
            ordinary_income: float       — wages, interest, pensions, etc.
            lt_capital_gains: float      — net long-term capital gains (baseline)
            st_capital_gains: float      — net short-term capital gains (baseline)
            deductions: float            — itemized or standard deduction
            investment_income: float     — for NIIT calculation
            scenarios: list[dict]        — each has 'name' and 'actions' list
            as_of: str
        """
        filing_status = inputs.get("filing_status", "mfj").lower()
        ordinary = float(inputs["ordinary_income"])
        lt_gains = float(inputs.get("lt_capital_gains", 0))
        st_gains = float(inputs.get("st_capital_gains", 0))
        deductions = float(inputs.get("deductions", 29_200))  # 2026 standard MFJ
        investment_income = float(inputs.get("investment_income", lt_gains + st_gains))
        as_of = inputs["as_of"]

        brackets = BRACKET_TABLES.get(filing_status, MFJ_BRACKETS_2026)

        # --- Baseline tax computation ---
        baseline = self._compute_tax(
            ordinary_income=ordinary,
            st_capital_gains=st_gains,
            lt_capital_gains=lt_gains,
            deductions=deductions,
            investment_income=investment_income,
            brackets=brackets,
            filing_status=filing_status,
        )

        # --- Scenario computations ---
        scenario_results: list[dict[str, Any]] = []

        for scenario_input in inputs.get("scenarios", []):
            scenario_name = scenario_input["name"]
            actions = [
                ScenarioAction(
                    action_type=a["action_type"],
                    amount=float(a["amount"]),
                    details=a.get("details", {}),
                )
                for a in scenario_input["actions"]
            ]

            # Apply actions to adjust income components
            adj_ordinary = ordinary
            adj_st_gains = st_gains
            adj_lt_gains = lt_gains
            adj_deductions = deductions
            adj_investment_income = investment_income
            trade_offs: list[str] = []

            for action in actions:
                if action.action_type == "roth_conversion":
                    # Roth conversion adds to ordinary income
                    adj_ordinary += action.amount
                    trade_offs.append(
                        f"Roth conversion of ${action.amount:,.0f} adds to ordinary income this year "
                        f"but provides tax-free growth and withdrawals in retirement."
                    )

                elif action.action_type == "harvest_loss":
                    # Losses offset gains; first ST, then LT, then up to $3K ordinary
                    remaining_loss = action.amount
                    # Offset ST gains first
                    offset_st = min(remaining_loss, max(adj_st_gains, 0))
                    adj_st_gains -= offset_st
                    remaining_loss -= offset_st
                    # Then LT gains
                    offset_lt = min(remaining_loss, max(adj_lt_gains, 0))
                    adj_lt_gains -= offset_lt
                    remaining_loss -= offset_lt
                    # Then up to $3,000 ordinary income
                    offset_ordinary = min(remaining_loss, 3_000)
                    adj_ordinary -= offset_ordinary
                    remaining_loss -= offset_ordinary
                    # Remainder carries forward (noted but not modeled)
                    if remaining_loss > 0:
                        trade_offs.append(
                            f"${remaining_loss:,.0f} in excess losses carry forward to future years."
                        )
                    trade_offs.append(
                        f"Harvesting ${action.amount:,.0f} in losses offsets gains and up to $3K ordinary income."
                    )
                    adj_investment_income -= (offset_st + offset_lt)

                elif action.action_type == "charitable_gift":
                    # Charitable deduction (simplified: assume cash gift, 60% AGI limit)
                    agi = adj_ordinary + adj_st_gains + adj_lt_gains
                    max_deduction = agi * 0.60
                    actual_deduction = min(action.amount, max_deduction)
                    adj_deductions += actual_deduction
                    if action.amount > max_deduction:
                        trade_offs.append(
                            f"Gift exceeds 60% AGI limit; ${action.amount - max_deduction:,.0f} carries forward."
                        )
                    trade_offs.append(
                        f"Charitable gift of ${action.amount:,.0f} adds ${actual_deduction:,.0f} to deductions."
                    )

                elif action.action_type == "realize_gain":
                    # Realize additional capital gains
                    term = action.details.get("term", "long")
                    if term == "short":
                        adj_st_gains += action.amount
                    else:
                        adj_lt_gains += action.amount
                    adj_investment_income += action.amount
                    trade_offs.append(
                        f"Realizing ${action.amount:,.0f} in {term}-term gains."
                    )

            # Compute scenario tax
            scenario_tax = self._compute_tax(
                ordinary_income=adj_ordinary,
                st_capital_gains=adj_st_gains,
                lt_capital_gains=adj_lt_gains,
                deductions=adj_deductions,
                investment_income=adj_investment_income,
                brackets=brackets,
                filing_status=filing_status,
            )

            delta = scenario_tax["total_tax"] - baseline["total_tax"]

            scenario_results.append({
                "name": scenario_name,
                "actions": [
                    {"action_type": a.action_type, "amount": a.amount}
                    for a in actions
                ],
                "projected_tax": scenario_tax,
                "baseline_tax": baseline["total_tax"],
                "delta": round(delta, 2),
                "delta_pct": round(
                    delta / baseline["total_tax"] * 100, 2
                ) if baseline["total_tax"] > 0 else 0.0,
                "trade_offs": trade_offs,
            })

        return {
            "as_of": as_of,
            "filing_status": filing_status,
            "baseline": baseline,
            "scenarios": scenario_results,
            "disclaimer": (
                "This is decision-support modeling, not tax advice. "
                "Consult a qualified tax professional before taking action."
            ),
        }

    # ------------------------------------------------------------------
    # Tax computation engine
    # ------------------------------------------------------------------

    def _compute_tax(
        self,
        ordinary_income: float,
        st_capital_gains: float,
        lt_capital_gains: float,
        deductions: float,
        investment_income: float,
        brackets: list[tuple[float, float]],
        filing_status: str,
    ) -> dict[str, Any]:
        """Compute federal tax liability for a single scenario."""
        # Short-term gains are taxed as ordinary income
        total_ordinary = ordinary_income + max(st_capital_gains, 0)
        taxable_ordinary = max(total_ordinary - deductions, 0)

        # Ordinary income tax (progressive brackets)
        ordinary_tax = self._apply_brackets(taxable_ordinary, brackets)

        # Long-term capital gains tax (preferential rates)
        lt_tax = self._compute_ltcg_tax(
            max(lt_capital_gains, 0), taxable_ordinary, filing_status
        )

        # Net Investment Income Tax (3.8%)
        niit_threshold = (
            NIIT_THRESHOLD_MFJ if "mfj" in filing_status or "jointly" in filing_status
            else 200_000
        )
        agi = total_ordinary + max(lt_capital_gains, 0)
        niit = 0.0
        if agi > niit_threshold:
            niit_base = min(max(investment_income, 0), agi - niit_threshold)
            niit = niit_base * NIIT_RATE

        total_tax = ordinary_tax + lt_tax + niit

        # Effective and marginal rates
        total_income = taxable_ordinary + max(lt_capital_gains, 0)
        effective_rate = total_tax / total_income if total_income > 0 else 0.0
        marginal_rate = self._marginal_rate(taxable_ordinary, brackets)

        return {
            "taxable_ordinary_income": round(taxable_ordinary, 2),
            "ordinary_tax": round(ordinary_tax, 2),
            "lt_capital_gains": round(max(lt_capital_gains, 0), 2),
            "lt_gains_tax": round(lt_tax, 2),
            "niit": round(niit, 2),
            "total_tax": round(total_tax, 2),
            "effective_rate": round(effective_rate, 4),
            "marginal_rate": marginal_rate,
        }

    @staticmethod
    def _apply_brackets(
        taxable_income: float,
        brackets: list[tuple[float, float]],
    ) -> float:
        """Progressive bracket computation."""
        tax = 0.0
        prev_limit = 0.0
        for limit, rate in brackets:
            if taxable_income <= prev_limit:
                break
            taxable_in_bracket = min(taxable_income, limit) - prev_limit
            tax += taxable_in_bracket * rate
            prev_limit = limit
        return tax

    @staticmethod
    def _compute_ltcg_tax(
        lt_gains: float,
        taxable_ordinary: float,
        filing_status: str,
    ) -> float:
        """Apply preferential LTCG rates (0/15/20)."""
        if lt_gains <= 0:
            return 0.0
        ltcg_brackets = LTCG_BRACKETS_MFJ  # simplified: same for all statuses
        tax = 0.0
        # LTCG stacks on top of ordinary income
        base = taxable_ordinary
        remaining = lt_gains
        for limit, rate in ltcg_brackets:
            if base >= limit:
                continue
            room = limit - base
            taxable = min(remaining, room)
            tax += taxable * rate
            remaining -= taxable
            base += taxable
            if remaining <= 0:
                break
        return tax

    @staticmethod
    def _marginal_rate(
        taxable_income: float,
        brackets: list[tuple[float, float]],
    ) -> float:
        """Return the marginal ordinary income tax rate."""
        for limit, rate in brackets:
            if taxable_income <= limit:
                return rate
        return brackets[-1][1]
```

### 6.2 Usage example

```python
result = registry.invoke("tax_scenario_engine", {
    "filing_status": "mfj",
    "ordinary_income": 350_000,
    "lt_capital_gains": 40_000,
    "st_capital_gains": 10_000,
    "deductions": 29_200,
    "investment_income": 55_000,
    "as_of": "2026-03-26",
    "scenarios": [
        {
            "name": "Convert $100K to Roth",
            "actions": [
                {"action_type": "roth_conversion", "amount": 100_000}
            ],
        },
        {
            "name": "Harvest $30K losses + $50K Roth conversion",
            "actions": [
                {"action_type": "harvest_loss", "amount": 30_000},
                {"action_type": "roth_conversion", "amount": 50_000},
            ],
        },
    ],
})
# result["scenarios"][0]["delta"] shows the additional tax cost of the Roth conversion
# result["scenarios"][1]["delta"] shows the net effect of combining loss harvest + conversion
```

---

## 7. Firm-Wide Opportunity Ranker

Aggregates account-level model scores into a firm-level priority list.
Each opportunity is ranked by: **estimated dollar impact x confidence x urgency**.

### 7.1 Implementation

```python
"""
sidecar/app/analytics/firm_ranker.py
"""
from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np

from app.analytics.registry import (
    AnalyticalModel,
    ModelCategory,
    ModelKind,
    ModelMetadata,
)


# ---------------------------------------------------------------------------
# Urgency decay — how many days until the opportunity expires or degrades
# ---------------------------------------------------------------------------

URGENCY_PROFILES: dict[str, dict[str, Any]] = {
    "rmd_deadline": {
        "base_urgency": 1.0,
        "decay_type": "cliff",      # urgency jumps when deadline is near
        "critical_days": 30,
    },
    "tax_loss_harvest": {
        "base_urgency": 0.7,
        "decay_type": "year_end",    # value increases as Dec 31 approaches
    },
    "concentration_risk": {
        "base_urgency": 0.5,
        "decay_type": "none",        # structural, not time-sensitive
    },
    "drift": {
        "base_urgency": 0.6,
        "decay_type": "none",
    },
    "beneficiary_missing": {
        "base_urgency": 0.8,
        "decay_type": "none",
    },
    "cash_drag": {
        "base_urgency": 0.4,
        "decay_type": "none",
    },
}

# Confidence mapping from severity labels
CONFIDENCE_MAP: dict[str, float] = {
    "action_needed": 0.95,
    "warning": 0.75,
    "info": 0.50,
}


class FirmWideOpportunityRanker:
    """
    Heuristic model: aggregate account-level analytical scores into a
    firm-wide priority list ranked by estimated_impact * confidence * urgency.
    """

    metadata = ModelMetadata(
        name="firm_opportunity_ranker",
        version="1.0.0",
        owner="firm-analytics",
        category=ModelCategory.FIRM_ANALYTICS,
        kind=ModelKind.HEURISTIC,
        description=(
            "Aggregate account-level scores from tax, portfolio, and compliance "
            "models into a single firm-wide priority list."
        ),
        use_case="Help firm leadership and advisors prioritize the highest-impact actions.",
        input_freshness_seconds=86_400,
        known_limitations=(
            "Dollar impact estimates are approximate and model-dependent.",
            "Urgency profiles are configurable heuristics, not market-derived.",
            "Does not deduplicate overlapping opportunities across models.",
        ),
    )

    def score(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """
        inputs:
            opportunities: list[dict]
                Each has:
                    client_id, client_name, account_id, advisor_id,
                    opportunity_type (str — matches URGENCY_PROFILES key),
                    estimated_dollar_impact (float),
                    severity (str),
                    source_model (str),
                    details (dict),
                    deadline (str | None — ISO date)
            as_of: str
        """
        as_of = date.fromisoformat(inputs["as_of"])
        raw = inputs["opportunities"]

        scored: list[dict[str, Any]] = []

        for opp in raw:
            opp_type = opp["opportunity_type"]
            dollar_impact = float(opp.get("estimated_dollar_impact", 0))
            severity = opp.get("severity", "info")
            deadline_str = opp.get("deadline")

            confidence = CONFIDENCE_MAP.get(severity, 0.50)
            urgency = self._compute_urgency(opp_type, as_of, deadline_str)

            # Composite rank score
            # Normalize dollar impact to a 0-1 scale (assume $100K is "max")
            impact_norm = min(dollar_impact / 100_000, 1.0)
            rank_score = impact_norm * confidence * urgency

            scored.append({
                "client_id": opp["client_id"],
                "client_name": opp.get("client_name", ""),
                "account_id": opp.get("account_id"),
                "advisor_id": opp.get("advisor_id"),
                "opportunity_type": opp_type,
                "estimated_dollar_impact": round(dollar_impact, 2),
                "severity": severity,
                "confidence": round(confidence, 2),
                "urgency": round(urgency, 4),
                "rank_score": round(rank_score, 6),
                "source_model": opp.get("source_model", "unknown"),
                "deadline": deadline_str,
                "details": opp.get("details", {}),
            })

        # Sort by rank_score descending
        scored.sort(key=lambda s: s["rank_score"], reverse=True)

        # Assign ordinal rank
        for i, item in enumerate(scored):
            item["rank"] = i + 1

        # Aggregate stats
        scores_arr = np.array([s["rank_score"] for s in scored]) if scored else np.array([])
        total_impact = sum(s["estimated_dollar_impact"] for s in scored)

        # Group by opportunity type
        type_counts: dict[str, int] = {}
        type_impact: dict[str, float] = {}
        for s in scored:
            t = s["opportunity_type"]
            type_counts[t] = type_counts.get(t, 0) + 1
            type_impact[t] = type_impact.get(t, 0) + s["estimated_dollar_impact"]

        return {
            "as_of": as_of.isoformat(),
            "total_opportunities": len(scored),
            "total_estimated_impact": round(total_impact, 2),
            "mean_rank_score": round(float(np.mean(scores_arr)), 6) if len(scores_arr) > 0 else 0.0,
            "by_type": {
                t: {"count": type_counts[t], "total_impact": round(type_impact[t], 2)}
                for t in sorted(type_counts.keys())
            },
            "ranked_opportunities": scored,
        }

    @staticmethod
    def _compute_urgency(
        opp_type: str,
        as_of: date,
        deadline_str: str | None,
    ) -> float:
        """Compute urgency multiplier (0-1) for an opportunity."""
        profile = URGENCY_PROFILES.get(opp_type, {"base_urgency": 0.5, "decay_type": "none"})
        base = profile["base_urgency"]

        decay_type = profile["decay_type"]

        if decay_type == "cliff" and deadline_str:
            deadline = date.fromisoformat(deadline_str)
            days_remaining = (deadline - as_of).days
            critical = profile.get("critical_days", 30)
            if days_remaining <= 0:
                return 1.0  # overdue — maximum urgency
            if days_remaining <= critical:
                # Linear ramp from base to 1.0 as deadline approaches
                return base + (1.0 - base) * (1 - days_remaining / critical)
            return base * 0.8  # well ahead of deadline

        if decay_type == "year_end":
            # Urgency increases as Dec 31 approaches
            year_end = date(as_of.year, 12, 31)
            days_to_ye = (year_end - as_of).days
            if days_to_ye <= 0:
                return 1.0
            # Sigmoid-ish: urgency ramps in Q4
            ye_factor = max(0, 1 - days_to_ye / 365)
            return base + (1.0 - base) * ye_factor ** 2

        # decay_type == "none" — structural opportunity, constant urgency
        return base
```

### 7.2 Rank score formula

```
rank_score = normalize(dollar_impact, cap=100K) * confidence * urgency
```

| Component | Source | Range |
|-----------|--------|-------|
| `dollar_impact` | Upstream model (e.g., tax saving, drift cost) | Normalized 0--1 |
| `confidence` | Mapped from severity: `action_needed`=0.95, `warning`=0.75, `info`=0.50 | 0--1 |
| `urgency` | Time-based profile per opportunity type (cliff, year-end, or constant) | 0--1 |

---

## 8. Beneficiary Completeness Audit

Scans accounts for missing or outdated beneficiary designations.
Retirement accounts (IRA, 401k) without beneficiaries are flagged as
high severity because they bypass the account owner's estate plan.

### 8.1 Implementation

```python
"""
sidecar/app/analytics/beneficiary_audit.py
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.analytics.registry import (
    AnalyticalModel,
    ModelCategory,
    ModelKind,
    ModelMetadata,
)


# Account types where missing beneficiary is critical
RETIREMENT_ACCOUNT_TYPES = {
    "traditional_ira", "roth_ira", "sep_ira", "simple_ira",
    "401k", "403b", "457b", "inherited_ira",
}

# How old a beneficiary designation can be before it's "stale"
STALE_THRESHOLD_DAYS = 365 * 3  # 3 years


class BeneficiaryCompletenessAudit:
    """
    Deterministic model: audit accounts for missing or outdated
    beneficiary designations.
    """

    metadata = ModelMetadata(
        name="beneficiary_audit",
        version="1.0.0",
        owner="compliance",
        category=ModelCategory.COMPLIANCE,
        kind=ModelKind.DETERMINISTIC,
        description=(
            "Scan accounts for missing or outdated beneficiary designations. "
            "Flag retirement accounts without beneficiaries as high severity."
        ),
        use_case="Ensure all accounts — especially qualified retirement accounts — have current beneficiaries.",
        input_freshness_seconds=86_400,
        known_limitations=(
            "Cannot verify beneficiary identity correctness, only presence.",
            "Stale threshold is calendar-based; life events (marriage, divorce, death) are not detected.",
        ),
    )

    def score(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """
        inputs:
            accounts: list[dict]
                Each has: account_id, account_type, account_title, client_id,
                          client_name, market_value,
                          beneficiaries: list[dict] | None
                              Each beneficiary: name, relationship,
                                                designation_date (ISO), share_pct
            as_of: str
        """
        as_of = date.fromisoformat(inputs["as_of"])
        accounts = inputs["accounts"]
        findings: list[dict[str, Any]] = []

        for acct in accounts:
            account_type = acct["account_type"].lower().replace(" ", "_")
            beneficiaries = acct.get("beneficiaries") or []
            is_retirement = account_type in RETIREMENT_ACCOUNT_TYPES
            market_value = float(acct.get("market_value", 0))

            issues: list[str] = []
            severity = "ok"

            # --- Check: no beneficiaries at all ---
            if not beneficiaries:
                issues.append("No beneficiary designated")
                severity = "action_needed" if is_retirement else "warning"

            else:
                # --- Check: shares don't sum to 100% ---
                total_share = sum(float(b.get("share_pct", 0)) for b in beneficiaries)
                if abs(total_share - 100.0) > 0.01:
                    issues.append(
                        f"Beneficiary shares sum to {total_share:.1f}%, not 100%"
                    )
                    severity = max(severity, "warning", key=lambda s: _sev_rank(s))

                # --- Check: stale designations ---
                for ben in beneficiaries:
                    desg_date_str = ben.get("designation_date")
                    if desg_date_str:
                        desg_date = date.fromisoformat(desg_date_str)
                        age_days = (as_of - desg_date).days
                        if age_days > STALE_THRESHOLD_DAYS:
                            issues.append(
                                f"Beneficiary '{ben.get('name', 'Unknown')}' designation is "
                                f"{age_days // 365} years old — may need review"
                            )
                            severity = max(severity, "warning", key=lambda s: _sev_rank(s))

            if issues:
                findings.append({
                    "account_id": acct["account_id"],
                    "account_type": account_type,
                    "account_title": acct.get("account_title", ""),
                    "client_id": acct.get("client_id"),
                    "client_name": acct.get("client_name", ""),
                    "market_value": market_value,
                    "is_retirement_account": is_retirement,
                    "beneficiary_count": len(beneficiaries),
                    "issues": issues,
                    "severity": severity,
                })

        # Sort: action_needed first, then by market_value descending
        findings.sort(key=lambda f: (_sev_rank(f["severity"]), -f["market_value"]))

        action_count = sum(1 for f in findings if f["severity"] == "action_needed")
        warning_count = sum(1 for f in findings if f["severity"] == "warning")

        return {
            "as_of": as_of.isoformat(),
            "total_accounts_scanned": len(accounts),
            "findings_count": len(findings),
            "action_needed_count": action_count,
            "warning_count": warning_count,
            "severity": (
                "action_needed" if action_count > 0
                else "warning" if warning_count > 0
                else "ok"
            ),
            "findings": findings,
        }


def _sev_rank(severity: str) -> int:
    """Lower number = higher severity (for sorting)."""
    return {"action_needed": 0, "warning": 1, "info": 2, "ok": 3}.get(severity, 9)
```

---

## 9. Cash Drag Detector

Identifies accounts with excessive uninvested cash relative to the target
allocation or an absolute threshold.

### 9.1 Financial background

Cash drag is the performance cost of holding uninvested cash. If a portfolio
targets 2 % cash and holds 15 %, the 13 % excess represents foregone returns
at the portfolio's expected rate.

### 9.2 Implementation

```python
"""
sidecar/app/analytics/cash_drag.py
"""
from __future__ import annotations

from typing import Any

from app.analytics.registry import (
    AnalyticalModel,
    ModelCategory,
    ModelKind,
    ModelMetadata,
)


class CashDragDetector:
    """
    Deterministic model: flag accounts holding more cash than their
    target allocation or an absolute threshold.
    """

    metadata = ModelMetadata(
        name="cash_drag_detector",
        version="1.0.0",
        owner="portfolio-analytics",
        category=ModelCategory.PORTFOLIO,
        kind=ModelKind.DETERMINISTIC,
        description=(
            "Identify accounts with excessive uninvested cash relative to "
            "model targets or absolute thresholds."
        ),
        use_case="Surface cash drag so advisors can deploy idle capital.",
        input_freshness_seconds=86_400,
        known_limitations=(
            "Does not distinguish intentional cash reserves (e.g., pending distribution).",
            "Expected return estimate used for drag cost is approximate.",
        ),
    )

    def __init__(
        self,
        default_cash_target_pct: float = 2.0,
        excess_threshold_pct: float = 5.0,
        severe_threshold_pct: float = 15.0,
        min_excess_dollars: float = 5_000.0,
        assumed_annual_return: float = 0.07,
    ) -> None:
        self._default_target = default_cash_target_pct
        self._excess_thresh = excess_threshold_pct
        self._severe_thresh = severe_threshold_pct
        self._min_excess = min_excess_dollars
        self._assumed_return = assumed_annual_return

    def score(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """
        inputs:
            accounts: list[dict]
                Each has: account_id, client_id, client_name, total_value,
                          cash_balance, cash_target_pct (optional)
            as_of: str
        """
        as_of = inputs["as_of"]
        accounts = inputs["accounts"]
        findings: list[dict[str, Any]] = []

        for acct in accounts:
            total_value = float(acct["total_value"])
            cash_balance = float(acct["cash_balance"])
            target_pct = float(acct.get("cash_target_pct", self._default_target))

            if total_value <= 0:
                continue

            cash_pct = (cash_balance / total_value) * 100
            excess_pct = cash_pct - target_pct
            excess_dollars = cash_balance - (total_value * target_pct / 100)

            if excess_pct < self._excess_thresh or excess_dollars < self._min_excess:
                continue

            # Estimated annual drag cost
            drag_cost = excess_dollars * self._assumed_return

            if excess_pct >= self._severe_thresh:
                severity = "action_needed"
            else:
                severity = "warning"

            findings.append({
                "account_id": acct["account_id"],
                "client_id": acct.get("client_id"),
                "client_name": acct.get("client_name", ""),
                "total_value": round(total_value, 2),
                "cash_balance": round(cash_balance, 2),
                "cash_pct": round(cash_pct, 2),
                "target_pct": target_pct,
                "excess_pct": round(excess_pct, 2),
                "excess_dollars": round(excess_dollars, 2),
                "estimated_annual_drag": round(drag_cost, 2),
                "severity": severity,
            })

        # Sort by drag cost descending
        findings.sort(key=lambda f: f["estimated_annual_drag"], reverse=True)

        total_excess = sum(f["excess_dollars"] for f in findings)
        total_drag = sum(f["estimated_annual_drag"] for f in findings)

        return {
            "as_of": as_of,
            "accounts_scanned": len(accounts),
            "accounts_flagged": len(findings),
            "total_excess_cash": round(total_excess, 2),
            "total_estimated_annual_drag": round(total_drag, 2),
            "severity": (
                "action_needed" if any(f["severity"] == "action_needed" for f in findings)
                else "warning" if findings
                else "ok"
            ),
            "findings": findings,
            "assumptions": [
                f"Assumed annual portfolio return: {self._assumed_return:.0%}",
                f"Excess threshold: {self._excess_thresh}% above target",
                f"Minimum excess: ${self._min_excess:,.0f}",
            ],
        }
```

---

## 10. Style Profile Extractor

Analyzes an advisor's sent email corpus to extract a structured writing
style profile used by the email drafting agent (Feature 3) to match
each advisor's tone.

### 10.1 What it extracts

| Dimension | Method |
|-----------|--------|
| Formality level | Vocabulary analysis: formal vs casual word ratios |
| Greeting patterns | Frequency-ranked list of opening phrases |
| Sign-off style | Frequency-ranked list of closing phrases |
| Average email length | Token/word statistics |
| Vocabulary preferences | TF-IDF top terms relative to a baseline corpus |
| Sentence complexity | Mean words per sentence, Flesch-Kincaid grade level |

### 10.2 Implementation

```python
"""
sidecar/app/analytics/style_profile.py

Deterministic text-analysis model — no LLM required.
Uses scipy and numpy for statistical computation.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import stats as sp_stats

from app.analytics.registry import (
    AnalyticalModel,
    ModelCategory,
    ModelKind,
    ModelMetadata,
)


# ---------------------------------------------------------------------------
# Reference word lists for formality scoring
# ---------------------------------------------------------------------------

FORMAL_MARKERS = frozenset({
    "regarding", "furthermore", "consequently", "therefore", "accordingly",
    "pursuant", "hereby", "enclosed", "attached", "kindly", "respectfully",
    "sincerely", "appreciate", "acknowledge", "advise", "inform",
    "per", "please", "herein", "aforementioned", "facilitate",
})

CASUAL_MARKERS = frozenset({
    "hey", "hi", "thanks", "cool", "awesome", "great", "gonna", "wanna",
    "fyi", "btw", "asap", "np", "yeah", "yep", "nope", "sure",
    "quick", "heads-up", "touch base", "loop in", "ping",
})

# Common greeting patterns (regex)
GREETING_PATTERNS = [
    (r"^hi\s+\w+", "Hi [Name]"),
    (r"^hey\s+\w+", "Hey [Name]"),
    (r"^hello\s+\w+", "Hello [Name]"),
    (r"^dear\s+\w+", "Dear [Name]"),
    (r"^good\s+(morning|afternoon|evening)", "Good [time of day]"),
    (r"^greetings", "Greetings"),
    (r"^hope\s+this\s+(finds|email)", "Hope this finds you well"),
]

# Common sign-off patterns (regex, applied to last 3 lines)
SIGNOFF_PATTERNS = [
    (r"best\s*regards?", "Best regards"),
    (r"kind\s*regards?", "Kind regards"),
    (r"warm\s*regards?", "Warm regards"),
    (r"sincerely", "Sincerely"),
    (r"thank(s|\s+you)", "Thanks / Thank you"),
    (r"all\s+the\s+best", "All the best"),
    (r"cheers", "Cheers"),
    (r"talk\s+soon", "Talk soon"),
    (r"best,?\s*$", "Best"),
    (r"regards,?\s*$", "Regards"),
]


@dataclass
class StyleProfile:
    """Structured style profile for one advisor."""
    advisor_id: str
    email_count: int
    formality_score: float              # 0 (very casual) to 1 (very formal)
    formality_label: str                # "formal", "semi-formal", "casual"
    greeting_distribution: dict[str, float]   # pattern -> frequency
    signoff_distribution: dict[str, float]
    avg_word_count: float
    median_word_count: float
    stddev_word_count: float
    avg_sentence_length: float          # words per sentence
    flesch_kincaid_grade: float
    top_vocabulary: list[tuple[str, float]]   # (word, tf-idf score)
    sample_greetings: list[str]
    sample_signoffs: list[str]


class StyleProfileExtractor:
    """
    Deterministic/heuristic model: analyze sent emails to build a
    structured style profile.  No LLM dependency.
    """

    metadata = ModelMetadata(
        name="style_profile_extractor",
        version="1.0.0",
        owner="personalization",
        category=ModelCategory.PERSONALIZATION,
        kind=ModelKind.HEURISTIC,
        description=(
            "Analyze advisor's sent emails to extract formality level, greeting "
            "patterns, sign-off style, length stats, and vocabulary preferences."
        ),
        use_case="Power email drafting in the advisor's authentic writing style.",
        input_freshness_seconds=604_800,  # refresh weekly
        known_limitations=(
            "Requires at least 20 emails for stable statistics.",
            "Formality scoring uses word-list heuristics, not contextual understanding.",
            "Does not distinguish between email types (client vs internal).",
        ),
    )

    def __init__(self, min_emails: int = 20, top_vocab_count: int = 30) -> None:
        self._min_emails = min_emails
        self._top_vocab = top_vocab_count

    def score(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """
        inputs:
            advisor_id: str
            emails: list[dict]
                Each has: body (str), subject (str), sent_at (str)
        """
        advisor_id = inputs["advisor_id"]
        emails = inputs["emails"]

        if len(emails) < self._min_emails:
            return {
                "advisor_id": advisor_id,
                "status": "insufficient_data",
                "email_count": len(emails),
                "minimum_required": self._min_emails,
            }

        bodies = [e["body"] for e in emails]

        # --- Word counts ---
        word_counts = np.array([len(self._tokenize(b)) for b in bodies])
        avg_wc = float(np.mean(word_counts))
        median_wc = float(np.median(word_counts))
        std_wc = float(np.std(word_counts))

        # --- Formality ---
        formality = self._compute_formality(bodies)
        if formality >= 0.65:
            formality_label = "formal"
        elif formality >= 0.35:
            formality_label = "semi-formal"
        else:
            formality_label = "casual"

        # --- Greetings ---
        greeting_counts, greeting_samples = self._extract_greetings(bodies)
        greeting_total = sum(greeting_counts.values()) or 1
        greeting_dist = {k: round(v / greeting_total, 3) for k, v in greeting_counts.most_common(5)}

        # --- Sign-offs ---
        signoff_counts, signoff_samples = self._extract_signoffs(bodies)
        signoff_total = sum(signoff_counts.values()) or 1
        signoff_dist = {k: round(v / signoff_total, 3) for k, v in signoff_counts.most_common(5)}

        # --- Sentence statistics ---
        all_sentences = []
        for body in bodies:
            sents = self._split_sentences(body)
            all_sentences.extend(sents)

        sent_lengths = np.array([len(self._tokenize(s)) for s in all_sentences]) if all_sentences else np.array([0])
        avg_sent_len = float(np.mean(sent_lengths))

        # --- Flesch-Kincaid grade level ---
        fk_grade = self._flesch_kincaid_grade(bodies)

        # --- TF-IDF vocabulary ---
        top_vocab = self._compute_tfidf(bodies)

        profile = StyleProfile(
            advisor_id=advisor_id,
            email_count=len(emails),
            formality_score=round(formality, 3),
            formality_label=formality_label,
            greeting_distribution=greeting_dist,
            signoff_distribution=signoff_dist,
            avg_word_count=round(avg_wc, 1),
            median_word_count=round(median_wc, 1),
            stddev_word_count=round(std_wc, 1),
            avg_sentence_length=round(avg_sent_len, 1),
            flesch_kincaid_grade=round(fk_grade, 1),
            top_vocabulary=top_vocab[:self._top_vocab],
            sample_greetings=greeting_samples[:5],
            sample_signoffs=signoff_samples[:5],
        )

        return self._profile_to_dict(profile)

    # ------------------------------------------------------------------
    # Text analysis helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Simple whitespace + punctuation tokenizer."""
        return re.findall(r"[a-z']+", text.lower())

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """Split text into sentences on . ! ? followed by space or end."""
        return [s.strip() for s in re.split(r'[.!?]+\s+', text) if len(s.strip()) > 3]

    def _compute_formality(self, bodies: list[str]) -> float:
        """Ratio-based formality score (0=casual, 1=formal)."""
        formal_count = 0
        casual_count = 0
        for body in bodies:
            tokens = set(self._tokenize(body))
            formal_count += len(tokens & FORMAL_MARKERS)
            casual_count += len(tokens & CASUAL_MARKERS)
        total = formal_count + casual_count
        if total == 0:
            return 0.5  # neutral
        return formal_count / total

    @staticmethod
    def _extract_greetings(bodies: list[str]) -> tuple[Counter, list[str]]:
        counts: Counter = Counter()
        samples: list[str] = []
        for body in bodies:
            first_line = body.strip().split("\n")[0].strip().rstrip(",")
            lower = first_line.lower()
            for pattern, label in GREETING_PATTERNS:
                if re.match(pattern, lower):
                    counts[label] += 1
                    if len(samples) < 10:
                        samples.append(first_line)
                    break
            else:
                counts["(other)"] += 1
        return counts, samples

    @staticmethod
    def _extract_signoffs(bodies: list[str]) -> tuple[Counter, list[str]]:
        counts: Counter = Counter()
        samples: list[str] = []
        for body in bodies:
            lines = [l.strip() for l in body.strip().split("\n") if l.strip()]
            last_lines = " ".join(lines[-3:]).lower() if lines else ""
            for pattern, label in SIGNOFF_PATTERNS:
                if re.search(pattern, last_lines):
                    counts[label] += 1
                    if len(samples) < 10 and lines:
                        samples.append(lines[-2] if len(lines) >= 2 else lines[-1])
                    break
            else:
                counts["(other)"] += 1
        return counts, samples

    def _flesch_kincaid_grade(self, bodies: list[str]) -> float:
        """Compute Flesch-Kincaid Grade Level across the corpus."""
        total_words = 0
        total_sentences = 0
        total_syllables = 0
        for body in bodies:
            words = self._tokenize(body)
            sents = self._split_sentences(body)
            total_words += len(words)
            total_sentences += max(len(sents), 1)
            total_syllables += sum(self._count_syllables(w) for w in words)

        if total_words == 0 or total_sentences == 0:
            return 0.0

        asl = total_words / total_sentences       # average sentence length
        asw = total_syllables / total_words       # average syllables per word
        return 0.39 * asl + 11.8 * asw - 15.59

    @staticmethod
    def _count_syllables(word: str) -> int:
        """Rough syllable count heuristic."""
        word = word.lower().rstrip("e")
        vowels = re.findall(r"[aeiouy]+", word)
        return max(len(vowels), 1)

    def _compute_tfidf(self, bodies: list[str]) -> list[tuple[str, float]]:
        """Simple TF-IDF over the email corpus."""
        # Document frequency
        n_docs = len(bodies)
        df: Counter = Counter()
        tf_total: Counter = Counter()
        stop_words = frozenset({
            "the", "a", "an", "is", "was", "are", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "shall", "can",
            "to", "of", "in", "for", "on", "with", "at", "by", "from",
            "as", "into", "through", "during", "before", "after", "and",
            "but", "or", "nor", "not", "so", "yet", "both", "either",
            "neither", "each", "every", "all", "any", "few", "more",
            "most", "other", "some", "such", "no", "only", "own", "same",
            "than", "too", "very", "just", "about", "above", "below",
            "between", "up", "down", "out", "off", "over", "under",
            "again", "further", "then", "once", "here", "there", "when",
            "where", "why", "how", "what", "which", "who", "whom",
            "this", "that", "these", "those", "i", "me", "my", "myself",
            "we", "our", "ours", "you", "your", "yours", "he", "him",
            "his", "she", "her", "hers", "it", "its", "they", "them",
            "their", "if", "also", "get", "got",
        })

        for body in bodies:
            tokens = self._tokenize(body)
            filtered = [t for t in tokens if t not in stop_words and len(t) > 2]
            tf_total.update(filtered)
            unique = set(filtered)
            df.update(unique)

        # TF-IDF
        tfidf_scores: list[tuple[str, float]] = []
        for word, freq in tf_total.items():
            idf = math.log(n_docs / (1 + df[word]))
            tfidf_scores.append((word, round(freq * idf, 4)))

        tfidf_scores.sort(key=lambda x: x[1], reverse=True)
        return tfidf_scores

    @staticmethod
    def _profile_to_dict(p: StyleProfile) -> dict[str, Any]:
        return {
            "advisor_id": p.advisor_id,
            "status": "complete",
            "email_count": p.email_count,
            "formality": {
                "score": p.formality_score,
                "label": p.formality_label,
            },
            "greetings": {
                "distribution": p.greeting_distribution,
                "samples": p.sample_greetings,
            },
            "signoffs": {
                "distribution": p.signoff_distribution,
                "samples": p.sample_signoffs,
            },
            "length": {
                "avg_words": p.avg_word_count,
                "median_words": p.median_word_count,
                "stddev_words": p.stddev_word_count,
            },
            "complexity": {
                "avg_sentence_length": p.avg_sentence_length,
                "flesch_kincaid_grade": p.flesch_kincaid_grade,
            },
            "vocabulary": {
                "top_terms": [{"term": t, "score": s} for t, s in p.top_vocabulary],
            },
        }
```

### 10.3 Injecting the profile into email drafting

The style profile is stored in Redis with key
`style_profile:{tenant_id}:{advisor_id}` and a 7-day TTL. When the email
drafting agent (Feature 3) runs, it retrieves the profile and injects it
into the system prompt:

```python
# In the email drafter agent's system prompt builder
style = await redis.get(f"style_profile:{tenant_id}:{advisor_id}")
if style:
    system_prompt += f"""

Match this advisor's writing style:
- Formality: {style['formality']['label']}
- Typical greeting: {style['greetings']['samples'][0] if style['greetings']['samples'] else 'Hi [Name]'}
- Typical sign-off: {style['signoffs']['samples'][0] if style['signoffs']['samples'] else 'Best'}
- Target length: ~{style['length']['avg_words']:.0f} words
- Preferred vocabulary includes: {', '.join(t['term'] for t in style['vocabulary']['top_terms'][:10])}
"""
```

---

## 11. Model Governance

Every registered model carries a `ModelMetadata` dataclass that serves as
its governance declaration. This is enforced at registration time: the
`ModelRegistry` rejects any model that does not expose a valid `metadata`
attribute.

### 11.1 ModelMetadata dataclass (full definition)

Reproduced from section 1 for completeness:

```python
@dataclass(frozen=True)
class ModelMetadata:
    """
    Governance declaration attached to every registered analytical model.

    Maps directly to spec section 12.5 requirements:
      - owner
      - intended decision-support use case
      - required input freshness
      - known limitations
      - reviewability of outputs
      - whether the model is deterministic, heuristic, or learned
    """
    name: str                               # Unique model identifier
    version: str                            # Semver string (e.g., "1.0.0")
    owner: str                              # Team or individual responsible
    category: ModelCategory                 # tax, portfolio, compliance, personalization, firm_analytics
    kind: ModelKind                         # deterministic, heuristic, or learned
    description: str                        # Human-readable description
    use_case: str                           # Intended decision-support use case
    input_freshness_seconds: int            # Max age of inputs before model should warn
    known_limitations: tuple[str, ...]      # Explicit limitations visible to reviewers
    reviewable: bool = True                 # Can an advisor inspect and override outputs?
    registered_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
```

### 11.2 Governance summary for all models

| Model | Version | Kind | Category | Freshness | Owner |
|-------|---------|------|----------|-----------|-------|
| `tax_loss_harvesting` | 1.0.0 | deterministic | tax | 24h | portfolio-analytics |
| `concentration_risk` | 1.0.0 | deterministic | portfolio | 24h | portfolio-analytics |
| `drift_detection` | 1.0.0 | deterministic | portfolio | 24h | portfolio-analytics |
| `rmd_calculator` | 1.0.0 | deterministic | tax | 24h | portfolio-analytics |
| `tax_scenario_engine` | 1.0.0 | heuristic | tax | 7d | tax-planning |
| `firm_opportunity_ranker` | 1.0.0 | heuristic | firm_analytics | 24h | firm-analytics |
| `beneficiary_audit` | 1.0.0 | deterministic | compliance | 24h | compliance |
| `cash_drag_detector` | 1.0.0 | deterministic | portfolio | 24h | portfolio-analytics |
| `style_profile_extractor` | 1.0.0 | heuristic | personalization | 7d | personalization |

### 11.3 Input freshness enforcement

Before invoking a model, callers should check that the `as_of` timestamp on
input data does not exceed `input_freshness_seconds`:

```python
from datetime import datetime, timezone

def check_freshness(model_meta: ModelMetadata, data_as_of: str) -> bool:
    """Return True if inputs are fresh enough for the model."""
    data_ts = datetime.fromisoformat(data_as_of).replace(tzinfo=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - data_ts).total_seconds()
    return age_seconds <= model_meta.input_freshness_seconds
```

### 11.4 Audit trail

Every `invoke()` call through the registry attaches `_model`, `_version`,
and `_scored_at` to the output dict. Downstream consumers (the platform API
or report artifact storage) should persist these fields so that any
analytical output can be traced back to the exact model version and
invocation time.

---

## Appendix A: Module Layout

```text
sidecar/app/analytics/
    __init__.py
    registry.py                  # ModelRegistry, ModelMetadata, ModelKind, ModelCategory
    startup.py                   # register_all_models()
    tax_loss_harvesting.py       # TaxLossHarvestingScorer
    concentration_risk.py        # ConcentrationRiskScorer
    drift_detection.py           # DriftDetector
    rmd_calculator.py            # RMDCalculator
    tax_scenario_engine.py       # TaxScenarioEngine
    firm_ranker.py               # FirmWideOpportunityRanker
    beneficiary_audit.py         # BeneficiaryCompletenessAudit
    cash_drag.py                 # CashDragDetector
    style_profile.py             # StyleProfileExtractor
```

## Appendix B: Dependencies

All models depend only on the Python standard library plus:

| Package | Usage |
|---------|-------|
| `numpy` | Array math for HHI, drift RMS, word-count statistics |
| `scipy` | Statistical utilities in style profiling |

No model in this layer calls an LLM. Every model is testable with
deterministic inputs and expected outputs.

## Appendix C: Integration with Feature Endpoints

| Endpoint | Models invoked |
|----------|----------------|
| `POST /ai/tax/plan` | `tax_loss_harvesting`, `rmd_calculator`, `tax_scenario_engine` |
| `POST /ai/portfolio/analyze` | `concentration_risk`, `drift_detection`, `cash_drag_detector`, `beneficiary_audit`, `tax_loss_harvesting` |
| `POST /ai/reports/firm-wide` | All models via `firm_opportunity_ranker` aggregation |
| `POST /ai/email/draft` | `style_profile_extractor` (profile lookup, not inline scoring) |
| `POST /ai/digest/generate` | `rmd_calculator`, `drift_detection`, `cash_drag_detector` (for alert generation) |
