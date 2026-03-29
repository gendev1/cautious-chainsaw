"""
app/services/retry.py — Retry wrapper for batch jobs.

Not used for interactive requests. Interactive callers use
the default PlatformClient which fails fast on first error.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.errors import PlatformReadError

logger = logging.getLogger("sidecar.retry")

T = TypeVar("T")


class RetryPolicy:
    """Retry wrapper for batch jobs that can tolerate
    added latency.
    """

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay_s: float = 0.5,
        max_delay_s: float = 5.0,
        retryable_codes: frozenset[str] = frozenset(
            {
                "TIMEOUT",
                "CONNECTION_ERROR",
                "PLATFORM_ERROR",
                "RATE_LIMITED",
            }
        ),
    ) -> None:
        self.max_attempts = max_attempts
        self.base_delay_s = base_delay_s
        self.max_delay_s = max_delay_s
        self.retryable_codes = retryable_codes

    async def execute(
        self, fn: Callable[[], Awaitable[T]]
    ) -> T:
        """Execute an async callable with retry logic."""
        last_error: PlatformReadError | None = None

        for attempt in range(1, self.max_attempts + 1):
            try:
                return await fn()
            except PlatformReadError as exc:
                last_error = exc
                if (
                    exc.error_code
                    not in self.retryable_codes
                ):
                    raise

                if attempt == self.max_attempts:
                    raise

                delay = min(
                    self.base_delay_s
                    * (2 ** (attempt - 1)),
                    self.max_delay_s,
                )
                logger.info(
                    "retrying platform read "
                    "(attempt %d/%d) after %.1fs: %s",
                    attempt,
                    self.max_attempts,
                    delay,
                    exc.error_code,
                )
                await asyncio.sleep(delay)

        assert last_error is not None
        raise last_error
