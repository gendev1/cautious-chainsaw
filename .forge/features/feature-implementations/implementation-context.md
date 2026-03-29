# Implementation Context: Feature Implementations

## Chosen Approach

Approach A — Router-by-router implementation with shared infrastructure.

## Implementation Order

### Step 1: Shared Infrastructure
- `utils/errors.py` — ErrorCategory enum, SidecarErrorResponse model, PlatformReadHTTPError, ModelProviderHTTPError, ValidationHTTPError, InternalHTTPError
- `utils/tracing.py` — Langfuse v4 tracing context manager
- `dependencies.py` — Add get_langfuse

### Step 2: All 9 routers (8 replacements + 1 new)
Each follows pattern: request model → validate → platform reads → agent invoke → response

### Step 3: Chat update + main.py
- Update chat.py for CopilotDeps
- Add crm router to main.py

## External Dependencies

No new dependencies needed.

## Test Cases

### Router tests (test_routers.py)
- Each router endpoint returns correct status code with mock deps
- 202 endpoints return job_id
- Missing headers return 400
- Request validation works

### Error utils (test_error_utils.py)
- PlatformReadHTTPError has status_code 502
- ValidationHTTPError has status_code 422

## Scope Boundaries

### In scope
- src/app/utils/errors.py (new)
- src/app/utils/tracing.py (new)
- src/app/routers/digest.py (replace)
- src/app/routers/email.py (replace)
- src/app/routers/tasks.py (replace)
- src/app/routers/crm.py (new)
- src/app/routers/meetings.py (replace)
- src/app/routers/tax.py (replace)
- src/app/routers/portfolio.py (replace)
- src/app/routers/reports.py (replace)
- src/app/routers/documents.py (replace)
- src/app/routers/chat.py (modify)
- src/app/dependencies.py (modify)
- src/app/main.py (modify)

### Out of scope
- src/app/routers/health.py — not modified
- src/app/routers/indexing.py — not modified
- Agent implementations — not modified
- Job implementations — not modified
