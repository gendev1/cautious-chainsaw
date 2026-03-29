"""
Cash drag detector.
"""
from __future__ import annotations

from typing import Any

from app.analytics.registry import (
    ModelCategory,
    ModelKind,
    ModelMetadata,
)


class CashDragDetector:
    """
    Deterministic model: flag accounts holding more cash
    than their target allocation or an absolute threshold.
    """

    metadata = ModelMetadata(
        name="cash_drag_detector",
        version="1.0.0",
        owner="portfolio-analytics",
        category=ModelCategory.PORTFOLIO,
        kind=ModelKind.DETERMINISTIC,
        description=(
            "Identify accounts with excessive uninvested "
            "cash relative to model targets or absolute "
            "thresholds."
        ),
        use_case=(
            "Surface cash drag so advisors can deploy "
            "idle capital."
        ),
        input_freshness_seconds=86_400,
        known_limitations=(
            "Does not distinguish intentional cash "
            "reserves (e.g., pending distribution).",
            "Expected return estimate used for drag cost "
            "is approximate.",
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

    def score(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        inputs:
            accounts: list[dict]
                Each has: account_id, client_id,
                    client_name, total_value,
                    cash_balance, cash_target_pct
            as_of: str
        """
        as_of = inputs["as_of"]
        accounts = inputs["accounts"]
        findings: list[dict[str, Any]] = []

        for acct in accounts:
            total_value = float(acct["total_value"])
            cash_balance = float(acct["cash_balance"])
            target_pct = float(
                acct.get(
                    "cash_target_pct",
                    self._default_target,
                )
            )

            if total_value <= 0:
                continue

            cash_pct = (cash_balance / total_value) * 100
            excess_pct = cash_pct - target_pct
            excess_dollars = cash_balance - (
                total_value * target_pct / 100
            )

            if (
                excess_pct < self._excess_thresh
                or excess_dollars < self._min_excess
            ):
                continue

            # Estimated annual drag cost
            drag_cost = (
                excess_dollars * self._assumed_return
            )

            if excess_pct >= self._severe_thresh:
                severity = "action_needed"
            else:
                severity = "warning"

            findings.append(
                {
                    "account_id": acct["account_id"],
                    "client_id": acct.get("client_id"),
                    "client_name": acct.get(
                        "client_name", ""
                    ),
                    "total_value": round(total_value, 2),
                    "cash_balance": round(
                        cash_balance, 2
                    ),
                    "cash_pct": round(cash_pct, 2),
                    "target_pct": target_pct,
                    "excess_pct": round(excess_pct, 2),
                    "excess_dollars": round(
                        excess_dollars, 2
                    ),
                    "estimated_annual_drag": round(
                        drag_cost, 2
                    ),
                    "severity": severity,
                }
            )

        # Sort by drag cost descending
        findings.sort(
            key=lambda f: f["estimated_annual_drag"],
            reverse=True,
        )

        total_excess = sum(
            f["excess_dollars"] for f in findings
        )
        total_drag = sum(
            f["estimated_annual_drag"] for f in findings
        )

        return {
            "as_of": as_of,
            "accounts_scanned": len(accounts),
            "accounts_flagged": len(findings),
            "total_excess_cash": round(total_excess, 2),
            "total_estimated_annual_drag": round(
                total_drag, 2
            ),
            "severity": (
                "action_needed"
                if any(
                    f["severity"] == "action_needed"
                    for f in findings
                )
                else "warning"
                if findings
                else "ok"
            ),
            "findings": findings,
            "assumptions": [
                f"Assumed annual portfolio return: "
                f"{self._assumed_return:.0%}",
                f"Excess threshold: "
                f"{self._excess_thresh}% above target",
                f"Minimum excess: "
                f"${self._min_excess:,.0f}",
            ],
        }
