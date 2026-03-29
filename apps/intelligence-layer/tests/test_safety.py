"""Tests for safety guardrails."""
from __future__ import annotations

import pytest

from app.agents.disclaimers import check_disclaimer
from app.agents.safety import validate_tool_safety


def test_rejects_mutation_prefix() -> None:
    """Tools with create_ prefix are rejected."""
    with pytest.raises(ValueError, match="mutation"):
        validate_tool_safety("create_order")


def test_allows_get_prefix() -> None:
    """Tools with get_ prefix pass validation."""
    validate_tool_safety("get_household_summary")  # should not raise


def test_disclaimer_detects_tax_keywords() -> None:
    """Tax keywords trigger disclaimer."""
    result = check_disclaimer("Consider tax-loss harvesting on the capital gains.")
    assert result.required is True
    assert result.text is not None


def test_disclaimer_not_required_for_general() -> None:
    """General text does not trigger disclaimer."""
    result = check_disclaimer("The weather is nice today.")
    assert result.required is False
