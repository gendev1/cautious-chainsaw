# Implementation Manifest: Core Infrastructure

## Files Created

| File | Purpose |
|---|---|
| `src/app/config.py` | Full SIDECAR_ prefixed Settings with all tiers |
| `src/app/context.py` | Frozen RequestContext dataclass |
| `src/app/errors.py` | SidecarError base + 10 subclasses, 5 categories |
| `src/app/dependencies.py` | Lifespan init/close + Depends() callables + type aliases |
| `src/app/models/__init__.py` | Package init |
| `src/app/models/access_scope.py` | AccessScope model with allows_*() and to_vector_filter() |
| `src/app/middleware/__init__.py` | Package init |
| `src/app/middleware/request_id.py` | RequestIdMiddleware — propagate/generate X-Request-ID |
| `src/app/middleware/tenant.py` | TenantContextMiddleware — extract tenant headers, build RequestContext |
| `src/app/middleware/logging.py` | StructuredLoggingMiddleware — log method/path/status/duration |
| `src/app/utils/__init__.py` | Package init |
| `src/app/utils/cache.py` | cache_key() scoped by tenant + actor |
| `src/app/services/__init__.py` | Package init |
| `src/app/services/vector_store.py` | VectorStore stub |
| `src/app/services/platform_client.py` | PlatformClient stub |
| `src/app/rag/__init__.py` | Package init |
| `src/app/rag/retriever.py` | Retriever stub |
| `src/app/agents/__init__.py` | Package init |
| `src/app/agents/deps.py` | AgentDeps frozen dataclass |
| `src/app/routers/__init__.py` | Package init |
| `src/app/routers/health.py` | /health + /ready with dependency checks |
| `src/app/routers/chat.py` | Stub router |
| `src/app/routers/digest.py` | Stub router |
| `src/app/routers/email.py` | Stub router |
| `src/app/routers/tasks.py` | Stub router |
| `src/app/routers/meetings.py` | Stub router |
| `src/app/routers/tax.py` | Stub router |
| `src/app/routers/portfolio.py` | Stub router |
| `src/app/routers/reports.py` | Stub router |
| `src/app/routers/documents.py` | Stub router |
| `src/app/jobs/__init__.py` | Package init |
| `src/app/jobs/worker.py` | ARQ WorkerSettings with job registrations |
| `src/app/jobs/daily_digest.py` | Stub job |
| `src/app/jobs/email_triage.py` | Stub job |
| `src/app/jobs/firm_report.py` | Stub job |
| `src/app/jobs/style_profile.py` | Stub job |
| `src/app/jobs/transcription.py` | Stub job |

## Files Modified

| File | Change |
|---|---|
| `src/app/main.py` | Rewritten — full create_app() factory with lifespan, middleware, exception handlers, routers |
| `src/app/__init__.py` | Updated to export from app.main instead of app.app |

## Files Deleted

| File | Reason |
|---|---|
| `src/app/app.py` | Replaced by main.py |
| `src/app/api/__init__.py` | Replaced by routers/ package |
| `src/app/api/routes.py` | Replaced by routers/health.py |

## Patterns Followed

- Factory pattern: `create_app()` returns configured `FastAPI` instance
- Lifespan: `@asynccontextmanager` for startup/shutdown resource management
- Settings singleton: `@lru_cache(maxsize=1)` on `get_settings()`
- DI via `Depends()`: all shared resources accessed through typed callables
- Annotated type aliases: `RedisClient`, `VectorStoreClient`, `Platform`, `Ctx`, `AppSettings`
- Frozen dataclasses: `RequestContext`, `AgentDeps`
- Error hierarchy: base `SidecarError` with typed subclasses per category
- Middleware ordering: CORS → RequestId → TenantContext → StructuredLogging

## Test Results

```
26 passed in 0.41s
Ruff: All checks passed!
```
