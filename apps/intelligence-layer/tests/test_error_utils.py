"""Tests for shared router error utilities."""
from __future__ import annotations

from app.utils.errors import (
    ErrorCategory,
    PlatformReadHTTPError,
    SidecarErrorResponse,
    ValidationHTTPError,
)


def test_error_category_values() -> None:
    """ErrorCategory has all 4 categories."""
    assert ErrorCategory.PLATFORM_READ == "platform_read"
    assert ErrorCategory.MODEL_PROVIDER == "model_provider"
    assert ErrorCategory.VALIDATION == "validation"
    assert ErrorCategory.INTERNAL == "internal"


def test_platform_read_error_status_502() -> None:
    """PlatformReadHTTPError produces 502."""
    err = PlatformReadHTTPError("platform down", request_id="r1")
    assert err.status_code == 502


def test_validation_error_status_422() -> None:
    """ValidationHTTPError produces 422."""
    err = ValidationHTTPError("bad input", request_id="r1")
    assert err.status_code == 422


def test_sidecar_error_response_serializes() -> None:
    """SidecarErrorResponse model serializes to dict."""
    resp = SidecarErrorResponse(
        category=ErrorCategory.INTERNAL,
        code="INTERNAL_ERROR",
        message="something broke",
        request_id="r1",
    )
    data = resp.model_dump()
    assert data["category"] == "internal"
    assert data["code"] == "INTERNAL_ERROR"
