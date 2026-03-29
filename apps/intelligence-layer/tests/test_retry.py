"""Tests for RetryPolicy — exponential backoff for batch jobs."""
from __future__ import annotations

import pytest

from app.errors import PlatformReadError
from app.services.retry import RetryPolicy


@pytest.mark.asyncio
async def test_returns_on_first_success() -> None:
    """Successful call returns immediately."""
    retry = RetryPolicy(max_attempts=3)
    result = await retry.execute(lambda: _async_value("ok"))
    assert result == "ok"


@pytest.mark.asyncio
async def test_retries_retryable_errors() -> None:
    """Retries TIMEOUT errors up to max_attempts."""
    call_count = 0

    async def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise PlatformReadError(
                status_code=0,
                error_code="TIMEOUT",
                message="timeout",
            )
        return "recovered"

    retry = RetryPolicy(
        max_attempts=3,
        base_delay_s=0.01,
    )
    result = await retry.execute(flaky)
    assert result == "recovered"
    assert call_count == 3


@pytest.mark.asyncio
async def test_does_not_retry_non_retryable() -> None:
    """Non-retryable errors propagate immediately."""
    retry = RetryPolicy(max_attempts=3)

    async def forbidden():
        raise PlatformReadError(
            status_code=403,
            error_code="FORBIDDEN",
            message="access denied",
        )

    with pytest.raises(PlatformReadError) as exc_info:
        await retry.execute(forbidden)
    assert exc_info.value.error_code == "FORBIDDEN"


@pytest.mark.asyncio
async def test_raises_after_exhausting_attempts() -> None:
    """Raises last error after max attempts exhausted."""
    retry = RetryPolicy(
        max_attempts=2,
        base_delay_s=0.01,
    )

    async def always_fail():
        raise PlatformReadError(
            status_code=0,
            error_code="TIMEOUT",
            message="timeout",
        )

    with pytest.raises(PlatformReadError):
        await retry.execute(always_fail)


async def _async_value(v):
    return v
