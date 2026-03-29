"""
Drift detection model.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from app.analytics.registry import (
    ModelCategory,
    ModelKind,
    ModelMetadata,
)


class DriftDetector:
    """
    Deterministic model: measure allocation drift vs a
    target model.
    """

    metadata = ModelMetadata(
        name="drift_detection",
        version="1.0.0",
        owner="portfolio-analytics",
        category=ModelCategory.PORTFOLIO,
        kind=ModelKind.DETERMINISTIC,
        description=(
            "Compare current allocation vs model target, "
            "compute per-bucket and overall drift, and "
            "assign severity."
        ),
        use_case=(
            "Detect when a portfolio has drifted beyond "
            "tolerance bands."
        ),
        input_freshness_seconds=86_400,
        known_limitations=(
            "Assumes asset class mapping is pre-computed "
            "upstream.",
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

    def score(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        inputs:
            current_allocation: dict[str, float]
                Maps asset class -> current weight (0-100).
            target_allocation: dict[str, float]
                Maps asset class -> target weight (0-100).
            thresholds: dict[str, float] | None
            as_of: str
        """
        current = inputs["current_allocation"]
        target = inputs["target_allocation"]
        custom_thresholds = inputs.get("thresholds") or {}
        as_of = inputs["as_of"]

        # Union of all asset classes
        all_classes = sorted(
            set(current.keys()) | set(target.keys())
        )

        position_drifts: list[dict[str, Any]] = []
        abs_drifts: list[float] = []

        for ac in all_classes:
            cur_w = float(current.get(ac, 0.0))
            tgt_w = float(target.get(ac, 0.0))
            drift_pct = cur_w - tgt_w
            abs_drift = abs(drift_pct)
            abs_drifts.append(abs_drift)

            threshold = float(
                custom_thresholds.get(
                    ac, self._default_thresh
                )
            )

            if abs_drift >= self._severe_thresh:
                severity = "action_needed"
            elif abs_drift >= threshold:
                severity = "warning"
            else:
                severity = "ok"

            position_drifts.append(
                {
                    "asset_class": ac,
                    "current_weight": round(cur_w, 2),
                    "target_weight": round(tgt_w, 2),
                    "drift_pct": round(drift_pct, 2),
                    "abs_drift_pct": round(abs_drift, 2),
                    "threshold": threshold,
                    "severity": severity,
                }
            )

        # Overall drift metrics
        drift_array = np.array(abs_drifts)
        max_drift = (
            float(np.max(drift_array))
            if len(drift_array) > 0
            else 0.0
        )
        mean_drift = (
            float(np.mean(drift_array))
            if len(drift_array) > 0
            else 0.0
        )

        # Root-mean-square drift
        rms_drift = (
            float(np.sqrt(np.mean(drift_array**2)))
            if len(drift_array) > 0
            else 0.0
        )

        # Overall severity
        if max_drift >= self._severe_thresh:
            overall_severity = "action_needed"
        elif max_drift >= self._default_thresh:
            overall_severity = "warning"
        else:
            overall_severity = "ok"

        # Drift score: 0-100 (higher = more drifted)
        drift_score = (
            min(rms_drift / self._severe_thresh, 1.0) * 100
        )

        return {
            "as_of": as_of,
            "overall_severity": overall_severity,
            "drift_score": round(drift_score, 2),
            "max_drift_pct": round(max_drift, 2),
            "mean_drift_pct": round(mean_drift, 2),
            "rms_drift_pct": round(rms_drift, 2),
            "position_drifts": position_drifts,
            "positions_breaching": sum(
                1
                for pd in position_drifts
                if pd["severity"] != "ok"
            ),
        }
