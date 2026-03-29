"""
app/agents/fallback.py — LLM fallback chain.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior

from app.services.degradation import dependency_health

logger = logging.getLogger("sidecar.agent_fallback")


async def run_with_llm_fallback(
    primary: Agent,
    fallback: Agent | None,
    prompt: str,
    *,
    deps: Any,
    agent_name: str,
) -> Any:
    """Try primary LLM, then fallback."""
    try:
        result = await primary.run(prompt, deps=deps)
        dependency_health.record_success("llm_primary")
        return result
    except Exception as primary_exc:
        dependency_health.record_failure("llm_primary")
        logger.warning(
            "primary_llm_failed agent=%s error=%s",
            agent_name,
            primary_exc,
        )

    if fallback is not None:
        try:
            result = await fallback.run(
                prompt, deps=deps
            )
            dependency_health.record_success(
                "llm_fallback"
            )
            logger.info(
                "fallback_llm_succeeded agent=%s",
                agent_name,
            )
            return result
        except Exception as fallback_exc:
            dependency_health.record_failure(
                "llm_fallback"
            )
            logger.error(
                "fallback_llm_failed agent=%s error=%s",
                agent_name,
                fallback_exc,
            )
            raise

    raise UnexpectedModelBehavior(
        "All LLM providers failed for agent: "
        + agent_name
    )
