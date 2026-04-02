"""
app/services/progress_events.py — Streaming agent progress events.

Ported from Claude Code's StreamingToolExecutor pattern
(claudecode/services/tools/StreamingToolExecutor.ts).

Provides structured SSE event types so the frontend can show
real-time agent activity: thinking, tool invocations, results,
and completion status.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    """SSE event types for agent progress streaming."""

    # Agent lifecycle
    AGENT_START = "agent.start"
    AGENT_THINKING = "agent.thinking"
    AGENT_DONE = "agent.done"
    AGENT_ERROR = "agent.error"

    # Tool execution
    TOOL_START = "tool.start"
    TOOL_PROGRESS = "tool.progress"
    TOOL_RESULT = "tool.result"
    TOOL_ERROR = "tool.error"

    # Text streaming (existing behavior)
    TEXT_DELTA = "text.delta"
    TEXT_DONE = "text.done"

    # Compaction events
    COMPACT_START = "compact.start"
    COMPACT_DONE = "compact.done"

    # Cost tracking (ported from Claude Code cost-tracker.ts)
    COST_UPDATE = "cost.update"

    # Stream control
    DONE = "done"


@dataclass
class ProgressEvent:
    """A single progress event for SSE streaming."""

    event: EventType
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_sse(self) -> str:
        """Format as a Server-Sent Event string."""
        payload = {
            "type": self.event.value,
            "timestamp": self.timestamp,
            **self.data,
        }
        return f"event: {self.event.value}\ndata: {json.dumps(payload)}\n\n"


# ---------------------------------------------------------------------------
# Event factory functions
# ---------------------------------------------------------------------------

def agent_start(agent_name: str, prompt_preview: str = "") -> ProgressEvent:
    """Agent has started processing."""
    return ProgressEvent(
        event=EventType.AGENT_START,
        data={
            "agent": agent_name,
            "prompt_preview": prompt_preview[:100],
        },
    )


def agent_thinking(agent_name: str) -> ProgressEvent:
    """Agent is thinking / waiting for LLM response."""
    return ProgressEvent(
        event=EventType.AGENT_THINKING,
        data={"agent": agent_name},
    )


def tool_start(
    tool_name: str,
    tool_call_id: str | None = None,
    args_preview: dict[str, Any] | None = None,
) -> ProgressEvent:
    """Tool execution has started."""
    data: dict[str, Any] = {"tool": tool_name}
    if tool_call_id:
        data["tool_call_id"] = tool_call_id
    if args_preview:
        # Only include non-sensitive preview of args
        data["args_preview"] = {
            k: str(v)[:50] for k, v in args_preview.items()
        }
    return ProgressEvent(event=EventType.TOOL_START, data=data)


def tool_result(
    tool_name: str,
    tool_call_id: str | None = None,
    duration_ms: float = 0.0,
    result_preview: str = "",
) -> ProgressEvent:
    """Tool execution completed successfully."""
    return ProgressEvent(
        event=EventType.TOOL_RESULT,
        data={
            "tool": tool_name,
            "tool_call_id": tool_call_id,
            "duration_ms": round(duration_ms, 1),
            "result_preview": result_preview[:200],
        },
    )


def tool_error(
    tool_name: str,
    error: str,
    tool_call_id: str | None = None,
) -> ProgressEvent:
    """Tool execution failed."""
    return ProgressEvent(
        event=EventType.TOOL_ERROR,
        data={
            "tool": tool_name,
            "error": error[:500],
            "tool_call_id": tool_call_id,
        },
    )


def text_delta(chunk: str) -> ProgressEvent:
    """Incremental text output from the agent."""
    return ProgressEvent(
        event=EventType.TEXT_DELTA,
        data={"text": chunk},
    )


def compact_start(
    original_messages: int,
    estimated_tokens: int,
) -> ProgressEvent:
    """Conversation compaction is starting."""
    return ProgressEvent(
        event=EventType.COMPACT_START,
        data={
            "original_messages": original_messages,
            "estimated_tokens": estimated_tokens,
        },
    )


def compact_done(
    final_messages: int,
    tokens_saved: int,
) -> ProgressEvent:
    """Conversation compaction completed."""
    return ProgressEvent(
        event=EventType.COMPACT_DONE,
        data={
            "final_messages": final_messages,
            "tokens_saved": tokens_saved,
        },
    )


def agent_done(
    agent_name: str,
    total_duration_ms: float = 0.0,
    tool_calls: int = 0,
) -> ProgressEvent:
    """Agent has completed processing."""
    return ProgressEvent(
        event=EventType.AGENT_DONE,
        data={
            "agent": agent_name,
            "total_duration_ms": round(total_duration_ms, 1),
            "tool_calls": tool_calls,
        },
    )


def agent_error(
    agent_name: str,
    error: str,
) -> ProgressEvent:
    """Agent encountered an error."""
    return ProgressEvent(
        event=EventType.AGENT_ERROR,
        data={
            "agent": agent_name,
            "error": error[:500],
        },
    )


def cost_update(
    total_cost_usd: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
) -> ProgressEvent:
    """Cost summary event emitted at stream end."""
    return ProgressEvent(
        event=EventType.COST_UPDATE,
        data={
            "total_cost_usd": total_cost_usd,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
        },
    )


def done_sentinel() -> ProgressEvent:
    """Final event signaling stream end."""
    return ProgressEvent(event=EventType.DONE)
