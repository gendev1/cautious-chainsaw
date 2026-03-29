"""
app/services/circuit_breaker.py — Consecutive-failure circuit breaker.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger("sidecar.circuit_breaker")


class CircuitOpenError(Exception):
    """Raised when the circuit breaker is open."""

    def __init__(
        self, failures: int, recovery_at: float
    ) -> None:
        remaining = max(
            0.0, recovery_at - time.monotonic()
        )
        super().__init__(
            f"Circuit open after {failures} consecutive "
            f"failures. Recovery in {remaining:.1f}s."
        )
        self.failures = failures
        self.recovery_at = recovery_at


class CircuitBreaker:
    """Simple consecutive-failure circuit breaker.

    States:
      CLOSED    -- normal operation
      OPEN      -- too many failures, requests rejected
      HALF_OPEN -- recovery timeout elapsed, one probe allowed
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_s: float = 30.0,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout_s = recovery_timeout_s
        self._consecutive_failures = 0
        self._last_failure_time: float = 0.0
        self._state: str = "CLOSED"

    @property
    def state(self) -> str:
        return self._state

    def check(self) -> None:
        """Raises CircuitOpenError if requests are blocked."""
        if self._state == "CLOSED":
            return

        if self._state == "OPEN":
            elapsed = (
                time.monotonic() - self._last_failure_time
            )
            if elapsed >= self._recovery_timeout_s:
                self._state = "HALF_OPEN"
                logger.info(
                    "circuit breaker transitioning to "
                    "HALF_OPEN"
                )
                return
            raise CircuitOpenError(
                failures=self._consecutive_failures,
                recovery_at=(
                    self._last_failure_time
                    + self._recovery_timeout_s
                ),
            )

        # HALF_OPEN: allow the probe request
        return

    def record_success(self) -> None:
        if self._state == "HALF_OPEN":
            logger.info(
                "circuit breaker closing after "
                "successful probe"
            )
        self._consecutive_failures = 0
        self._state = "CLOSED"

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        self._last_failure_time = time.monotonic()

        if (
            self._consecutive_failures
            >= self._failure_threshold
        ):
            if self._state != "OPEN":
                logger.warning(
                    "circuit breaker opening after %d "
                    "consecutive failures",
                    self._consecutive_failures,
                )
            self._state = "OPEN"
        elif self._state == "HALF_OPEN":
            logger.warning(
                "circuit breaker reopening after "
                "failed probe"
            )
            self._state = "OPEN"
