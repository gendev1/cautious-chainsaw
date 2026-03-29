"""Tests for classify_platform_error function."""
from __future__ import annotations

import httpx

from app.errors import PlatformReadError, classify_platform_error


def test_classify_404() -> None:
    """404 maps to NOT_FOUND."""
    resp = httpx.Response(
        404,
        json={"detail": "Resource not found"},
        request=httpx.Request("GET", "http://test/v1/foo"),
    )
    err = classify_platform_error(resp)
    assert isinstance(err, PlatformReadError)
    assert err.error_code == "NOT_FOUND"
    assert err.status_code == 404


def test_classify_500() -> None:
    """5xx maps to PLATFORM_ERROR."""
    resp = httpx.Response(
        500,
        json={"message": "Internal server error"},
        request=httpx.Request("GET", "http://test/v1/foo"),
    )
    err = classify_platform_error(resp)
    assert err.error_code == "PLATFORM_ERROR"
    assert err.status_code == 500


def test_classify_non_json_body() -> None:
    """Non-JSON error body falls back to text."""
    resp = httpx.Response(
        502,
        text="Bad Gateway",
        request=httpx.Request("GET", "http://test/v1/foo"),
    )
    err = classify_platform_error(resp)
    assert err.error_code == "PLATFORM_ERROR"
    assert "Bad Gateway" in err.message
