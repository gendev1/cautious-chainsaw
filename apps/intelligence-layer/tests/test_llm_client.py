"""Tests for LLM client — model tier definitions."""
from __future__ import annotations

from app.services.llm_client import TIERS, ModelTier


def test_tiers_has_five_entries() -> None:
    """T14: TIERS dict has 5 entries."""
    assert len(TIERS) == 5
    expected_keys = {"copilot", "batch", "analysis", "extraction", "transcription"}
    assert set(TIERS.keys()) == expected_keys


def test_copilot_tier_models() -> None:
    """Copilot tier uses claude-sonnet with gpt-4o fallback."""
    tier = TIERS["copilot"]
    assert isinstance(tier, ModelTier)
    assert "sonnet" in tier.primary
    assert tier.fallback is not None
    assert "gpt-4o" in tier.fallback


def test_analysis_tier_no_fallback() -> None:
    """Analysis tier has no fallback — accuracy critical."""
    tier = TIERS["analysis"]
    assert "opus" in tier.primary
    assert tier.fallback is None


def test_model_tier_is_frozen() -> None:
    """ModelTier is frozen — fields cannot be changed."""
    import pytest

    tier = ModelTier(primary="test:model", fallback=None)
    with pytest.raises(AttributeError):
        tier.primary = "other:model"  # type: ignore
