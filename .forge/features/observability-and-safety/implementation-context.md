# Implementation Context: Observability and Safety

## Chosen Approach

Approach A — Module-by-module with independent testability.

## Implementation Order

### Step 1: Pure observability modules (no external deps except stdlib)
- observability/cost.py, observability/token_budget.py, observability/redaction.py, observability/logging.py

### Step 2: Error classification package
- errors/__init__.py, errors/classification.py, errors/classifier.py, errors/handlers.py

### Step 3: Safety and models
- models/base.py, agents/safety.py, agents/disclaimers.py

### Step 4: Langfuse (v4) + Prometheus modules
- observability/langfuse_client.py, observability/tracing.py, observability/metrics.py, observability/tool_audit.py

### Step 5: Middleware
- middleware/tracing.py, middleware/token_budget.py, middleware/logging_context.py, middleware/metrics.py

### Step 6: Agent runner + fallback + degradation
- agents/runner.py, agents/fallback.py, services/degradation.py

### Step 7: Admin router + cost_tracking
- observability/cost_tracking.py, routers/admin.py

### Step 8: Wiring
- pyproject.toml, main.py

## External Dependencies

| Dependency | Status | Action |
|---|---|---|
| prometheus_client | Not installed | Add to pyproject.toml |
| structlog | Already installed | — |
| langfuse | Already installed (v4) | — |

## Test Cases

### Cost computation (test_cost.py)
- compute_request_cost returns correct Decimal for known model
- compute_request_cost uses default rate for unknown model
- Zero tokens returns zero cost

### Token budget (test_token_budget.py)
- _budget_key includes today's date
- check_budget returns allowed when under limit
- check_budget returns denied when over limit

### Redaction (test_redaction.py)
- redact_string replaces SSN pattern
- redact_string replaces account number pattern
- redact_string replaces Bearer token
- redact_value handles nested dicts

### Error classification (test_error_classification.py)
- classify_exception maps PlatformReadError to PLATFORM_READ_FAILURE
- classify_exception maps ValidationError to VALIDATION_FAILURE
- classify_exception maps unknown to INTERNAL_ERROR

### Safety (test_safety.py)
- validate_tool_safety rejects create_ prefix
- validate_tool_safety allows get_ prefix
- check_disclaimer detects tax keywords
- check_disclaimer returns not required for non-tax content

### Staleness (test_staleness.py)
- check_staleness returns not stale for recent data
- check_staleness returns stale for old data

## Scope Boundaries

### In scope
All files listed in architecture. ~22 new files + 2 modified.

### Out of scope
- Existing errors.py (SidecarError hierarchy) — not modified
- Existing middleware files — not modified (new middleware added alongside)
- Agent implementations — not modified
- Router implementations — not modified (except main.py wiring)
