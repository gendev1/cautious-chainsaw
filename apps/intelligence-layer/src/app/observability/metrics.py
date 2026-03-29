"""app/observability/metrics.py — Prometheus metrics."""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

REQUEST_LATENCY = Histogram(
    "sidecar_request_latency_seconds",
    "HTTP request latency",
    labelnames=["method", "endpoint", "status_code"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0],
)

REQUEST_COUNT = Counter(
    "sidecar_request_total",
    "Total HTTP requests",
    labelnames=["method", "endpoint", "status_code"],
)

AGENT_LATENCY = Histogram(
    "sidecar_agent_latency_seconds",
    "Agent call latency",
    labelnames=["agent", "model", "tenant_id"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0],
)

AGENT_TOKENS_INPUT = Counter(
    "sidecar_agent_input_tokens_total",
    "Input tokens",
    labelnames=["agent", "model", "tenant_id"],
)

AGENT_TOKENS_OUTPUT = Counter(
    "sidecar_agent_output_tokens_total",
    "Output tokens",
    labelnames=["agent", "model", "tenant_id"],
)

TOOL_CALL_COUNT = Counter(
    "sidecar_tool_calls_total",
    "Tool invocations",
    labelnames=["tool", "agent", "tenant_id"],
)

TOOL_CALL_LATENCY = Histogram(
    "sidecar_tool_call_latency_seconds",
    "Tool call latency",
    labelnames=["tool"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

CACHE_HIT = Counter(
    "sidecar_cache_hit_total",
    "Cache hits",
    labelnames=["cache_name"],
)

CACHE_MISS = Counter(
    "sidecar_cache_miss_total",
    "Cache misses",
    labelnames=["cache_name"],
)

ERROR_COUNT = Counter(
    "sidecar_errors_total",
    "Errors by classification",
    labelnames=["error_code", "agent", "endpoint"],
)

TOKEN_BUDGET_REMAINING = Gauge(
    "sidecar_token_budget_remaining",
    "Remaining token budget",
    labelnames=["tenant_id"],
)
