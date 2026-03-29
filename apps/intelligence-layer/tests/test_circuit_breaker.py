"""Tests for CircuitBreaker — consecutive-failure circuit breaker."""
from __future__ import annotations

import time

import pytest

from app.services.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
)


def test_closed_allows_requests() -> None:
    """New circuit breaker is CLOSED and allows requests."""
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout_s=1.0)
    assert cb.state == "CLOSED"
    cb.check()  # should not raise


def test_opens_after_threshold() -> None:
    """Circuit opens after N consecutive failures."""
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout_s=10.0)
    for _ in range(3):
        cb.record_failure()
    assert cb.state == "OPEN"
    with pytest.raises(CircuitOpenError):
        cb.check()


def test_rejects_when_open() -> None:
    """Open circuit rejects all requests."""
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout_s=100.0)
    cb.record_failure()
    cb.record_failure()
    with pytest.raises(CircuitOpenError) as exc_info:
        cb.check()
    assert exc_info.value.failures == 2


def test_half_open_after_recovery_timeout() -> None:
    """Circuit transitions to HALF_OPEN after recovery timeout."""
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout_s=0.01)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "OPEN"
    time.sleep(0.02)
    cb.check()  # should transition to HALF_OPEN
    assert cb.state == "HALF_OPEN"


def test_closes_after_successful_probe() -> None:
    """Successful probe in HALF_OPEN state closes the circuit."""
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout_s=0.01)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.02)
    cb.check()  # HALF_OPEN
    cb.record_success()
    assert cb.state == "CLOSED"


def test_reopens_after_failed_probe() -> None:
    """Failed probe in HALF_OPEN reopens the circuit."""
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout_s=0.01)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.02)
    cb.check()  # HALF_OPEN
    cb.record_failure()
    assert cb.state == "OPEN"
