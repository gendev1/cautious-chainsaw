"""app/observability/tracing.py — Agent and tool span helpers."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from langfuse import Langfuse


@dataclass
class AgentSpan:
    _ctx: Any
    _span: Any
    start: float

    @classmethod
    def begin(
        cls,
        langfuse: Langfuse,
        *,
        agent_name: str,
        model: str,
        input_payload: dict[str, Any],
    ) -> AgentSpan:
        ctx = langfuse._start_as_current_otel_span_with_processed_media(
            name=agent_name,
            as_type="generation",
            model=model,
            input=input_payload,
        )
        span = ctx.__enter__()
        return cls(_ctx=ctx, _span=span, start=time.monotonic())

    def end(
        self,
        *,
        output: Any = None,
        usage_input_tokens: int = 0,
        usage_output_tokens: int = 0,
        model: str | None = None,
    ) -> None:
        try:
            self._ctx.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass


@dataclass
class ToolSpan:
    _ctx: Any
    _span: Any
    start: float

    @classmethod
    def begin(
        cls,
        langfuse: Langfuse,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolSpan:
        ctx = langfuse._start_as_current_otel_span_with_processed_media(
            name=f"tool:{tool_name}",
            input=arguments,
        )
        span = ctx.__enter__()
        return cls(_ctx=ctx, _span=span, start=time.monotonic())

    def end(
        self,
        *,
        output_summary: str = "",
        error: str | None = None,
    ) -> None:
        try:
            self._ctx.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
