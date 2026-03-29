# Implementation Context: Platform Client

## Chosen Approach

Approach A — Spec-Faithful Implementation. Full 24-method PlatformClient with circuit breaker, request-scoped cache, retry policy, error classification, three adapters, and MockPlatformClient. All platform response models in a separate file.

## Implementation Order

### Step 1: Platform Response Models
Create `src/app/models/platform_models.py` with:
- 7 enums (AccountType, AccountStatus, TransferStatus, OrderStatus, TimelineEventType, DocumentCategory)
- FreshnessMeta base model
- ~20 response models: Holding, HoldingSummary, AccountSummary, HouseholdSummary, ContactInfo, ClientProfile, TransferAsset, TransferCase, OrderProjection, ExecutionProjection, ReportSnapshot, DocumentMetadata, TimelineEvent, ClientSummary, DocumentMatch, RealizedGainsSummary, BenchmarkData, CalendarEvent, TaskSummary, PriorityEmail, AccountAlert, EmailThread, TeamMember, CRMActivity
- All monetary values use Decimal, all data models include FreshnessMeta

### Step 2: AccessScope Update
Update `src/app/models/access_scope.py`:
- Add fields: tenant_id, actor_id, actor_type, request_id, conversation_id
- Add fingerprint() method (SHA256-based)
- Keep existing: visibility_mode, ID lists, allows_*(), to_vector_filter()

### Step 3: Infrastructure Modules
Create three new files:
- `src/app/services/circuit_breaker.py` — CircuitBreaker (CLOSED/OPEN/HALF_OPEN), CircuitOpenError
- `src/app/services/request_cache.py` — RequestScopedCache (dict-based, max 100 entries, monotonic timestamps)
- `src/app/services/retry.py` — RetryPolicy (exponential backoff, retryable codes set)

### Step 4: Error Classification
Update `src/app/errors.py`:
- Add classify_platform_error(response: httpx.Response) -> PlatformReadError
- Maps status codes: 400→BAD_REQUEST, 401→UNAUTHORIZED, 403→FORBIDDEN, 404→NOT_FOUND, 409→CONFLICT, 422→VALIDATION_ERROR, 429→RATE_LIMITED, 5xx→PLATFORM_ERROR

### Step 5: PlatformClient
Replace `src/app/services/platform_client.py`:
- PlatformClientConfig (base_url, service_token, timeout_s, circuit thresholds)
- PlatformClient with __init__(config, cache=None), close()
- _scope_headers(), _cache_key(), _get() with circuit breaker + cache + error handling
- 24 typed async methods

### Step 6: Adapters
Replace stubs:
- `src/app/tools/email_adapter.py` — EmailAdapter with search_emails(), get_recent_emails()
- `src/app/tools/crm_adapter.py` — CRMAdapter with search_notes(), get_activities(), get_open_tasks()
- `src/app/tools/calendar_adapter.py` — CalendarAdapter with get_upcoming_events(), get_today_schedule()

### Step 7: Dependency Wiring
Update `src/app/dependencies.py`:
- Add get_request_cache() -> RequestScopedCache
- Update get_platform_client() to use PlatformClientConfig + inject cache

### Step 8: MockPlatformClient
Create `tests/mocks/__init__.py` and `tests/mocks/mock_platform_client.py`:
- Canned responses for all methods
- set_*() override methods
- set_error() for error simulation

## External Dependencies

| Dependency | Already Installed | Notes |
|---|---|---|
| httpx | Yes | Already used by PlatformClient stub and EmbeddingClient |
| pydantic | Yes | v2, already used everywhere |
| pydantic-settings | Yes | Already used in config.py |

No new external dependencies needed.

## Test Cases

### Circuit Breaker (test_circuit_breaker.py)
- Closed state allows requests
- Opens after N consecutive failures
- Rejects requests when open
- Transitions to half-open after recovery timeout
- Closes after successful probe
- Reopens after failed probe

### Request Cache (test_request_cache.py)
- Cache miss returns None
- Cache hit returns stored value
- Evicts oldest entry at max capacity
- Stats track hits/misses/entries
- Clear empties the cache

### Retry Policy (test_retry.py)
- Returns on first success
- Retries retryable errors up to max_attempts
- Does not retry non-retryable errors
- Exponential backoff between attempts
- Raises after exhausting attempts

### Error Classification (test_errors.py additions)
- classify_platform_error maps each status code correctly
- Extracts structured error body detail
- Falls back to response text on non-JSON bodies

### PlatformClient (test_platform_client.py)
- Scope headers attached to every request
- Cache key is deterministic for same inputs
- Cached response returned on second call
- Circuit breaker opens after threshold failures
- Timeout raises PlatformReadError with TIMEOUT code
- Each typed method calls correct endpoint and parses response

### AccessScope (test_access_scope.py additions)
- fingerprint() returns stable hash
- New fields serialize/deserialize correctly
- Existing allows_*() still work

### Adapters (test_adapters.py)
- EmailAdapter.search_emails builds correct Graph API params
- CRMAdapter.search_notes propagates scope headers
- CalendarAdapter.get_upcoming_events parses response

### MockPlatformClient (test_mock_platform.py)
- Default canned responses returned
- set_*() overrides work
- set_error() raises configured error

## Scope Boundaries

### In scope
- `src/app/models/platform_models.py` (new)
- `src/app/models/access_scope.py` (modify)
- `src/app/services/circuit_breaker.py` (new)
- `src/app/services/request_cache.py` (new)
- `src/app/services/retry.py` (new)
- `src/app/errors.py` (modify)
- `src/app/services/platform_client.py` (replace)
- `src/app/tools/email_adapter.py` (replace)
- `src/app/tools/crm_adapter.py` (replace)
- `src/app/tools/calendar_adapter.py` (replace)
- `src/app/dependencies.py` (modify)
- `tests/mocks/__init__.py` (new)
- `tests/mocks/mock_platform_client.py` (new)

### Out of scope
- Agent implementations (agents/*.py) — not modified
- Tool implementations (tools/platform.py, tools/search.py) — not modified (method signatures preserved)
- RAG pipeline modules — not modified
- Router implementations — not modified
- Main app factory — not modified (lifespan already calls init_platform_client)
