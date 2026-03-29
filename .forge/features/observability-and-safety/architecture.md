# Architecture: Observability and Safety

## Approach A: Module-by-Module (Recommended)

Implement each section of the spec as an independent module. Group by concern: observability, middleware, safety, errors, degradation.

**New files (~22):**

Observability package:
1. `observability/__init__.py`
2. `observability/langfuse_client.py` — Singleton client
3. `observability/tracing.py` — AgentSpan, ToolSpan (v4 adapted)
4. `observability/cost.py` — MODEL_RATES, compute_request_cost
5. `observability/cost_tracking.py` — Redis daily/monthly cost counters
6. `observability/token_budget.py` — Redis token ledger
7. `observability/metrics.py` — Prometheus metric definitions
8. `observability/redaction.py` — Redaction patterns + structlog processor
9. `observability/tool_audit.py` — audited_tool decorator
10. `observability/logging.py` — structlog configuration

Middleware:
11. `middleware/tracing.py` — LangfuseTraceMiddleware (v4)
12. `middleware/token_budget.py` — enforce_token_budget dependency
13. `middleware/logging_context.py` — structlog context binding
14. `middleware/metrics.py` — PrometheusMiddleware

Agents:
15. `agents/safety.py` — Mutation tool validator
16. `agents/disclaimers.py` — Tax/compliance disclaimers
17. `agents/runner.py` — Agent runner with retry/fallback, ToolCallCounter
18. `agents/fallback.py` — LLM fallback chain

Models/Errors/Services:
19. `models/base.py` — FinancialDataMixin, StaleDataWarning
20. `errors/__init__.py` + `errors/classification.py` + `errors/classifier.py` + `errors/handlers.py`
21. `services/degradation.py` — DependencyHealth, DegradedResult
22. `routers/admin.py` — /internal/admin/cost/{tenant_id}

**Modified:**
23. `pyproject.toml` — add prometheus_client
24. `main.py` — register middleware, admin router, error handlers

## Recommendation

**Approach A** — each module is independently testable.

## Task Breakdown (recommended approach)

| Order | Files | Depends On |
|---|---|---|
| 1 | observability/ (pure modules: cost, token_budget, redaction, metrics, logging) | — |
| 2 | errors/ package | — |
| 3 | models/base.py, agents/safety.py, agents/disclaimers.py | — |
| 4 | observability/langfuse_client.py, observability/tracing.py | Langfuse v4 spike |
| 5 | middleware/ (tracing, token_budget, logging_context, metrics) | 1, 4 |
| 6 | agents/runner.py, agents/fallback.py, observability/tool_audit.py | 2, 4 |
| 7 | services/degradation.py | — |
| 8 | routers/admin.py | 1 |
| 9 | main.py, pyproject.toml updates | all above |
