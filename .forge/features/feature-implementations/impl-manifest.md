# Implementation Manifest: Feature Implementations

## Files Created

| File | Purpose |
|---|---|
| `src/app/utils/errors.py` | ErrorCategory, SidecarErrorResponse, 4 HTTP exception classes |
| `src/app/utils/tracing.py` | Langfuse v4 tracing context manager |
| `src/app/routers/crm.py` | POST /crm/sync-payload — CRM sync payload generation |

## Files Modified

| File | Change |
|---|---|
| `src/app/routers/digest.py` | Replaced stub: POST /digest/generate (202), GET /digest/latest |
| `src/app/routers/email.py` | Replaced stub: POST /email/draft, POST /email/triage |
| `src/app/routers/tasks.py` | Replaced stub: POST /tasks/extract |
| `src/app/routers/meetings.py` | Replaced stub: POST /meetings/prep, /transcribe (202), /summarize, GET /{id}/summary |
| `src/app/routers/tax.py` | Replaced stub: POST /tax/plan |
| `src/app/routers/portfolio.py` | Replaced stub: POST /portfolio/analyze |
| `src/app/routers/reports.py` | Replaced stub: POST /reports/firm-wide (202), POST /reports/narrative |
| `src/app/routers/documents.py` | Replaced stub: POST /documents/classify, POST /documents/extract |
| `src/app/dependencies.py` | Added get_langfuse() dependency |
| `src/app/main.py` | Added crm router import + inclusion |

## Patterns Followed

- All endpoints use Annotated[RequestContext, Depends(get_request_context)]
- Agents imported lazily inside endpoint functions
- AgentDeps constructed per-request from platform, access_scope, tenant_id, actor_id
- 202 endpoints enqueue ARQ jobs via JobContext + get_job_pool
- Cache reads use tenant-scoped Redis keys
- Error handling: ModelProviderHTTPError for agent failures, InternalHTTPError for enqueue failures
- All raise statements use `from exc` in except blocks (B904)

## Test Results

```
149 passed in 3.09s
Ruff: All checks passed!
```
