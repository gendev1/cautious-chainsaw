# Design Discussion: Observability and Safety

## Resolved Decisions

### 1. Errors package — Create new alongside existing
- **Category**: blocking
- **Decision**: Create `errors/` package (classification.py, classifier.py, handlers.py) as new files. Keep existing `errors.py` (SidecarError hierarchy) untouched.
- **Rationale**: New error classification system is orthogonal — it classifies exceptions for HTTP responses, while existing errors.py defines the exception types themselves.

### 2. Langfuse — Adapt to v4 OTEL
- **Category**: blocking
- **Decision**: All Langfuse code adapted to v4 OTEL API. Use `_start_as_current_otel_span_with_processed_media` for spans, no `.trace()` or `.generation()`. Spike to verify API surface.
- **Rationale**: Real Langfuse v4 is installed. User has .env with keys configured.

### 3. prometheus_client — Add to dependencies
- **Category**: blocking
- **Decision**: Add prometheus_client to pyproject.toml.

## Open Questions

None.

## Summary for Architect

New files (~20):
- `observability/` package: langfuse_client.py, tracing.py, cost.py, cost_tracking.py, token_budget.py, metrics.py, redaction.py, tool_audit.py, logging.py
- `middleware/`: tracing.py, token_budget.py, logging_context.py, metrics.py
- `agents/`: safety.py, disclaimers.py, runner.py, fallback.py
- `models/base.py`: FinancialDataMixin, StaleDataWarning
- `errors/`: __init__.py, classification.py, classifier.py, handlers.py
- `services/degradation.py`: DependencyHealth, DegradedResult
- `routers/admin.py`: Internal cost dashboard

Modified:
- pyproject.toml: add prometheus_client
- main.py: register new middleware + admin router + error handlers + startup validation
