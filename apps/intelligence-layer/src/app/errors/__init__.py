"""
app/errors.py — Sidecar error hierarchy and classification.
"""
from __future__ import annotations

from typing import Any, Literal

import httpx

ErrorCategory = Literal[
    "platform_read",
    "model_provider",
    "validation",
    "transcription",
    "internal",
]


class SidecarError(Exception):
    """Base error for all sidecar failures."""

    def __init__(
        self,
        *,
        error_code: str,
        category: ErrorCategory,
        status_code: int = 500,
        message: str,
        detail: Any = None,
    ) -> None:
        self.error_code = error_code
        self.category = category
        self.status_code = status_code
        self.message = message
        self.detail = detail
        super().__init__(message)


# ---------------------------------------------------------------------------
# Platform read failures
# ---------------------------------------------------------------------------

class PlatformReadError(SidecarError):
    """Raised when a platform API read fails.

    Attributes:
        status_code: HTTP status code (0 for connection/timeout errors).
        error_code: Classified error code string.
        message: Human-readable description.
    """

    def __init__(
        self,
        status_code: int,
        error_code: str,
        message: str,
    ) -> None:
        super().__init__(
            error_code=error_code,
            category="platform_read",
            status_code=status_code,
            message=message,
        )


class PlatformTimeoutError(SidecarError):
    """Platform API read timed out."""

    def __init__(self, resource: str) -> None:
        super().__init__(
            error_code="PLATFORM_READ_TIMEOUT",
            category="platform_read",
            status_code=504,
            message=f"Platform API timed out reading {resource}.",
            detail={"resource": resource},
        )


# ---------------------------------------------------------------------------
# Model provider failures
# ---------------------------------------------------------------------------

class ModelProviderError(SidecarError):
    """An LLM, embedding, or reranking model provider failed."""

    def __init__(self, provider: str, detail: Any = None) -> None:
        super().__init__(
            error_code="MODEL_PROVIDER_FAILED",
            category="model_provider",
            status_code=502,
            message=f"Model provider '{provider}' returned an error.",
            detail={"provider": provider, **(detail or {})},
        )


class ModelProviderRateLimitError(SidecarError):
    """Model provider rate-limited the request."""

    def __init__(self, provider: str, retry_after: float | None = None) -> None:
        super().__init__(
            error_code="MODEL_PROVIDER_RATE_LIMITED",
            category="model_provider",
            status_code=429,
            message=f"Model provider '{provider}' rate-limited the request.",
            detail={"provider": provider, "retry_after_s": retry_after},
        )


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------

class ValidationError(SidecarError):
    """Request payload or header validation failed."""

    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__(
            error_code="VALIDATION_FAILED",
            category="validation",
            status_code=422,
            message=message,
            detail=detail,
        )


class ScopeViolationError(SidecarError):
    """The actor attempted to access a resource outside their access scope."""

    def __init__(self, resource_type: str, resource_id: str) -> None:
        super().__init__(
            error_code="SCOPE_VIOLATION",
            category="validation",
            status_code=403,
            message=(
                f"Access denied: {resource_type} '{resource_id}'"
                " is outside the provided access scope."
            ),
            detail={"resource_type": resource_type, "resource_id": resource_id},
        )


# ---------------------------------------------------------------------------
# Transcription failures
# ---------------------------------------------------------------------------

class TranscriptionError(SidecarError):
    """Audio transcription failed."""

    def __init__(self, provider: str, detail: Any = None) -> None:
        super().__init__(
            error_code="TRANSCRIPTION_FAILED",
            category="transcription",
            status_code=502,
            message=f"Transcription via '{provider}' failed.",
            detail={"provider": provider, **(detail or {})},
        )


class TranscriptionTooLongError(SidecarError):
    """Audio exceeds the maximum allowed duration."""

    def __init__(self, duration_s: float, max_s: int) -> None:
        super().__init__(
            error_code="TRANSCRIPTION_TOO_LONG",
            category="transcription",
            status_code=422,
            message=f"Audio duration ({duration_s}s) exceeds maximum ({max_s}s).",
            detail={"duration_s": duration_s, "max_s": max_s},
        )


# ---------------------------------------------------------------------------
# Internal failures
# ---------------------------------------------------------------------------

class InternalError(SidecarError):
    """Catch-all for unexpected internal failures."""

    def __init__(self, message: str = "An internal error occurred.", detail: Any = None) -> None:
        super().__init__(
            error_code="INTERNAL_ERROR",
            category="internal",
            status_code=500,
            message=message,
            detail=detail,
        )


class RedisUnavailableError(SidecarError):
    """Redis is unreachable."""

    def __init__(self) -> None:
        super().__init__(
            error_code="REDIS_UNAVAILABLE",
            category="internal",
            status_code=503,
            message="Redis is unavailable.",
        )


class VectorStoreUnavailableError(SidecarError):
    """Vector store is unreachable."""

    def __init__(self) -> None:
        super().__init__(
            error_code="VECTOR_STORE_UNAVAILABLE",
            category="internal",
            status_code=503,
            message="Vector store is unavailable.",
        )


# ---------------------------------------------------------------------------
# Error classification for platform HTTP responses
# ---------------------------------------------------------------------------


def classify_platform_error(
    response: httpx.Response,
) -> PlatformReadError:
    """Classify an HTTP error response into a PlatformReadError."""
    status = response.status_code

    try:
        body = response.json()
        detail = body.get(
            "detail", body.get("message", response.text[:500])
        )
    except Exception:
        detail = response.text[:500]

    error_map: dict[int, str] = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMITED",
    }

    if status in error_map:
        code = error_map[status]
    elif 400 <= status < 500:
        code = "CLIENT_ERROR"
    else:
        code = "PLATFORM_ERROR"

    return PlatformReadError(
        status_code=status,
        error_code=code,
        message=str(detail),
    )
