# Design Discussion: Platform Client

## Resolved Decisions

### 1. AccessScope — Merge into existing model
- **Category**: blocking
- **Decision**: Add the spec's new fields (tenant_id, actor_id, actor_type, request_id, conversation_id, fingerprint()) to the existing `models/access_scope.py` AccessScope. Keep existing allows_*() methods and to_vector_filter() intact.
- **Rationale**: The existing access_scope.py was scaffolded. The spec's AccessScope is the canonical shape. Merging avoids two competing AccessScope models.
- **Constraint**: Preserve backward compatibility for allows_*(), to_vector_filter(), and all existing tests.

### 2. Response models — Separate file
- **Category**: blocking
- **Decision**: Create `models/platform_models.py` for platform response models (HouseholdSummary, AccountSummary, etc.) and enums. Keep agent output models in `schemas.py`.
- **Rationale**: ~20 new models + 7 enums + FreshnessMeta is a substantial surface. Mixing with 22 agent output models in schemas.py would create a 600+ line file with two unrelated concerns. Clean separation follows the existing pattern of access_scope.py being its own file.
- **Constraint**: PlatformClient imports from platform_models.py. Agent tools that need both import from both.

### 3. PlatformClient — Replace stub entirely
- **Category**: blocking
- **Decision**: Replace the existing 12-method stub with the full 24-method implementation from the spec.
- **Rationale**: The stub was scaffolding. The spec is the target implementation.
- **Constraint**: Preserve method signature compatibility for the 7 methods used by tools/platform.py and 4 methods used by tools/search.py.

## Open Questions

None — all questions resolved.

## Summary for Architect

- Merge AccessScope fields into existing access_scope.py (add fingerprint, identity fields; keep allows_*())
- New file: `models/platform_models.py` for ~20 response models + enums + FreshnessMeta
- New file: `services/circuit_breaker.py` for CircuitBreaker + CircuitOpenError
- New file: `services/request_cache.py` for RequestScopedCache
- New file: `services/retry.py` for RetryPolicy
- New file: `services/errors.py` addition: classify_platform_error() function
- Replace: `services/platform_client.py` — full 24-method implementation
- Replace: `tools/email_adapter.py`, `crm_adapter.py`, `calendar_adapter.py` — full typed adapters
- New file: `tests/mocks/mock_platform_client.py` — MockPlatformClient
- Update: `dependencies.py` — add get_request_cache(), update get_platform_client()
- Existing tools/platform.py and tools/search.py should work without changes (method names preserved)
