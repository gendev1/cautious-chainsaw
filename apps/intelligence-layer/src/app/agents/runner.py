"""
app/agents/runner.py — Agent runner with retry, fallback,
and tool call tracking.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior

logger = logging.getLogger("sidecar.agent_runner")

MAX_TOOL_CALLS_PER_TURN = 3
MAX_RETRIES = 1


@dataclass
class ToolCallCounter:
    """Tracks tool calls within a single agent turn."""

    count: int = 0
    limit: int = MAX_TOOL_CALLS_PER_TURN
    invocations: list[str] = field(
        default_factory=list
    )

    def record(self, tool_name: str) -> None:
        self.count += 1
        self.invocations.append(tool_name)

    @property
    def budget_exhausted(self) -> bool:
        return self.count >= self.limit


class ToolCallLimitExceeded(Exception):
    def __init__(
        self, limit: int, invocations: list[str]
    ) -> None:
        self.limit = limit
        self.invocations = invocations
        super().__init__(
            f"Tool call limit ({limit}) exceeded. "
            f"Invocations: {invocations}"
        )


class AgentOutputError(Exception):
    def __init__(
        self,
        agent_name: str,
        errors: list[dict],
    ) -> None:
        self.agent_name = agent_name
        self.errors = errors
        super().__init__(
            f"Agent '{agent_name}' produced invalid "
            "output"
        )


async def run_agent_safe(
    agent: Agent,
    prompt: str,
    *,
    deps: Any,
    agent_name: str,
    fallback_agent: Agent | None = None,
) -> Any:
    """Run agent with retry and fallback on validation
    failure.
    """
    last_error: Exception | None = None

    for attempt in range(1 + MAX_RETRIES):
        try:
            return await agent.run(prompt, deps=deps)
        except (
            ValidationError,
            UnexpectedModelBehavior,
        ) as exc:
            last_error = exc
            logger.warning(
                "agent_output_validation_failed "
                "agent=%s attempt=%d error=%s",
                agent_name,
                attempt + 1,
                exc,
            )

    if fallback_agent is not None:
        try:
            logger.info(
                "agent_fallback_attempt agent=%s",
                agent_name,
            )
            return await fallback_agent.run(
                prompt, deps=deps
            )
        except (
            ValidationError,
            UnexpectedModelBehavior,
        ) as exc:
            last_error = exc
            logger.error(
                "agent_fallback_also_failed "
                "agent=%s error=%s",
                agent_name,
                exc,
            )

    error_details: list[dict] = []
    if isinstance(last_error, ValidationError):
        error_details = list(last_error.errors())

    raise AgentOutputError(
        agent_name=agent_name, errors=error_details
    )
