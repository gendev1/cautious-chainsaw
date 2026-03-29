# Test Manifest: Feature Implementations

## Test Files Created

| File | Tests | Purpose |
|---|---|---|
| `tests/test_error_utils.py` | 4 | Error category enum, HTTP exceptions, response model |
| `tests/test_router_endpoints.py` | 6 | Router endpoint wiring via FastAPI TestClient |

## Spec → Test Mapping

| Spec Requirement | Test Location |
|---|---|
| ErrorCategory 4 values | test_error_utils.py::test_error_category_values |
| PlatformReadHTTPError 502 | test_error_utils.py::test_platform_read_error_status_502 |
| ValidationHTTPError 422 | test_error_utils.py::test_validation_error_status_422 |
| SidecarErrorResponse model | test_error_utils.py::test_sidecar_error_response_serializes |
| POST /ai/digest/generate 202 | test_router_endpoints.py::test_digest_generate_returns_202 |
| GET /ai/digest/latest 404 | test_router_endpoints.py::test_digest_latest_returns_404_when_empty |
| POST /ai/email/draft routable | test_router_endpoints.py::test_email_draft_endpoint_exists |
| POST /ai/meetings/transcribe 202 | test_router_endpoints.py::test_meetings_transcribe_returns_202 |
| POST /ai/documents/classify routable | test_router_endpoints.py::test_documents_classify_endpoint_exists |
| Missing headers 422 | test_router_endpoints.py::test_missing_tenant_header_returns_422 |

## Edge Cases Covered

- Missing tenant headers returns 422
- Empty Redis cache returns 404 for digest
- Endpoint routability verified even when agent deps fail

## Test File Checksums

| File | SHA256 |
|---|---|
| tests/test_error_utils.py | b3304835181a0bb91df99e14a8c61806a6973c838ff1aa817f858d3004d85a9b |
| tests/test_router_endpoints.py | 3a7b5aac55b35ccb1a37490e0af57c68ebf1893974345a6f05cfca0b08d0e2b4 |

## Run Command

```bash
cd apps/intelligence-layer && python -m pytest tests/test_error_utils.py tests/test_router_endpoints.py -v
```
