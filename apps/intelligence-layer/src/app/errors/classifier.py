"""
app/errors/classifier.py — Exception-to-classification mapper.
"""
from __future__ import annotations

import httpx
from pydantic import ValidationError

from app.errors.classification import (
    ClassifiedError,
    ErrorCategory,
)


class PlatformReadError(Exception):
    """Raised when a platform client read fails."""


class TranscriptionError(Exception):
    """Raised when audio transcription fails."""


class ContextTooLargeError(Exception):
    """Raised when context exceeds model window."""

    def __init__(
        self, token_count: int, limit: int
    ) -> None:
        self.token_count = token_count
        self.limit = limit
        super().__init__(
            f"Context {token_count} tokens exceeds "
            f"limit {limit}"
        )


def classify_exception(
    exc: Exception,
) -> ClassifiedError:
    """Map an exception to a ClassifiedError."""
    if isinstance(exc, PlatformReadError) or (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code >= 500
    ):
        return ClassifiedError(
            error_code=ErrorCategory.PLATFORM_READ_FAILURE,
            message=(
                "Failed to read data from the "
                "wealth platform."
            ),
            detail=str(exc),
            retryable=True,
            retry_after_seconds=5,
        )

    if isinstance(exc, TranscriptionError):
        return ClassifiedError(
            error_code=ErrorCategory.TRANSCRIPTION_FAILURE,
            message="Audio transcription failed.",
            detail=str(exc),
            retryable=True,
            retry_after_seconds=30,
        )

    if isinstance(exc, ValidationError):
        return ClassifiedError(
            error_code=ErrorCategory.VALIDATION_FAILURE,
            message=(
                "Request or response validation failed."
            ),
            detail=str(exc),
            retryable=False,
        )

    if isinstance(exc, ContextTooLargeError):
        return ClassifiedError(
            error_code=ErrorCategory.CONTEXT_TOO_LARGE,
            message=(
                f"Context ({exc.token_count} tokens) "
                f"exceeds limit ({exc.limit})."
            ),
            detail=str(exc),
            retryable=False,
        )

    return ClassifiedError(
        error_code=ErrorCategory.INTERNAL_ERROR,
        message=(
            "An internal processing error occurred."
        ),
        detail=str(exc),
        retryable=False,
    )
