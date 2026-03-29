"""Tests for error hierarchy and error response envelope."""
from __future__ import annotations

from app.errors import (
    InternalError,
    ModelProviderError,
    ModelProviderRateLimitError,
    PlatformReadError,
    PlatformTimeoutError,
    RedisUnavailableError,
    ScopeViolationError,
    SidecarError,
    TranscriptionError,
    TranscriptionTooLongError,
    ValidationError,
    VectorStoreUnavailableError,
)


def test_sidecar_error_attributes() -> None:
    """T10: SidecarError subclasses carry correct status_code and error_code."""
    cases = [
        (
            PlatformReadError(502, "PLATFORM_READ_FAILED", "Failed to read household"),
            502, "PLATFORM_READ_FAILED", "platform_read",
        ),
        (PlatformTimeoutError("household"), 504, "PLATFORM_READ_TIMEOUT", "platform_read"),
        (ModelProviderError("anthropic"), 502, "MODEL_PROVIDER_FAILED", "model_provider"),
        (
            ModelProviderRateLimitError("anthropic"),
            429, "MODEL_PROVIDER_RATE_LIMITED", "model_provider",
        ),
        (ValidationError("bad input"), 422, "VALIDATION_FAILED", "validation"),
        (ScopeViolationError("household", "h1"), 403, "SCOPE_VIOLATION", "validation"),
        (TranscriptionError("whisper"), 502, "TRANSCRIPTION_FAILED", "transcription"),
        (TranscriptionTooLongError(8000.0, 7200), 422, "TRANSCRIPTION_TOO_LONG", "transcription"),
        (InternalError(), 500, "INTERNAL_ERROR", "internal"),
        (RedisUnavailableError(), 503, "REDIS_UNAVAILABLE", "internal"),
        (VectorStoreUnavailableError(), 503, "VECTOR_STORE_UNAVAILABLE", "internal"),
    ]
    for err, expected_status, expected_code, expected_category in cases:
        assert err.status_code == expected_status, f"{err.__class__.__name__}: status_code"
        assert err.error_code == expected_code, f"{err.__class__.__name__}: error_code"
        assert err.category == expected_category, f"{err.__class__.__name__}: category"


def test_scope_violation_returns_403() -> None:
    """T12: ScopeViolationError carries 403 and correct detail."""
    err = ScopeViolationError("household", "h_123")
    assert err.status_code == 403
    assert err.error_code == "SCOPE_VIOLATION"
    assert err.detail["resource_type"] == "household"
    assert err.detail["resource_id"] == "h_123"


def test_all_errors_inherit_sidecar_error() -> None:
    """All error subclasses inherit from SidecarError."""
    classes = [
        PlatformReadError, PlatformTimeoutError,
        ModelProviderError, ModelProviderRateLimitError,
        ValidationError, ScopeViolationError,
        TranscriptionError, TranscriptionTooLongError,
        InternalError, RedisUnavailableError, VectorStoreUnavailableError,
    ]
    for cls in classes:
        assert issubclass(cls, SidecarError), f"{cls.__name__} must inherit SidecarError"
