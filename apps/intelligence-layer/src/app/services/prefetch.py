"""
app/services/prefetch.py — Context prefetch during LLM streaming.

Ported from Claude Code's memory prefetch pattern
(claudecode/QueryEngine.ts: startRelevantMemoryPrefetch).

Fires async data-loading tasks concurrently with LLM generation,
so platform data and conversation state are ready when the model
finishes generating and needs to render or execute tools.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("sidecar.prefetch")


@dataclass
class PrefetchResult:
    """Result of a prefetch operation."""

    key: str
    data: Any = None
    error: Exception | None = None
    duration_ms: float = 0.0
    hit: bool = False  # True if data was already cached


@dataclass
class PrefetchManager:
    """Manages concurrent prefetch tasks that run alongside LLM generation.

    Usage:
        prefetch = PrefetchManager()
        prefetch.schedule("clients", platform.get_advisor_clients, advisor_id=aid)
        prefetch.schedule("household", platform.get_household_summary, household_id=hid)

        # Start prefetching while LLM generates
        prefetch.start()

        # ... LLM generates response ...

        # Await results when needed (usually instant since they ran in parallel)
        clients = await prefetch.get("clients")
        household = await prefetch.get("household")
    """

    _tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    _results: dict[str, PrefetchResult] = field(default_factory=dict)
    _factories: dict[str, tuple[Any, dict]] = field(default_factory=dict)
    _started: bool = False

    def schedule(
        self,
        key: str,
        coro_fn: Any,
        **kwargs: Any,
    ) -> None:
        """Schedule a prefetch task. Must be called before start()."""
        if self._started:
            logger.warning(
                "prefetch_schedule_after_start key=%s", key
            )
            return
        self._factories[key] = (coro_fn, kwargs)

    def start(self) -> None:
        """Fire all scheduled prefetch tasks concurrently."""
        if self._started:
            return
        self._started = True

        for key, (coro_fn, kwargs) in self._factories.items():
            self._tasks[key] = asyncio.create_task(
                self._run(key, coro_fn, kwargs)
            )

        if self._tasks:
            logger.info(
                "prefetch_started",
                extra={"keys": list(self._tasks.keys())},
            )

    async def _run(
        self,
        key: str,
        coro_fn: Any,
        kwargs: dict,
    ) -> PrefetchResult:
        """Execute a single prefetch task with timing."""
        start = time.monotonic()
        try:
            data = await coro_fn(**kwargs)
            result = PrefetchResult(
                key=key,
                data=data,
                duration_ms=(time.monotonic() - start) * 1000,
            )
        except Exception as exc:
            result = PrefetchResult(
                key=key,
                error=exc,
                duration_ms=(time.monotonic() - start) * 1000,
            )
            logger.warning(
                "prefetch_failed key=%s error=%s",
                key,
                exc,
            )
        self._results[key] = result
        return result

    async def get(self, key: str, default: Any = None) -> Any:
        """Await and return prefetched data. Returns default if not found or errored."""
        if key in self._results:
            result = self._results[key]
            return result.data if result.error is None else default

        task = self._tasks.get(key)
        if task is None:
            return default

        try:
            result = await task
            return result.data if result.error is None else default
        except Exception:
            return default

    async def get_result(self, key: str) -> PrefetchResult | None:
        """Await and return the full PrefetchResult (including timing/error info)."""
        if key in self._results:
            return self._results[key]

        task = self._tasks.get(key)
        if task is None:
            return None

        try:
            return await task
        except Exception as exc:
            return PrefetchResult(key=key, error=exc)

    async def cancel_all(self) -> None:
        """Cancel any still-running prefetch tasks."""
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        # Await cancellation
        if self._tasks:
            await asyncio.gather(
                *self._tasks.values(), return_exceptions=True
            )

    def stats(self) -> dict[str, Any]:
        """Return timing stats for completed prefetches."""
        return {
            key: {
                "duration_ms": round(r.duration_ms, 1),
                "success": r.error is None,
                "hit": r.hit,
            }
            for key, r in self._results.items()
        }
