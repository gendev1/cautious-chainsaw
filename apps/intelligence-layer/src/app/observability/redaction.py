"""
app/observability/redaction.py — Sensitive data redaction.
"""
from __future__ import annotations

import re
from typing import Any

REDACTION_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\b\d{3}-?\d{2}-?\d{4}\b"),
        "[REDACTED_SSN]",
    ),
    (
        re.compile(r"\b\d{8,17}\b"),
        "[REDACTED_ACCT]",
    ),
    (
        re.compile(
            r"(password|passwd|secret|token|api_key|apikey)"
            r"\s*[=:]\s*\S+",
            re.I,
        ),
        r"\1=[REDACTED]",
    ),
    (
        re.compile(
            r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.I
        ),
        "Bearer [REDACTED]",
    ),
    (
        re.compile(
            r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"
        ),
        "[REDACTED_CC]",
    ),
]


def redact_string(text: str) -> str:
    """Redact sensitive patterns from a string."""
    for pattern, replacement in REDACTION_RULES:
        text = pattern.sub(replacement, text)
    return text


def redact_value(value: Any) -> Any:
    """Recursively redact sensitive data."""
    if isinstance(value, str):
        return redact_string(value)
    if isinstance(value, dict):
        return {
            k: redact_value(v) for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return type(value)(
            redact_value(item) for item in value
        )
    return value


def redact_processor(
    logger: Any, method_name: str, event_dict: dict
) -> dict:
    """structlog processor for redaction."""
    return {
        key: redact_value(value)
        for key, value in event_dict.items()
    }
