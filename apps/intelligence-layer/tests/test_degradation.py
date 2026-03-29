"""Tests for dependency health tracking and graceful degradation."""
from __future__ import annotations

from app.services.degradation import DependencyHealth


def test_starts_healthy() -> None:
    """All dependencies start healthy."""
    dh = DependencyHealth()
    assert dh.is_healthy("llm_primary") is True
    assert dh.is_healthy("platform_api") is True


def test_becomes_unhealthy_after_threshold() -> None:
    """Dependency becomes unhealthy after exceeding failure threshold."""
    dh = DependencyHealth()
    for _ in range(3):
        dh.record_failure("llm_primary")
    assert dh.is_healthy("llm_primary") is False


def test_recovers_after_success() -> None:
    """Dependency recovers after a success."""
    dh = DependencyHealth()
    for _ in range(3):
        dh.record_failure("llm_primary")
    assert dh.is_healthy("llm_primary") is False
    dh.record_success("llm_primary")
    assert dh.is_healthy("llm_primary") is True
