# Test Manifest: Core Infrastructure

## Test Files Created

| File | Test count | Purpose |
|---|---|---|
| `apps/intelligence-layer/tests/test_health.py` | 2 | Health and readiness endpoints |
| `apps/intelligence-layer/tests/test_middleware.py` | 5 | Request ID, tenant context middleware |
| `apps/intelligence-layer/tests/test_access_scope.py` | 8 | AccessScope model validation and filtering |
| `apps/intelligence-layer/tests/test_errors.py` | 3 | Error hierarchy attributes and inheritance |
| `apps/intelligence-layer/tests/test_config.py` | 4 | Settings defaults, singleton, CORS parsing |
| `apps/intelligence-layer/tests/test_cache.py` | 3 | Cache key format utility |

**Total: 6 files, 25 test cases**

## Spec → Test Mapping

| Spec case | Test location |
|---|---|
| T1: GET /health returns 200 | `test_health.py::test_health_returns_ok` |
| T2: GET /ready returns structured checks | `test_health.py::test_ready_returns_structured_checks` |
| T3: Missing tenant headers → 400 | `test_middleware.py::test_missing_tenant_headers_returns_400` |
| T4: Valid headers attach RequestContext | `test_middleware.py::test_valid_tenant_headers_attach_context` |
| T5: X-Request-ID propagation | `test_middleware.py::test_request_id_propagated` |
| T5b: UUID generation when missing | `test_middleware.py::test_request_id_generated_when_missing` |
| T6: full_tenant allows any household | `test_access_scope.py::test_full_tenant_allows_any_household` |
| T7: scoped denies unlisted household | `test_access_scope.py::test_scoped_denies_unlisted_household` |
| T8: to_vector_filter for both modes | `test_access_scope.py::test_to_vector_filter_full_tenant`, `test_to_vector_filter_scoped` |
| T9: cache_key format | `test_cache.py::test_cache_key_format` |
| T10: Error attributes | `test_errors.py::test_sidecar_error_attributes` |
| T11: Settings defaults | `test_config.py::test_settings_loads_with_defaults` |
| T12: ScopeViolationError 403 | `test_errors.py::test_scope_violation_returns_403` |

## Edge Cases Covered

- [x] Health paths skip tenant middleware check
- [x] AccessScope allows/denies for all resource types (household, client, account, document)
- [x] Vector filter with empty resource lists (full_tenant mode)
- [x] Vector filter with multiple resource types (scoped mode)
- [x] CORS origins from comma-separated string
- [x] CORS origins from list
- [x] Settings singleton cache behavior
- [x] All 11 error subclasses validated for correct codes
- [x] Request ID generation (UUID format, 36 chars)
- [x] Cache key with variable number of parts

## Test File Checksums

| File | SHA256 |
|---|---|
| `apps/intelligence-layer/tests/test_health.py` | `3b749c75764e76db7ad0e931610f0ff11b9cf3dadb8bf0c0fd8a607b3b468ec9` |
| `apps/intelligence-layer/tests/test_middleware.py` | `679af503e68c99e63d375221c4afaaa0aaa76b8f643af5e3323f20cd3e8c63bb` |
| `apps/intelligence-layer/tests/test_access_scope.py` | `ee799fcd5d8d868da0f05cb8c79722dd84d1b7a494618880e55e5c0987dbf525` |
| `apps/intelligence-layer/tests/test_errors.py` | `09dc8e2996a7bfde05aa72f436271c51366ef7e15fd64d6f8c9d061cb52744b2` |
| `apps/intelligence-layer/tests/test_config.py` | `87e28ff2f26ccd7016bfd39e5db6fa31490a6270abe3747d31492a394ef2dced` |
| `apps/intelligence-layer/tests/test_cache.py` | `53b7078e4da7dfdcb604cab749198b1b84f60ce53a278e01663a9ed16e194818` |

## Run Command

```bash
cd apps/intelligence-layer && uv run pytest tests/ -v
```
