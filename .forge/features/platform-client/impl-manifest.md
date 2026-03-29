# Implementation Manifest: Platform Client

## Files Created

| File | Purpose |
|---|---|
| `src/app/models/platform_models.py` | ~20 response models, 7 enums, FreshnessMeta |
| `src/app/services/circuit_breaker.py` | CircuitBreaker (CLOSED/OPEN/HALF_OPEN) + CircuitOpenError |
| `src/app/services/request_cache.py` | RequestScopedCache (per-request in-memory dict) |
| `src/app/services/retry.py` | RetryPolicy (exponential backoff for batch jobs) |
| `tests/mocks/mock_platform_client.py` | MockPlatformClient with canned data + set_*/set_error |

## Files Modified

| File | Change |
|---|---|
| `src/app/models/access_scope.py` | Added tenant_id, actor_id, actor_type, request_id, conversation_id, fingerprint(); kept allows_*(), to_vector_filter() |
| `src/app/errors.py` | Changed PlatformReadError constructor to (status_code, error_code, message); added classify_platform_error() |
| `src/app/services/platform_client.py` | Replaced 12-method stub with full 24-method implementation using PlatformClientConfig, circuit breaker, request cache |
| `src/app/tools/email_adapter.py` | Added EmailAdapter class with search_emails(), get_recent_emails(); kept tool function |
| `src/app/tools/crm_adapter.py` | Added CRMAdapter class with search_notes(), get_activities(), get_open_tasks(); kept tool function |
| `src/app/tools/calendar_adapter.py` | Added CalendarAdapter class with get_upcoming_events(), get_today_schedule(); kept tool function |
| `src/app/dependencies.py` | Added get_request_cache(); updated init_platform_client() to use PlatformClientConfig |
| `tests/test_errors.py` | Updated PlatformReadError test to match new constructor signature |

## Patterns Followed

- httpx.AsyncClient as transport with connection pooling
- Pydantic v2 models with Decimal for monetary values
- AccessScope passed through every read call
- Circuit breaker shared per PlatformClient instance
- Request-scoped cache (dict-based, max 100 entries)
- SHA256-based deterministic cache keys
- Agent tool backward compatibility (kept existing tool functions in adapter files)

## Test Results

```
116 passed in 1.85s
Ruff: All checks passed!
```
