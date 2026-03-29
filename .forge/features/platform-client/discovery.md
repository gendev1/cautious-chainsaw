# Discovery: Platform Client

## Requirements

1. **PlatformClient class** — `httpx.AsyncClient`-based, read-only, typed methods. One method per approved platform API endpoint. No generic fetch, no dynamic endpoint construction.
2. **24 typed read methods** covering: household summary, account summary, client profile, transfer case, order projection, execution projection, report snapshot, document metadata, client timeline, advisor clients, firm accounts, document search, document content, client holdings, client realized gains, client accounts, benchmark data, advisor calendar, advisor tasks, advisor priority emails, account alerts, email thread, advisor team, CRM activity feed.
3. **AccessScope model** — Pydantic v2 model with tenant_id, actor_id, actor_type, request_id, conversation_id, visibility_mode, and ID lists (household, client, account, document, advisor). Includes `fingerprint()` method for cache keys.
4. **Scope propagation** — Every request carries `X-Tenant-ID`, `X-Actor-ID`, `X-Request-ID`, `X-Access-Scope` headers. No code path can bypass scope attachment.
5. **PlatformReadError + classify_platform_error** — Single exception type with status_code, error_code, message. Error classification maps HTTP status codes to named error codes (BAD_REQUEST, UNAUTHORIZED, FORBIDDEN, NOT_FOUND, CONFLICT, VALIDATION_ERROR, RATE_LIMITED, PLATFORM_ERROR).
6. **CircuitBreaker** — Consecutive-failure circuit breaker (CLOSED/OPEN/HALF_OPEN states). Default threshold: 5 failures, 30s recovery timeout.
7. **RequestScopedCache** — Per-request in-memory dict cache (max 100 entries). Created per FastAPI request, discarded after. SHA256 cache keys from method name + args.
8. **RetryPolicy** — Exponential backoff for batch jobs only (max 3 attempts, 0.5s base delay, 5s max). Not used for interactive requests.
9. **EmailAdapter** — Typed read-only client for Microsoft Graph API email reads (search_emails, get_recent_emails).
10. **CRMAdapter** — Typed read-only client for CRM via platform integration endpoints (search_notes, get_activities, get_open_tasks).
11. **CalendarAdapter** — Typed read-only client for Graph API calendar (get_upcoming_events, get_today_schedule).
12. **MockPlatformClient** — Test double with canned responses for all methods, configurable overrides via set_* methods and error simulation via set_error.
13. **~20 Pydantic response models** — All monetary values use Decimal. Data-bearing models include FreshnessMeta. Enums for account type/status, transfer status, order status, timeline event type, document category.
14. **Integration tests** — Using httpx.MockTransport to verify header propagation, circuit breaker behavior, timeout handling.
15. **FastAPI dependency integration** — `get_request_cache()` and `get_platform_client()` dependencies that wire cache into client per request.

## Decisions Already Made

- Transport is httpx.AsyncClient (async-native, connection pooling, HTTP/2)
- 3-second timeout for interactive reads, 2-second connect timeout
- No automatic retries for interactive requests (fail fast)
- Circuit breaker is shared across all methods on a single PlatformClient instance
- Service-to-service auth via Bearer token (service JWT or shared secret)
- CRM reads go through platform integration endpoints, not directly to Salesforce/Wealthbox
- All adapters are read-only; sidecar never sends emails, creates events, or writes CRM records
- AccessScope is pass-through — sidecar never widens, narrows, or recomputes it
- Response models use Decimal for money, never float
- FreshnessMeta on all data-bearing models

## Constraints

- Must integrate with existing `app/services/platform_client.py` (currently a stub with 12 empty method signatures)
- Must integrate with existing `app/models/access_scope.py` (AccessScope already exists with allows_*() and to_vector_filter())
- Must integrate with existing `app/models/schemas.py` (already has 22 agent output models)
- Must integrate with existing `app/config.py` Settings class (already has platform_api_url, platform_service_token fields)
- Existing tools in `app/tools/` (email_adapter.py, crm_adapter.py, calendar_adapter.py) are stubs that need replacing
- The spec uses `app/` import paths but the actual codebase uses `src/app/` prefixed paths under `apps/intelligence-layer/`
- Existing errors.py has SidecarError hierarchy; spec introduces PlatformReadError (separate from SidecarError)
- All code runs under Python 3.12+, Pydantic v2

## Open Questions

- [ ] The spec defines a new AccessScope in schemas.py with different fields (household_ids, client_ids, etc.) than the existing AccessScope in models/access_scope.py (which has visibility_mode, household_ids, etc. plus allows_*() methods). Should we merge these into one model or replace the existing one? The spec's AccessScope is very similar but adds document_ids, advisor_ids lists and a fingerprint() method.
- [ ] The spec defines ~20 new response models (HouseholdSummary, AccountSummary, ClientProfile, etc.) that should go in schemas.py. The existing schemas.py already has 22 agent output models. Should we keep all in one file or split platform response models into a separate file (e.g., `models/platform_models.py`)?
- [ ] The existing `app/services/platform_client.py` has 12 stub methods. The spec defines 24 methods. Should we replace the stub entirely with the full spec implementation?
