# Test Manifest: Platform Client

## Test Files Created

| File | Tests | Purpose |
|---|---|---|
| `tests/test_circuit_breaker.py` | 6 | CircuitBreaker state machine (CLOSED/OPEN/HALF_OPEN) |
| `tests/test_request_cache.py` | 5 | RequestScopedCache get/set/evict/stats/clear |
| `tests/test_retry.py` | 4 | RetryPolicy success/retry/non-retryable/exhaustion |
| `tests/test_platform_client.py` | 6 | PlatformClient headers/cache/circuit/timeout/parsing |
| `tests/test_platform_models.py` | 4 | Response model validation, Decimal types, nesting |
| `tests/test_classify_errors.py` | 3 | classify_platform_error status code mapping |
| `tests/test_mock_platform.py` | 3 | MockPlatformClient canned/override/error |
| `tests/test_adapters.py` | 3 | EmailAdapter/CRMAdapter/CalendarAdapter |

## Spec → Test Mapping

| Spec Requirement | Test Location |
|---|---|
| Circuit breaker CLOSED/OPEN/HALF_OPEN states | test_circuit_breaker.py |
| Circuit opens after threshold | test_circuit_breaker.py::test_opens_after_threshold |
| Recovery timeout → HALF_OPEN | test_circuit_breaker.py::test_half_open_after_recovery_timeout |
| Request-scoped cache hit/miss | test_request_cache.py::test_cache_hit/miss |
| Cache eviction at capacity | test_request_cache.py::test_evicts_oldest_at_max_capacity |
| RetryPolicy exponential backoff | test_retry.py::test_retries_retryable_errors |
| Non-retryable errors propagate | test_retry.py::test_does_not_retry_non_retryable |
| Scope headers on every request | test_platform_client.py::test_scope_headers_sent |
| Deterministic cache keys | test_platform_client.py::test_cache_key_deterministic |
| Cached response reuse | test_platform_client.py::test_cached_response_reused |
| Circuit breaker integration | test_platform_client.py::test_circuit_opens_after_failures |
| Timeout → PlatformReadError | test_platform_client.py::test_timeout_raises_platform_error |
| Typed response parsing | test_platform_client.py::test_typed_method_parses_response |
| FreshnessMeta on models | test_platform_models.py::test_freshness_meta_roundtrip |
| Decimal for monetary values | test_platform_models.py::test_account_summary_uses_decimal |
| classify_platform_error mapping | test_classify_errors.py |
| MockPlatformClient canned data | test_mock_platform.py::test_default_canned_response |
| MockPlatformClient overrides | test_mock_platform.py::test_custom_override |
| MockPlatformClient error sim | test_mock_platform.py::test_error_simulation |
| EmailAdapter Graph API call | test_adapters.py::test_email_adapter_search |
| CRMAdapter scope headers | test_adapters.py::test_crm_adapter_scope_headers |
| CalendarAdapter event parsing | test_adapters.py::test_calendar_adapter_parses_events |

## Edge Cases Covered

- Circuit breaker probe failure → reopen
- Cache eviction of oldest entry under pressure
- Non-retryable error codes bypass retry loop
- Non-JSON error response body fallback
- MockPlatformClient error injection by resource key

## Test File Checksums

| File | SHA256 |
|---|---|
| tests/test_circuit_breaker.py | 45a0aa594574d0b35e0f54cc0e54006f521f4f0e7b3c25bf2ca466f2310d77a7 |
| tests/test_request_cache.py | 56d69201dbb3914166702310e9ad9076c6000473b098a7c6b72ac181760ca685 |
| tests/test_retry.py | 6e7193ec7097bdbb0551049ad1c30fc6169868095bd0f8a2a7d5a56b325a6305 |
| tests/test_platform_client.py | 69029e934850a649d6fcc59cd896959ba51d4a193c0b3552bb73350fb94744d0 |
| tests/test_platform_models.py | e8ca8d8d54d40237c336acbc86243a1646575bcb1000b124e15d5885742088a2 |
| tests/test_classify_errors.py | f52bd3a692cb618c18f2ca429aaf76ef08962b19ab7dd980933627cd5a431a55 |
| tests/test_mock_platform.py | 30b424547e7386cbe703937b357b1c123b78ef347670c8de037aec74ebf0b5d2 |
| tests/test_adapters.py | 6315f320cfddd14b40a007cfe4f9cf5cf143ace94cf2b13cd222c31061d49088 |

## Run Command

```bash
cd apps/intelligence-layer && python -m pytest tests/test_circuit_breaker.py tests/test_request_cache.py tests/test_retry.py tests/test_platform_client.py tests/test_platform_models.py tests/test_classify_errors.py tests/test_mock_platform.py tests/test_adapters.py -v
```
