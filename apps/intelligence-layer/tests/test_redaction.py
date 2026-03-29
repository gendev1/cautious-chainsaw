"""Tests for sensitive data redaction."""
from __future__ import annotations

from app.observability.redaction import redact_string, redact_value


def test_redact_ssn() -> None:
    """SSN patterns are redacted."""
    assert "[REDACTED_SSN]" in redact_string("SSN is 123-45-6789")


def test_redact_bearer_token() -> None:
    """Bearer tokens are redacted."""
    result = redact_string("Authorization: Bearer sk-abc123xyz")
    assert "sk-abc123xyz" not in result
    assert "[REDACTED]" in result


def test_redact_password_in_key_value() -> None:
    """Password key=value patterns are redacted."""
    result = redact_string("password=s3cret123")
    assert "s3cret123" not in result
    assert "[REDACTED]" in result


def test_redact_value_nested_dict() -> None:
    """Recursively redacts nested dicts."""
    data = {"user": {"ssn": "123-45-6789", "name": "Jane"}}
    result = redact_value(data)
    assert "[REDACTED_SSN]" in result["user"]["ssn"]
    assert result["user"]["name"] == "Jane"
