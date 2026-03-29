"""
app/jobs/errors.py — Job failure classification and retry policy.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any

import httpx

logger = logging.getLogger("sidecar.jobs.errors")


class FailureCategory(str, Enum):
    """Failure categories that determine retry behavior."""

    PLATFORM_READ = "platform_read"
    MODEL_PROVIDER = "model_provider"
    VALIDATION = "validation"
    INTERNAL = "internal"


def classify_error(exc: Exception) -> FailureCategory:
    """Classify an exception into a failure category."""
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code >= 500:
            return FailureCategory.PLATFORM_READ
        if exc.response.status_code == 429:
            return FailureCategory.MODEL_PROVIDER
        if exc.response.status_code in (401, 403):
            return FailureCategory.PLATFORM_READ
        return FailureCategory.VALIDATION

    if isinstance(
        exc, httpx.ConnectError | httpx.TimeoutException
    ):
        return FailureCategory.PLATFORM_READ

    if isinstance(exc, ValueError | TypeError):
        return FailureCategory.VALIDATION

    error_name = type(exc).__name__
    if (
        "model" in error_name.lower()
        or "provider" in error_name.lower()
    ):
        return FailureCategory.MODEL_PROVIDER
    if (
        "rate" in str(exc).lower()
        or "quota" in str(exc).lower()
    ):
        return FailureCategory.MODEL_PROVIDER

    return FailureCategory.INTERNAL


RETRY_POLICY: dict[FailureCategory, dict[str, Any]] = {
    FailureCategory.PLATFORM_READ: {
        "max_retries": 3,
        "base_delay_seconds": 5,
        "backoff_factor": 2,
        "retry": True,
    },
    FailureCategory.MODEL_PROVIDER: {
        "max_retries": 3,
        "base_delay_seconds": 10,
        "backoff_factor": 3,
        "retry": True,
    },
    FailureCategory.VALIDATION: {
        "max_retries": 0,
        "base_delay_seconds": 0,
        "backoff_factor": 1,
        "retry": False,
    },
    FailureCategory.INTERNAL: {
        "max_retries": 1,
        "base_delay_seconds": 30,
        "backoff_factor": 1,
        "retry": True,
    },
}


def compute_retry_delay(
    category: FailureCategory, attempt: int
) -> float | None:
    """Compute retry delay. Returns None if not retryable."""
    policy = RETRY_POLICY[category]
    if (
        not policy["retry"]
        or attempt >= policy["max_retries"]
    ):
        return None
    return policy["base_delay_seconds"] * (
        policy["backoff_factor"] ** attempt
    )
