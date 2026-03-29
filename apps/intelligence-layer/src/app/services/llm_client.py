"""
app/services/llm_client.py — Model tier definitions and fallback chain.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior


@dataclass(frozen=True)
class ModelTier:
    primary: str
    fallback: str | None


TIERS: dict[str, ModelTier] = {
    "copilot": ModelTier(
        primary="anthropic:claude-sonnet-4-6",
        fallback="openai:gpt-4o",
    ),
    "batch": ModelTier(
        primary="anthropic:claude-haiku-4-5",
        fallback="together:meta-llama/Llama-3.3-70B",
    ),
    "analysis": ModelTier(
        primary="anthropic:claude-opus-4-6",
        fallback=None,
    ),
    "extraction": ModelTier(
        primary="anthropic:claude-haiku-4-5",
        fallback=None,
    ),
    "transcription": ModelTier(
        primary="whisper:large-v3",
        fallback="deepgram:nova-3",
    ),
}


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
