# Test Manifest: Observability and Safety

## Test Files Created

| File | Tests | Purpose |
|---|---|---|
| `tests/test_cost.py` | 3 | Cost computation with Decimal |
| `tests/test_redaction.py` | 4 | SSN/token/password/nested redaction |
| `tests/test_error_classification.py` | 3 | Exception → ErrorCategory mapping |
| `tests/test_safety.py` | 4 | Mutation rejection + disclaimer detection |
| `tests/test_staleness.py` | 2 | Data freshness checks |
| `tests/test_degradation.py` | 3 | DependencyHealth tracking |

## Spec → Test Mapping

| Spec Requirement | Test |
|---|---|
| compute_request_cost known model | test_cost.py::test_known_model_cost |
| Default rate for unknown model | test_cost.py::test_unknown_model_uses_default |
| SSN redaction | test_redaction.py::test_redact_ssn |
| Bearer token redaction | test_redaction.py::test_redact_bearer_token |
| Nested dict redaction | test_redaction.py::test_redact_value_nested_dict |
| PlatformReadError → PLATFORM_READ_FAILURE | test_error_classification.py |
| ValidationError → VALIDATION_FAILURE | test_error_classification.py |
| Mutation tool rejection | test_safety.py::test_rejects_mutation_prefix |
| get_ prefix allowed | test_safety.py::test_allows_get_prefix |
| Tax disclaimer detection | test_safety.py::test_disclaimer_detects_tax_keywords |
| StaleDataWarning for old data | test_staleness.py::test_old_data_is_stale |
| DependencyHealth threshold | test_degradation.py::test_becomes_unhealthy_after_threshold |

## Edge Cases Covered

- Zero tokens → zero cost
- Nested dict/list redaction
- Unknown exception → INTERNAL_ERROR
- Non-tax content → no disclaimer
- Health recovery after success

## Test File Checksums

| File | SHA256 |
|---|---|
| tests/test_cost.py | 85c9b6b110ab3be6fdf59cd0a045c879df6e848290869d17762a11355b200c32 |
| tests/test_redaction.py | 0d421bf994e7d09eeb9da929ba4777fafec1be95f932033c4ca2c33f919f6066 |
| tests/test_error_classification.py | 437d6574c887338e70cd1c7f24c1438bc225cd75690a0e49ac71704167706b3e |
| tests/test_safety.py | be32373680c7ef7957532b41560494313450c8dd8da5388258a3d29af110be6b |
| tests/test_staleness.py | 2b721107717741d14ee67407b10b8e8067b1054165dbad8d3b3f1060f9d9f752 |
| tests/test_degradation.py | 7716713ad6b37dd91506b3342fffa7b387fb918f9754a4f2c1f3c7d00004b3b5 |

## Run Command

```bash
cd apps/intelligence-layer && python -m pytest tests/test_cost.py tests/test_redaction.py tests/test_error_classification.py tests/test_safety.py tests/test_staleness.py tests/test_degradation.py -v
```
