# Architecture: Platform Client

## Approach A: Spec-Faithful Implementation (Recommended)

Implement the spec as-is, adapting import paths and integrating with the existing codebase. All new support modules (circuit breaker, request cache, retry, error classifier) are standalone files. Platform response models get their own file. AccessScope is extended in place.

**Files created:**
1. `models/platform_models.py` — ~20 response models, 7 enums, FreshnessMeta
2. `services/circuit_breaker.py` — CircuitBreaker + CircuitOpenError
3. `services/request_cache.py` — RequestScopedCache
4. `services/retry.py` — RetryPolicy
5. `tests/mocks/__init__.py` + `tests/mocks/mock_platform_client.py` — MockPlatformClient

**Files modified:**
6. `models/access_scope.py` — Add tenant_id, actor_id, actor_type, request_id, conversation_id, fingerprint(); keep allows_*(), to_vector_filter()
7. `services/platform_client.py` — Replace stub with full 24-method implementation using PlatformClientConfig, circuit breaker, request cache
8. `errors.py` — Add classify_platform_error() function
9. `tools/email_adapter.py` — Replace stub with full EmailAdapter class
10. `tools/crm_adapter.py` — Replace stub with full CRMAdapter class
11. `tools/calendar_adapter.py` — Replace stub with full CalendarAdapter class
12. `dependencies.py` — Add get_request_cache(), update get_platform_client() to use PlatformClientConfig + cache

**Trade-offs:**
- (+) Directly matches spec, easy to verify against
- (+) Clean separation: infrastructure modules (circuit breaker, cache, retry) are independently testable
- (+) Preserves all existing method signatures used by tools/platform.py and tools/search.py
- (-) 12 files touched, but scope is clear and bounded

**Task breakdown (dependency order):**
1. `models/platform_models.py` (no deps)
2. `models/access_scope.py` update (no deps on new code)
3. `services/circuit_breaker.py` (no deps)
4. `services/request_cache.py` (no deps)
5. `services/retry.py` (depends on errors.py)
6. `errors.py` update — add classify_platform_error (depends on httpx)
7. `services/platform_client.py` (depends on 1-6)
8. `tools/email_adapter.py` (depends on access_scope, errors)
9. `tools/crm_adapter.py` (depends on access_scope, errors)
10. `tools/calendar_adapter.py` (depends on access_scope, errors)
11. `dependencies.py` update (depends on 7, 4)
12. `tests/mocks/mock_platform_client.py` (depends on 1, 6)

## Approach B: Minimal Core First

Implement only PlatformClient + response models + error classifier. Defer circuit breaker, request cache, retry, and adapter rewrites to a follow-up.

**Trade-offs:**
- (+) Smaller diff, faster to land
- (-) Incomplete spec coverage — circuit breaker and caching are core to the spec's design
- (-) Adapters stay as stubs, blocking agent integration tests
- (-) Would need a second pass to finish

## Recommendation

**Approach A** — The spec defines a cohesive system where circuit breaker, caching, and error classification are tightly integrated into PlatformClient._get(). Implementing them separately would require stubbing internal PlatformClient behavior, adding complexity for no benefit. The dependency order is clean and each file is independently testable.

## Task Breakdown (recommended approach)

| Order | File | Action | Depends On |
|---|---|---|---|
| 1 | `models/platform_models.py` | Create: ~20 response models, 7 enums, FreshnessMeta | — |
| 2 | `models/access_scope.py` | Update: add identity fields + fingerprint() | — |
| 3 | `services/circuit_breaker.py` | Create: CircuitBreaker + CircuitOpenError | — |
| 4 | `services/request_cache.py` | Create: RequestScopedCache | — |
| 5 | `errors.py` | Update: add classify_platform_error() | — |
| 6 | `services/retry.py` | Create: RetryPolicy | errors.py |
| 7 | `services/platform_client.py` | Replace: full 24-method client | 1-6 |
| 8 | `tools/email_adapter.py` | Replace: EmailAdapter | access_scope, errors |
| 9 | `tools/crm_adapter.py` | Replace: CRMAdapter | access_scope, errors |
| 10 | `tools/calendar_adapter.py` | Replace: CalendarAdapter | access_scope, errors |
| 11 | `dependencies.py` | Update: get_request_cache(), get_platform_client() | 4, 7 |
| 12 | `tests/mocks/mock_platform_client.py` | Create: MockPlatformClient | 1, 5 |
