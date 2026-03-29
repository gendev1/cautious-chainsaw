"""
app/jobs/observability.py — Job-level Langfuse telemetry.

Uses Langfuse v4 OTEL-based API.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from langfuse import Langfuse

logger = logging.getLogger("sidecar.jobs.observability")


@dataclass
class JobMetrics:
    """Accumulated metrics for a single job execution."""

    job_name: str
    tenant_id: str
    actor_id: str
    started_at: float = field(
        default_factory=time.monotonic
    )
    ended_at: float | None = None
    status: str = "running"
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_calls: int = 0
    platform_reads: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    error: str | None = None
    error_category: str | None = None

    @property
    def duration_seconds(self) -> float:
        end = self.ended_at or time.monotonic()
        return end - self.started_at


class JobTracer:
    """Wraps Langfuse trace management for a single
    job execution using Langfuse v4 OTEL spans.
    """

    def __init__(
        self,
        langfuse: Langfuse,
        job_name: str,
        tenant_id: str,
        actor_id: str,
        extra_metadata: dict | None = None,
    ) -> None:
        self._langfuse = langfuse
        self.metrics = JobMetrics(
            job_name=job_name,
            tenant_id=tenant_id,
            actor_id=actor_id,
        )
        self._trace_id = langfuse.create_trace_id()
        self._metadata = {
            "tenant_id": tenant_id,
            "actor_id": actor_id,
            **(extra_metadata or {}),
        }
        self._span_ctx = (
            langfuse._start_as_current_otel_span_with_processed_media(
                name=job_name,
                metadata=self._metadata,
            )
        )
        self._span = self._span_ctx.__enter__()

    def start_generation(
        self,
        name: str,
        model: str | None = None,
        input_data: Any = None,
    ) -> Any:
        self.metrics.model_calls += 1
        gen_ctx = (
            self._langfuse._start_as_current_otel_span_with_processed_media(
                name=name,
                as_type="generation",
                model=model,
                input=input_data,
            )
        )
        span = gen_ctx.__enter__()
        return _GenerationHandle(gen_ctx, span)

    def end_generation(
        self,
        generation: Any,
        output: Any = None,
        token_usage: dict | None = None,
    ) -> None:
        if token_usage:
            self.metrics.prompt_tokens += token_usage.get(
                "prompt_tokens", 0
            )
            self.metrics.completion_tokens += (
                token_usage.get("completion_tokens", 0)
            )
            self.metrics.total_tokens += token_usage.get(
                "total_tokens", 0
            )
        if isinstance(generation, _GenerationHandle):
            generation.close()

    def record_platform_read(self) -> None:
        self.metrics.platform_reads += 1

    def record_cache_hit(self) -> None:
        self.metrics.cache_hits += 1

    def record_cache_miss(self) -> None:
        self.metrics.cache_misses += 1

    def start_span(self, name: str, **kwargs: Any) -> Any:
        ctx = self._langfuse._start_as_current_otel_span_with_processed_media(
            name=name, **kwargs
        )
        span = ctx.__enter__()
        return _SpanHandle(ctx, span)

    def complete(self, output: Any = None) -> None:
        self.metrics.ended_at = time.monotonic()
        self.metrics.status = "success"
        try:
            self._span_ctx.__exit__(None, None, None)
        except Exception:
            pass

    def fail(
        self,
        error: Exception,
        category: str | None = None,
    ) -> None:
        self.metrics.ended_at = time.monotonic()
        self.metrics.status = "error"
        self.metrics.error = str(error)
        self.metrics.error_category = category
        try:
            self._span_ctx.__exit__(
                type(error), error, error.__traceback__
            )
        except Exception:
            pass


class _GenerationHandle:
    """Wrapper to manage generation span lifecycle."""

    def __init__(self, ctx: Any, span: Any) -> None:
        self._ctx = ctx
        self._span = span

    def close(self) -> None:
        try:
            self._ctx.__exit__(None, None, None)
        except Exception:
            pass


class _SpanHandle:
    """Wrapper to manage span lifecycle."""

    def __init__(self, ctx: Any, span: Any) -> None:
        self._ctx = ctx
        self._span = span

    def end(self, **kwargs: Any) -> None:
        try:
            self._ctx.__exit__(None, None, None)
        except Exception:
            pass
