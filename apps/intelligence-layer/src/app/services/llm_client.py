"""
app/services/llm_client.py — Model tier definitions and fallback chain.

Model strings are read from Settings (env vars) at access time, not import
time, so .env changes take effect without code changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import httpx
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior


@dataclass(frozen=True)
class ModelTier:
    primary: str
    fallback: str | None


def _build_tiers() -> dict[str, ModelTier]:
    """Build tier map from current Settings."""
    from app.config import get_settings
    s = get_settings()
    return {
        "copilot": ModelTier(primary=s.copilot_model, fallback=s.copilot_fallback_model),
        "batch": ModelTier(primary=s.batch_model, fallback=s.batch_fallback_model),
        "analysis": ModelTier(primary=s.analysis_model, fallback=None),
        "extraction": ModelTier(primary=s.extraction_model, fallback=None),
        "transcription": ModelTier(primary="whisper:large-v3", fallback="deepgram:nova-3"),
    }


@lru_cache(maxsize=1)
def get_tiers() -> dict[str, ModelTier]:
    """Cached tier map — built once from Settings on first access."""
    return _build_tiers()


def get_model(tier: str) -> str:
    """Return the primary model string for a tier. Reads from Settings."""
    tiers = get_tiers()
    t = tiers.get(tier)
    if t is None:
        raise ValueError(f"Unknown model tier: {tier!r}. Available: {list(tiers)}")
    return t.primary


def get_fallback(tier: str) -> str | None:
    """Return the fallback model string for a tier, or None."""
    tiers = get_tiers()
    t = tiers.get(tier)
    return t.fallback if t else None


# Backwards-compatible alias — existing code that reads TIERS directly
# will get a dict that reads from Settings rather than hardcoded values.
class _LazyTiers:
    """Dict-like proxy that builds from Settings on first access."""
    def __getitem__(self, key: str) -> ModelTier:
        return get_tiers()[key]
    def get(self, key: str, default=None):
        return get_tiers().get(key, default)
    def __contains__(self, key: str) -> bool:
        return key in get_tiers()
    def __len__(self) -> int:
        return len(get_tiers())
    def items(self):
        return get_tiers().items()
    def keys(self):
        return get_tiers().keys()

TIERS = _LazyTiers()


async def run_with_fallback_chain(
    agent: Agent,
    prompt: str,
    deps,
    *,
    message_history=None,
    fallback_models: list[str] | None = None,
):
    """Run an agent with a multi-level fallback chain.

    The agent's own primary and fallback_model are tried first.
    If both fail and fallback_models is provided, each model in the
    list is tried in order.
    """
    try:
        return await agent.run(
            prompt,
            deps=deps,
            message_history=message_history,
        )
    except (UnexpectedModelBehavior, httpx.HTTPStatusError) as first_err:
        if not fallback_models:
            raise

        last_err = first_err
        for model in fallback_models:
            try:
                return await agent.run(
                    prompt,
                    deps=deps,
                    message_history=message_history,
                    model=model,
                )
            except (
                UnexpectedModelBehavior,
                httpx.HTTPStatusError,
            ) as e:
                last_err = e
                continue

        raise last_err from None
