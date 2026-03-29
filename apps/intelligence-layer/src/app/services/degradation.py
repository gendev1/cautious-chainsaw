"""
app/services/degradation.py — Dependency health and degradation.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger("sidecar.degradation")


class DegradedResult(BaseModel):
    """Marks a result as degraded with an explanation."""

    data: Any = None
    degraded: bool = False
    degradation_reason: str | None = None
    warnings: list[str] = []


class DependencyHealth:
    """Tracks health of external dependencies."""

    def __init__(self) -> None:
        self._failure_counts: dict[str, int] = {}
        self._thresholds: dict[str, int] = {
            "llm_primary": 3,
            "llm_fallback": 3,
            "platform_api": 5,
            "vector_store": 3,
            "redis": 5,
            "transcription": 3,
        }

    def record_failure(self, dependency: str) -> None:
        self._failure_counts[dependency] = (
            self._failure_counts.get(dependency, 0) + 1
        )
        logger.warning(
            "dependency_failure_recorded: %s (%d)",
            dependency,
            self._failure_counts[dependency],
        )

    def record_success(self, dependency: str) -> None:
        self._failure_counts[dependency] = 0

    def is_healthy(self, dependency: str) -> bool:
        count = self._failure_counts.get(dependency, 0)
        threshold = self._thresholds.get(dependency, 3)
        return count < threshold


dependency_health = DependencyHealth()
