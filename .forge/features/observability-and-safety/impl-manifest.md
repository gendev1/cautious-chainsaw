# Implementation Manifest: Observability and Safety

## Files Created

| File | Purpose |
|---|---|
| `src/app/observability/__init__.py` | Package init |
| `src/app/observability/langfuse_client.py` | Langfuse v4 singleton |
| `src/app/observability/tracing.py` | AgentSpan, ToolSpan (v4 OTEL) |
| `src/app/observability/cost.py` | MODEL_RATES, compute_request_cost |
| `src/app/observability/cost_tracking.py` | Redis daily/monthly cost counters |
| `src/app/observability/token_budget.py` | Redis token ledger |
| `src/app/observability/metrics.py` | Prometheus metric definitions (11 metrics) |
| `src/app/observability/redaction.py` | SSN/token/password redaction + structlog processor |
| `src/app/observability/tool_audit.py` | audited_tool decorator |
| `src/app/observability/logging.py` | structlog JSON config with redaction |
| `src/app/middleware/tracing.py` | LangfuseTraceMiddleware (v4 OTEL) |
| `src/app/middleware/token_budget.py` | enforce_token_budget dependency |
| `src/app/middleware/logging_context.py` | structlog context binding middleware |
| `src/app/middleware/metrics.py` | PrometheusMiddleware |
| `src/app/agents/safety.py` | Mutation tool validator |
| `src/app/agents/disclaimers.py` | Tax/compliance disclaimer injection |
| `src/app/agents/runner.py` | Agent runner with retry/fallback |
| `src/app/agents/fallback.py` | LLM fallback chain |
| `src/app/models/base.py` | FinancialDataMixin, StaleDataWarning |
| `src/app/errors/classification.py` | 6-category ErrorCategory enum |
| `src/app/errors/classifier.py` | Exception-to-classification mapper |
| `src/app/errors/handlers.py` | Global exception handler |
| `src/app/services/degradation.py` | DependencyHealth, DegradedResult |
| `src/app/routers/admin.py` | /internal/admin/cost/{tenant_id} |

## Files Modified

| File | Change |
|---|---|
| `src/app/errors/__init__.py` | Moved content from errors.py into package __init__ |
| `src/app/routers/health.py` | Added /metrics endpoint (Prometheus) |
| `src/app/main.py` | Added admin router |
| `src/app/config.py` | Added token_budget_redis_prefix, default_daily_token_limit |
| `pyproject.toml` | Added prometheus-client dependency |

## Patterns Followed

- Langfuse v4 OTEL spans throughout (no v2/v3 .trace/.generation)
- Decimal arithmetic for cost computation
- Redis atomic increments for token budget and cost tracking
- structlog with redaction processor for all log output
- Static mutation tool validation at startup
- Tax/compliance keyword regex for disclaimer injection
- 6-category error classification with HTTP status mapping

## Test Results

```
168 passed in 3.09s
Ruff: All checks passed!
```
