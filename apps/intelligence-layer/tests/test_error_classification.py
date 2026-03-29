"""Tests for error classification system."""
from __future__ import annotations

from app.errors.classification import ErrorCategory
from app.errors.classifier import (
    PlatformReadError,
    classify_exception,
)


def test_classify_platform_read_error() -> None:
    """PlatformReadError maps to PLATFORM_READ_FAILURE."""
    exc = PlatformReadError("test failure")
    classified = classify_exception(exc)
    assert classified.error_code == ErrorCategory.PLATFORM_READ_FAILURE
    assert classified.retryable is True


def test_classify_validation_error() -> None:
    """ValidationError maps to VALIDATION_FAILURE."""
    from pydantic import ValidationError as PydanticValidationError
    try:
        from pydantic import BaseModel
        class Strict(BaseModel):
            x: int
        Strict(x="not_an_int")  # type: ignore
    except PydanticValidationError as exc:
        classified = classify_exception(exc)
        assert classified.error_code == ErrorCategory.VALIDATION_FAILURE
        assert classified.retryable is False


def test_classify_unknown_as_internal() -> None:
    """Unknown exceptions map to INTERNAL_ERROR."""
    exc = RuntimeError("something unexpected")
    classified = classify_exception(exc)
    assert classified.error_code == ErrorCategory.INTERNAL_ERROR
    assert classified.retryable is False
