# Discovery: Observability and Safety

## Requirements

1. **Langfuse integration** — Singleton client (observability/langfuse_client.py), per-request trace middleware (middleware/tracing.py), per-agent span helpers (observability/tracing.py: AgentSpan, ToolSpan)
2. **Token budget management** — Redis-backed daily token ledger (observability/token_budget.py), pre-call enforcement middleware (middleware/token_budget.py), 429 with Retry-After
3. **Cost tracking** — Per-model rate table (observability/cost.py), daily/monthly Redis counters in micro-dollars (observability/cost_tracking.py), admin cost endpoint (routers/admin.py)
4. **Structured logging** — structlog JSON config (observability/logging.py), per-request context binding middleware (middleware/logging_context.py)
5. **Tool call auditing** — audited_tool decorator (observability/tool_audit.py), ToolCallCounter + ToolCallLimitExceeded (agents/runner.py)
6. **Safety guardrails** — Mutation tool validator (agents/safety.py), tax/compliance disclaimer injection (agents/disclaimers.py), FinancialDataMixin + staleness check (models/base.py)
7. **Output validation** — Agent runner with retry + fallback on ValidationError (agents/runner.py), AgentOutputError exception handler
8. **Sensitive data redaction** — SSN/account/password/bearer regex patterns (observability/redaction.py), structlog processor
9. **Failure classification** — 6-category ErrorCategory enum (errors/classification.py), exception classifier (errors/classifier.py), global exception handler (errors/handlers.py)
10. **Prometheus metrics** — 12 metric definitions (observability/metrics.py), PrometheusMiddleware (middleware/metrics.py), /metrics endpoint
11. **Graceful degradation** — DependencyHealth tracker (services/degradation.py), LLM fallback chain (agents/fallback.py), vector store fallback to platform text search

## Decisions Already Made

- Langfuse v4 (OTEL-based, no .trace() method)
- structlog for JSON logging
- Prometheus client for metrics
- Redis for token budget + cost tracking (atomic increments)
- Cost in micro-dollars (Decimal * 1M → int) for Redis storage
- 6 error categories: platform_read, llm_provider, transcription, validation, context_too_large, internal
- Max 3 tool calls per agent turn
- Mutation tools rejected at startup via pattern matching

## Constraints

- Langfuse v4 API has no .trace() or .generation() — must adapt all spec code
- Existing errors.py already has SidecarError hierarchy and PlatformReadError — spec introduces a parallel errors/ directory
- Existing middleware (request_id, tenant, logging) already in place — new middleware must integrate
- prometheus_client needs to be added to dependencies
- structlog already in pyproject.toml
- Spec references app/errors/ directory but existing code uses app/errors.py single file

## Open Questions

- [ ] The spec creates a separate `errors/` package (classification.py, classifier.py, handlers.py) but the codebase already has `errors.py` at the app root with SidecarError hierarchy. Should we create the new errors/ package alongside the existing errors.py, or integrate the new classification into the existing file?
- [ ] The spec uses Langfuse v2/v3 API (.trace(), .generation()) throughout but we have Langfuse v4. Should we adapt all Langfuse code to v4 OTEL patterns, or create a thin v2-compatible wrapper?
- [ ] prometheus_client is not currently in pyproject.toml. Should we add it?
