"""
app/services/hooks.py — Lifecycle hook system for agent orchestration.

Ported from Claude Code's hook architecture (utils/hooks/sessionHooks.ts,
utils/hooks/postSamplingHooks.ts). Provides a registry for lifecycle
callbacks that fire at agent run, tool call, compaction, and error events.

Hooks run concurrently with a per-hook timeout. Failures are logged
but never propagate to callers.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine

logger = logging.getLogger("sidecar.hooks")


class HookEvent(str, Enum):
    """Lifecycle events that can trigger hooks."""

    PRE_AGENT_RUN = "pre_agent_run"
    POST_AGENT_RUN = "post_agent_run"
    PRE_TOOL_CALL = "pre_tool_call"
    POST_TOOL_CALL = "post_tool_call"
    PRE_COMPACT = "pre_compact"
    POST_COMPACT = "post_compact"
    ON_ERROR = "on_error"


@dataclass
class HookContext:
    """Context passed to every hook callback."""

    agent_name: str
    tenant_id: str
    conversation_id: str | None = None
    messages: list[Any] | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result: Any = None
    error: Exception | None = None
    timing_ms: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


# Hook callback type: async callable that receives HookContext
HookCallback = Callable[[HookContext], Coroutine[Any, Any, None]]


class HookRegistry:
    """Registry for lifecycle hooks.

    Hook failures are logged but never propagate to callers.
    Hooks run concurrently with a per-hook timeout.
    """

    def __init__(self, timeout_s: float = 5.0) -> None:
        self._hooks: dict[HookEvent, list[HookCallback]] = defaultdict(list)
        self._timeout_s = timeout_s

    def register(self, event: HookEvent, callback: HookCallback) -> None:
        """Register a hook callback for an event type."""
        self._hooks[event].append(callback)

    def clear(self, event: HookEvent | None = None) -> None:
        """Clear hooks for a specific event, or all hooks if event is None."""
        if event is None:
            self._hooks.clear()
        else:
            self._hooks.pop(event, None)

    async def fire(self, event: HookEvent, context: HookContext) -> None:
        """Fire all registered hooks for an event concurrently.

        Individual hook failures are caught and logged — they never
        propagate to the caller.
        """
        callbacks = self._hooks.get(event)
        if not callbacks:
            return

        tasks = [
            asyncio.create_task(self._run_hook(cb, context))
            for cb in callbacks
        ]

        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_hook(
        self, callback: HookCallback, context: HookContext
    ) -> None:
        """Run a single hook with timeout and error isolation."""
        try:
            await asyncio.wait_for(
                callback(context),
                timeout=self._timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "hook_timeout callback=%s event=%s timeout_s=%s",
                getattr(callback, "__name__", repr(callback)),
                context.agent_name,
                self._timeout_s,
            )
        except Exception:
            logger.exception(
                "hook_error callback=%s event=%s",
                getattr(callback, "__name__", repr(callback)),
                context.agent_name,
            )

    @property
    def hook_count(self) -> dict[HookEvent, int]:
        """Return count of registered hooks per event."""
        return {event: len(cbs) for event, cbs in self._hooks.items()}


# ---------------------------------------------------------------------------
# Module-level default registry
# ---------------------------------------------------------------------------

_default_registry: HookRegistry | None = None


def get_hook_registry() -> HookRegistry:
    """Return the module-level default hook registry."""
    global _default_registry
    if _default_registry is None:
        _default_registry = HookRegistry()
    return _default_registry


def reset_hook_registry() -> None:
    """Replace the default registry with a fresh instance."""
    global _default_registry
    _default_registry = HookRegistry()
