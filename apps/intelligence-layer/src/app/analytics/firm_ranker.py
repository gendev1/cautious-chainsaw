"""
Firm-wide opportunity ranker.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np

from app.analytics.registry import (
    ModelCategory,
    ModelKind,
    ModelMetadata,
)

# -------------------------------------------------------------------
# Urgency decay profiles
# -------------------------------------------------------------------

URGENCY_PROFILES: dict[str, dict[str, Any]] = {
    "rmd_deadline": {
        "base_urgency": 1.0,
        "decay_type": "cliff",
        "critical_days": 30,
    },
    "tax_loss_harvest": {
        "base_urgency": 0.7,
        "decay_type": "year_end",
    },
    "concentration_risk": {
        "base_urgency": 0.5,
        "decay_type": "none",
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
    Heuristic model: aggregate account-level analytical
    scores into a firm-wide priority list ranked by
    estimated_impact * confidence * urgency.
    """

    metadata = ModelMetadata(
        name="firm_opportunity_ranker",
        version="1.0.0",
        owner="firm-analytics",
        category=ModelCategory.FIRM_ANALYTICS,
        kind=ModelKind.HEURISTIC,
        description=(
            "Aggregate account-level scores from tax, "
            "portfolio, and compliance models into a "
            "single firm-wide priority list."
        ),
        use_case=(
            "Help firm leadership and advisors prioritize "
            "the highest-impact actions."
        ),
        input_freshness_seconds=86_400,
        known_limitations=(
            "Dollar impact estimates are approximate "
            "and model-dependent.",
            "Urgency profiles are configurable heuristics, "
            "not market-derived.",
            "Does not deduplicate overlapping opportunities "
            "across models.",
        ),
    )

    def score(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        inputs:
            opportunities: list[dict]
                Each has: client_id, client_name,
                    account_id, advisor_id,
                    opportunity_type, severity,
                    estimated_dollar_impact,
                    source_model, details, deadline
            as_of: str
        """
        as_of = date.fromisoformat(inputs["as_of"])
        raw = inputs["opportunities"]

        scored: list[dict[str, Any]] = []

        for opp in raw:
            opp_type = opp["opportunity_type"]
            dollar_impact = float(
                opp.get("estimated_dollar_impact", 0)
            )
            severity = opp.get("severity", "info")
            deadline_str = opp.get("deadline")

            confidence = CONFIDENCE_MAP.get(
                severity, 0.50
            )
            urgency = self._compute_urgency(
                opp_type, as_of, deadline_str
            )

            # Composite rank score
            impact_norm = min(
                dollar_impact / 100_000, 1.0
            )
            rank_score = (
                impact_norm * confidence * urgency
            )

            scored.append(
                {
                    "client_id": opp["client_id"],
                    "client_name": opp.get(
                        "client_name", ""
                    ),
                    "account_id": opp.get("account_id"),
                    "advisor_id": opp.get("advisor_id"),
                    "opportunity_type": opp_type,
                    "estimated_dollar_impact": round(
                        dollar_impact, 2
                    ),
                    "severity": severity,
                    "confidence": round(confidence, 2),
                    "urgency": round(urgency, 4),
                    "rank_score": round(rank_score, 6),
                    "source_model": opp.get(
                        "source_model", "unknown"
                    ),
                    "deadline": deadline_str,
                    "details": opp.get("details", {}),
                }
            )

        # Sort by rank_score descending
        scored.sort(
            key=lambda s: s["rank_score"], reverse=True
        )

        # Assign ordinal rank
        for i, item in enumerate(scored):
            item["rank"] = i + 1

        # Aggregate stats
        scores_arr = (
            np.array([s["rank_score"] for s in scored])
            if scored
            else np.array([])
        )
        total_impact = sum(
            s["estimated_dollar_impact"] for s in scored
        )

        # Group by opportunity type
        type_counts: dict[str, int] = {}
        type_impact: dict[str, float] = {}
        for s in scored:
            t = s["opportunity_type"]
            type_counts[t] = type_counts.get(t, 0) + 1
            type_impact[t] = (
                type_impact.get(t, 0)
                + s["estimated_dollar_impact"]
            )

        mean_score = (
            round(float(np.mean(scores_arr)), 6)
            if len(scores_arr) > 0
            else 0.0
        )

        return {
            "as_of": as_of.isoformat(),
            "total_opportunities": len(scored),
            "total_estimated_impact": round(
                total_impact, 2
            ),
            "mean_rank_score": mean_score,
            "by_type": {
                t: {
                    "count": type_counts[t],
                    "total_impact": round(
                        type_impact[t], 2
                    ),
                }
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
        """Compute urgency multiplier (0-1)."""
        profile = URGENCY_PROFILES.get(
            opp_type,
            {"base_urgency": 0.5, "decay_type": "none"},
        )
        base = profile["base_urgency"]

        decay_type = profile["decay_type"]

        if decay_type == "cliff" and deadline_str:
            deadline = date.fromisoformat(deadline_str)
            days_remaining = (deadline - as_of).days
            critical = profile.get("critical_days", 30)
            if days_remaining <= 0:
                return 1.0  # overdue
            if days_remaining <= critical:
                return base + (1.0 - base) * (
                    1 - days_remaining / critical
                )
            return base * 0.8  # well ahead

        if decay_type == "year_end":
            year_end = date(as_of.year, 12, 31)
            days_to_ye = (year_end - as_of).days
            if days_to_ye <= 0:
                return 1.0
            ye_factor = max(0, 1 - days_to_ye / 365)
            return base + (1.0 - base) * ye_factor**2

        # decay_type == "none"
        return base
