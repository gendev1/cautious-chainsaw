"""Tests for job failure classification and retry policy."""
from __future__ import annotations

import httpx

from app.jobs.errors import (
    FailureCategory,
    classify_error,
    compute_retry_delay,
)


def test_classify_http_500_as_platform_read() -> None:
    """HTTP 500 from platform maps to PLATFORM_READ."""
    request = httpx.Request("GET", "http://test/v1/foo")
    response = httpx.Response(500, request=request)
    exc = httpx.HTTPStatusError("server error", request=request, response=response)
    assert classify_error(exc) == FailureCategory.PLATFORM_READ


def test_classify_timeout_as_platform_read() -> None:
    """Timeout maps to PLATFORM_READ."""
    exc = httpx.ReadTimeout("timed out")
    assert classify_error(exc) == FailureCategory.PLATFORM_READ


def test_classify_rate_limit_as_model_provider() -> None:
    """HTTP 429 maps to MODEL_PROVIDER."""
    request = httpx.Request("POST", "http://api.openai.com/v1/chat")
    response = httpx.Response(429, request=request)
    exc = httpx.HTTPStatusError("rate limited", request=request, response=response)
    assert classify_error(exc) == FailureCategory.MODEL_PROVIDER


def test_classify_value_error_as_validation() -> None:
    """ValueError maps to VALIDATION."""
    exc = ValueError("bad input")
    assert classify_error(exc) == FailureCategory.VALIDATION


def test_compute_retry_delay_validation_returns_none() -> None:
    """Validation errors are not retryable."""
    assert compute_retry_delay(FailureCategory.VALIDATION, 0) is None


def test_compute_retry_delay_platform_read_backoff() -> None:
    """Platform read uses exponential backoff: 5s, 10s, 20s."""
    d0 = compute_retry_delay(FailureCategory.PLATFORM_READ, 0)
    d1 = compute_retry_delay(FailureCategory.PLATFORM_READ, 1)
    assert d0 == 5.0
    assert d1 == 10.0
    # Attempt 3 exceeds max_retries, returns None
    assert compute_retry_delay(FailureCategory.PLATFORM_READ, 3) is None
