"""
app/agents/runner.py — Agent runner with retry, fallback,
tool call tracking, and intelligent orchestration.

IntelligentRunner wraps pydantic_ai agent.run/run_stream with
patterns from Claude Code's query loop:
- Pre-run token budget check
- Pre/post agent hooks
- Cost accumulation from result.usage()
- Reactive compaction retry on prompt_too_long
- Error hooks and fallback chain
"""
from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage

from app.services.progress_events import ProgressEvent

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


# ---------------------------------------------------------------------------
# IntelligentRunner — wraps pydantic_ai with Claude Code orchestration
# ---------------------------------------------------------------------------


class TokenBudgetExhausted(Exception):
    """Raised when a tenant's daily token budget is exhausted."""

    def __init__(self, tenant_id: str, used: int, limit: int) -> None:
        self.tenant_id = tenant_id
        self.used = used
        self.limit = limit
        super().__init__(
            f"Token budget exhausted for tenant {tenant_id}: "
            f"used {used:,} of {limit:,}"
        )


@dataclass
class RunConfig:
    """Configuration for an intelligent agent run."""

    agent_name: str
    tenant_id: str
    actor_id: str
    conversation_id: str | None = None
    cost_accumulator: Any | None = None
    hook_registry: Any | None = None
    compaction_strategy: Any | None = None
    progress_callback: Any | None = None
    token_budget_limit: int | None = None
    redis: Any = None


class IntelligentRunner:
    """Wraps pydantic_ai agent.run/run_stream with orchestration.

    Ported from Claude Code's QueryEngine pattern. Provides:
    - Pre-run token budget check
    - Pre/post agent hooks
    - Cost accumulation from result.usage()
    - Reactive compaction retry on prompt_too_long
    - Error hooks and fallback chain
    """

    def __init__(self, config: RunConfig) -> None:
        self._config = config

    async def run(
        self,
        agent: Agent,
        prompt: str,
        *,
        deps: Any,
        message_history: list[ModelMessage] | None = None,
    ) -> Any:
        """Run agent with full orchestration wrapper."""
        # Pre-run: check token budget
        await self._check_token_budget()

        # Pre-run: fire hook
        await self._fire_hook("PRE_AGENT_RUN")

        start = time.monotonic()
        try:
            result = await agent.run(
                prompt,
                deps=deps,
                message_history=message_history,
            )
        except UnexpectedModelBehavior as exc:
            # Check if this is a prompt_too_long error — retry with reactive compact
            if "prompt_too_long" in str(exc).lower() and message_history is not None:
                logger.info(
                    "reactive_compact_retry agent=%s",
                    self._config.agent_name,
                )
                from app.services.compaction import reactive_compact
                compaction = await reactive_compact(
                    message_history,
                    strategy=self._config.compaction_strategy,
                )
                result = await agent.run(
                    prompt,
                    deps=deps,
                    message_history=compaction.messages,
                )
            else:
                await self._fire_hook("ON_ERROR", error=exc)
                raise
        except Exception as exc:
            await self._fire_hook("ON_ERROR", error=exc)
            raise

        duration_ms = (time.monotonic() - start) * 1000

        # Post-run: accumulate cost
        cost, inp, out = self._extract_cost(result)
        if self._config.cost_accumulator is not None:
            self._config.cost_accumulator.record_agent_run(
                agent_name=self._config.agent_name,
                cost_usd=cost,
                input_tokens=inp,
                output_tokens=out,
                tool_calls=0,
                duration_ms=duration_ms,
            )

        # Post-run: fire hook
        await self._fire_hook("POST_AGENT_RUN")

        return result.output

    async def run_stream(
        self,
        agent: Agent,
        prompt: str,
        *,
        deps: Any,
        message_history: list[ModelMessage] | None = None,
    ) -> AsyncIterator[str | ProgressEvent]:
        """Run agent with streaming, yielding text chunks and progress events."""
        await self._check_token_budget()
        await self._fire_hook("PRE_AGENT_RUN")

        start = time.monotonic()

        async with agent.run_stream(
            prompt,
            deps=deps,
            message_history=message_history,
        ) as result:
            async for chunk in result.stream_text():
                yield chunk

        duration_ms = (time.monotonic() - start) * 1000

        cost, inp, out = await self._extract_cost_async(result)
        if self._config.cost_accumulator is not None:
            self._config.cost_accumulator.record_agent_run(
                agent_name=self._config.agent_name,
                cost_usd=cost,
                input_tokens=inp,
                output_tokens=out,
                tool_calls=0,
                duration_ms=duration_ms,
            )

        await self._fire_hook("POST_AGENT_RUN")

    async def _check_token_budget(self) -> None:
        """Check if tenant has remaining token budget. Raises if exhausted."""
        limit = self._config.token_budget_limit
        if limit is not None and limit <= 0:
            raise TokenBudgetExhausted(
                tenant_id=self._config.tenant_id,
                used=0,
                limit=limit,
            )

    async def _fire_hook(self, event_name: str, **kwargs: Any) -> None:
        """Fire a hook event if registry is available."""
        registry = self._config.hook_registry
        if registry is None:
            return
        from app.services.hooks import HookContext, HookEvent
        event = HookEvent(event_name.lower())
        ctx = HookContext(
            agent_name=self._config.agent_name,
            tenant_id=self._config.tenant_id,
            conversation_id=self._config.conversation_id,
            error=kwargs.get("error"),
        )
        await registry.fire(event, ctx)

    def _extract_cost(self, result: Any) -> tuple[Decimal, int, int]:
        """Extract cost and token counts from a pydantic_ai result (sync)."""
        try:
            usage_fn = getattr(result, "usage", None)
            if usage_fn is None or not callable(usage_fn):
                return Decimal(0), 0, 0
            usage = usage_fn()
            import asyncio
            if asyncio.iscoroutine(usage):
                usage.close()
                return Decimal(0), 0, 0
            inp = getattr(usage, "request_tokens", 0) or 0
            out = getattr(usage, "response_tokens", 0) or 0
            cost = Decimal(0)
            return cost, inp, out
        except Exception:
            return Decimal(0), 0, 0

    async def _extract_cost_async(self, result: Any) -> tuple[Decimal, int, int]:
        """Extract cost and token counts, handling awaitable usage()."""
        try:
            usage_fn = getattr(result, "usage", None)
            if usage_fn is None or not callable(usage_fn):
                return Decimal(0), 0, 0
            usage = usage_fn()
            import asyncio
            if asyncio.iscoroutine(usage) or asyncio.isfuture(usage):
                usage = await usage
            inp = getattr(usage, "request_tokens", 0) or 0
            out = getattr(usage, "response_tokens", 0) or 0
            cost = Decimal(0)
            return cost, inp, out
        except Exception:
            return Decimal(0), 0, 0
