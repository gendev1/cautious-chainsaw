"""
app/services/tool_orchestrator.py — Parallel tool execution for agents.

Ported from Claude Code's tool orchestration pattern
(claudecode/services/tools/toolOrchestration.ts, StreamingToolExecutor.ts).

Features:
- Read-only/mutating tool partitioning with concurrent execution
- Dynamic concurrency check (is_concurrency_safe)
- Progress event emission during tool execution
- Sibling abort on error (cancel remaining parallel tools)
- Hook integration (PRE_TOOL_CALL / POST_TOOL_CALL)
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Coroutine

from app.services.progress_events import (
    ProgressEvent,
    tool_error,
    tool_result,
    tool_start,
)

logger = logging.getLogger("sidecar.tool_orchestrator")

# All platform and search tools are read-only
READ_ONLY_TOOLS: frozenset[str] = frozenset({
    # Platform tools
    "get_household_summary",
    "get_account_summary",
    "get_client_timeline",
    "get_transfer_case",
    "get_order_projection",
    "get_report_snapshot",
    "get_advisor_clients",
    "get_constructed_portfolio",
    # Search tools
    "search_documents",
    "search_emails",
    "search_crm_notes",
    "search_meeting_transcripts",
})

# Tools that are conditionally read-only based on args
CONDITIONALLY_SAFE_TOOLS: dict[str, Callable[[dict[str, Any]], bool]] = {}

MAX_CONCURRENT_TOOLS = 8
"""Maximum number of read-only tools to run concurrently."""


def is_read_only(tool_name: str) -> bool:
    """Check if a tool is classified as read-only (safe for parallel exec)."""
    return tool_name in READ_ONLY_TOOLS


def is_concurrency_safe(
    tool_name: str, args: dict[str, Any] | None = None
) -> bool:
    """Check if a tool call is safe for concurrent execution.

    Checks static READ_ONLY_TOOLS first, then CONDITIONALLY_SAFE_TOOLS.
    """
    if tool_name in READ_ONLY_TOOLS:
        return True
    checker = CONDITIONALLY_SAFE_TOOLS.get(tool_name)
    if checker is not None and args is not None:
        return checker(args)
    return False


@dataclass
class ToolCallRequest:
    """A pending tool call to be orchestrated."""

    tool_name: str
    call_fn: Callable[..., Coroutine[Any, Any, Any]]
    args: dict[str, Any]
    tool_call_id: str | None = None


@dataclass
class ToolCallResult:
    """Result of an orchestrated tool call."""

    tool_name: str
    tool_call_id: str | None
    result: Any
    error: Exception | None = None
    duration_ms: float = 0.0
    cancelled: bool = False


@dataclass
class OrchestrationStats:
    """Statistics from a tool orchestration batch."""

    total_calls: int = 0
    parallel_calls: int = 0
    serial_calls: int = 0
    total_duration_ms: float = 0.0
    parallel_saved_ms: float = 0.0
    cancelled_calls: int = 0


async def _execute_single(request: ToolCallRequest) -> ToolCallResult:
    """Execute a single tool call with timing."""
    start = time.monotonic()
    try:
        result = await request.call_fn(**request.args)
        return ToolCallResult(
            tool_name=request.tool_name,
            tool_call_id=request.tool_call_id,
            result=result,
            duration_ms=(time.monotonic() - start) * 1000,
        )
    except asyncio.CancelledError:
        return ToolCallResult(
            tool_name=request.tool_name,
            tool_call_id=request.tool_call_id,
            result=None,
            duration_ms=(time.monotonic() - start) * 1000,
            cancelled=True,
        )
    except Exception as exc:
        return ToolCallResult(
            tool_name=request.tool_name,
            tool_call_id=request.tool_call_id,
            result=None,
            error=exc,
            duration_ms=(time.monotonic() - start) * 1000,
        )


async def orchestrate_tool_calls(
    requests: list[ToolCallRequest],
    *,
    max_concurrent: int = MAX_CONCURRENT_TOOLS,
    progress_callback: Callable[[ProgressEvent], Coroutine[Any, Any, None]] | None = None,
    hook_registry: Any | None = None,
    hook_context_base: Any | None = None,
    abort_on_error: bool = False,
) -> tuple[list[ToolCallResult], OrchestrationStats]:
    """Execute tool calls with read-only parallelism.

    Features (ported from Claude Code's toolOrchestration.ts):
    - Read-only/mutating partitioning with concurrent execution
    - Progress event emission via progress_callback
    - Sibling abort when abort_on_error=True
    - Hook integration via hook_registry
    """
    if not requests:
        return [], OrchestrationStats()

    start = time.monotonic()

    # Partition into read-only and mutating
    read_only: list[ToolCallRequest] = []
    mutating: list[ToolCallRequest] = []

    for req in requests:
        if is_concurrency_safe(req.tool_name, req.args):
            read_only.append(req)
        else:
            mutating.append(req)

    results: list[ToolCallResult] = []
    cancelled_count = 0
    parallel_individual_total_ms = 0.0

    # Execute read-only tools in parallel
    if read_only:
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _limited(req: ToolCallRequest) -> ToolCallResult:
            async with semaphore:
                # Fire PRE_TOOL_CALL hook
                if hook_registry is not None and hook_context_base is not None:
                    from app.services.hooks import HookContext, HookEvent
                    ctx = HookContext(
                        agent_name=hook_context_base.agent_name,
                        tenant_id=hook_context_base.tenant_id,
                        conversation_id=hook_context_base.conversation_id,
                        tool_name=req.tool_name,
                        tool_args=req.args,
                    )
                    await hook_registry.fire(HookEvent.PRE_TOOL_CALL, ctx)

                # Emit progress event
                if progress_callback is not None:
                    await progress_callback(
                        tool_start(req.tool_name, req.tool_call_id, req.args)
                    )

                result = await _execute_single(req)

                # Emit result/error event
                if progress_callback is not None:
                    if result.error:
                        await progress_callback(
                            tool_error(req.tool_name, str(result.error), req.tool_call_id)
                        )
                    else:
                        preview = str(result.result)[:200] if result.result else ""
                        await progress_callback(
                            tool_result(req.tool_name, req.tool_call_id, result.duration_ms, preview)
                        )

                # Fire POST_TOOL_CALL hook
                if hook_registry is not None and hook_context_base is not None:
                    from app.services.hooks import HookContext, HookEvent
                    ctx = HookContext(
                        agent_name=hook_context_base.agent_name,
                        tenant_id=hook_context_base.tenant_id,
                        conversation_id=hook_context_base.conversation_id,
                        tool_name=req.tool_name,
                        tool_result=result.result,
                        timing_ms=result.duration_ms,
                    )
                    await hook_registry.fire(HookEvent.POST_TOOL_CALL, ctx)

                return result

        if abort_on_error:
            # Create tasks so we can cancel siblings on error
            tasks = [asyncio.create_task(_limited(req)) for req in read_only]

            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_EXCEPTION
            )

            # Check if any completed task raised an error
            has_error = any(
                t.done() and not t.cancelled() and t.exception() is not None
                for t in done
            )

            # Also check if any returned result has an error
            if not has_error:
                has_error = any(
                    t.done() and not t.cancelled()
                    and t.result() is not None
                    and t.result().error is not None
                    for t in done
                )

            if has_error and pending:
                for t in pending:
                    t.cancel()
                cancelled_count = len(pending)
                # Wait for cancellations to complete
                await asyncio.gather(*pending, return_exceptions=True)

            for t in tasks:
                if t.cancelled():
                    # Find the corresponding request
                    idx = tasks.index(t)
                    results.append(ToolCallResult(
                        tool_name=read_only[idx].tool_name,
                        tool_call_id=read_only[idx].tool_call_id,
                        result=None,
                        cancelled=True,
                    ))
                elif t.exception() is not None:
                    idx = tasks.index(t)
                    exc = t.exception()
                    results.append(ToolCallResult(
                        tool_name=read_only[idx].tool_name,
                        tool_call_id=read_only[idx].tool_call_id,
                        result=None,
                        error=exc if isinstance(exc, Exception) else RuntimeError(str(exc)),
                    ))
                else:
                    results.append(t.result())
        else:
            parallel_results = await asyncio.gather(
                *[_limited(req) for req in read_only],
                return_exceptions=False,
            )
            results.extend(parallel_results)

        parallel_individual_total_ms = sum(
            r.duration_ms for r in results if not r.cancelled
        )

    # Execute mutating tools sequentially
    for req in mutating:
        if progress_callback is not None:
            await progress_callback(
                tool_start(req.tool_name, req.tool_call_id, req.args)
            )

        result = await _execute_single(req)

        if progress_callback is not None:
            if result.error:
                await progress_callback(
                    tool_error(req.tool_name, str(result.error), req.tool_call_id)
                )
            else:
                preview = str(result.result)[:200] if result.result else ""
                await progress_callback(
                    tool_result(req.tool_name, req.tool_call_id, result.duration_ms, preview)
                )

        results.append(result)

    total_ms = (time.monotonic() - start) * 1000

    parallel_wall_ms = max(
        (r.duration_ms for r in results[:len(read_only)] if not r.cancelled),
        default=0.0,
    )
    saved_ms = max(0.0, parallel_individual_total_ms - parallel_wall_ms)

    stats = OrchestrationStats(
        total_calls=len(requests),
        parallel_calls=len(read_only),
        serial_calls=len(mutating),
        total_duration_ms=total_ms,
        parallel_saved_ms=saved_ms,
        cancelled_calls=cancelled_count,
    )

    if read_only:
        logger.info(
            "tool_orchestration_complete",
            extra={
                "total_calls": stats.total_calls,
                "parallel_calls": stats.parallel_calls,
                "serial_calls": stats.serial_calls,
                "total_ms": round(stats.total_duration_ms, 1),
                "saved_ms": round(stats.parallel_saved_ms, 1),
                "cancelled": stats.cancelled_calls,
            },
        )

    return results, stats
