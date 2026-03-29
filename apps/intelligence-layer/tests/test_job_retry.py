"""Tests for with_retry_policy decorator."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from arq import Retry

from app.jobs.retry import with_retry_policy


@pytest.mark.asyncio
async def test_passthrough_on_success() -> None:
    """Successful job returns result without retry."""
    async def my_job(ctx, arg1):
        return {"status": "ok", "arg1": arg1}

    wrapped = with_retry_policy(my_job)
    result = await wrapped({"job_try": 1, "job_id": "test"}, "hello")
    assert result == {"status": "ok", "arg1": "hello"}


@pytest.mark.asyncio
async def test_raises_retry_on_retryable_error() -> None:
    """Retryable error triggers arq Retry."""
    import httpx

    async def failing_job(ctx):
        raise httpx.ReadTimeout("timed out")

    wrapped = with_retry_policy(failing_job)
    with pytest.raises(Retry):
        await wrapped({"job_try": 1, "job_id": "test"})


@pytest.mark.asyncio
async def test_dead_letter_on_non_retryable() -> None:
    """Non-retryable error records dead letter and re-raises."""
    mock_redis = AsyncMock()

    async def bad_job(ctx):
        raise ValueError("invalid input")

    wrapped = with_retry_policy(bad_job)
    with pytest.raises(ValueError):
        await wrapped({"job_try": 1, "job_id": "test", "redis": mock_redis})
