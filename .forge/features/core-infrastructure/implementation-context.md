# Implementation Context: Core Infrastructure

## Chosen Approach

Approach A: Spec-Faithful Bottom-Up. Build each layer from foundation up following the spec's production-ready code blocks. Each step creates files with no forward-reference dependencies.

## Implementation Order

### Step 1: Configuration
- **Files:** `src/app/config.py` (rewrite)
- **What:** Replace existing simple Settings with full spec Settings class using `SIDECAR_` prefix, all model tiers, Redis, ARQ, vector store, transcription, Langfuse, cache TTL fields. Add `cors_allowed_origins` field validator.
- **Verify:** Module imports cleanly, `Settings()` instantiates with defaults.

### Step 2: Models
- **Files:** `src/app/models/__init__.py` (new), `src/app/models/access_scope.py` (new)
- **What:** `AccessScope(BaseModel)` with `visibility_mode`, resource ID lists, `allows_*()` methods, `to_vector_filter()`.
- **Verify:** Model validates JSON, filter generation works for both modes.

### Step 3: Request Context
- **Files:** `src/app/context.py` (new)
- **What:** Frozen `RequestContext` dataclass with tenant_id, actor_id, actor_type, request_id, conversation_id, access_scope.
- **Verify:** Dataclass is frozen and immutable.

### Step 4: Error Hierarchy
- **Files:** `src/app/errors.py` (new)
- **What:** `SidecarError` base + 10 subclasses covering 5 error categories.
- **Verify:** Each error has correct status_code, error_code, category.

### Step 5: Cache Utility
- **Files:** `src/app/utils/__init__.py` (new), `src/app/utils/cache.py` (new)
- **What:** `cache_key(namespace, tenant_id, actor_id, *parts)` function.
- **Verify:** Key format is `namespace:tenant:actor:parts`.

### Step 6: Service Stubs
- **Files:** `src/app/services/__init__.py` (new), `src/app/services/vector_store.py` (new), `src/app/services/platform_client.py` (new)
- **What:** `VectorStore` stub with `connect()`, `disconnect()`, `health_check()`, `similarity_search()`. `PlatformClient` stub with httpx client, `close()`, scope-enforced reads.
- **Verify:** Both instantiate and their async methods are callable.

### Step 7: RAG Stub
- **Files:** `src/app/rag/__init__.py` (new), `src/app/rag/retriever.py` (new)
- **What:** `Retriever` stub with `search()` accepting query, tenant_id, access_scope.
- **Verify:** Imports cleanly.

### Step 8: Agent Deps
- **Files:** `src/app/agents/__init__.py` (new), `src/app/agents/deps.py` (new)
- **What:** Frozen `AgentDeps` dataclass with context, platform, redis, retriever. Property shortcuts for tenant_id and access_scope.
- **Verify:** Dataclass is frozen, properties work.

### Step 9: Middleware
- **Files:** `src/app/middleware/__init__.py` (new), `src/app/middleware/request_id.py` (new), `src/app/middleware/tenant.py` (new), `src/app/middleware/logging.py` (new)
- **What:** Three BaseHTTPMiddleware subclasses per the spec. RequestId generates/propagates UUID. Tenant extracts headers, builds RequestContext, returns 400 on missing required headers (skips health/docs paths). Logging emits structured request log with duration.
- **Verify:** Each middleware dispatches correctly in isolation.

### Step 10: Dependencies
- **Files:** `src/app/dependencies.py` (new)
- **What:** Lifespan init/close helpers for Redis, VectorStore, PlatformClient. FastAPI `Depends()` callables. Annotated type aliases.
- **Verify:** All callables importable and type-correct.

### Step 11: Health Router
- **Files:** `src/app/routers/__init__.py` (new), `src/app/routers/health.py` (new)
- **What:** `GET /health` (liveness) and `GET /ready` (readiness with Redis/vector store/platform API/LLM provider checks).
- **Verify:** `/health` returns 200, `/ready` returns structured checks JSON.

### Step 12: Domain Router Stubs
- **Files:** `src/app/routers/chat.py`, `digest.py`, `email.py`, `tasks.py`, `meetings.py`, `tax.py`, `portfolio.py`, `reports.py`, `documents.py` (all new)
- **What:** Each has `router = APIRouter(tags=[...])` with no endpoints.
- **Verify:** All import cleanly.

### Step 13: Job Stubs
- **Files:** `src/app/jobs/__init__.py` (new), `src/app/jobs/worker.py` (new), `src/app/jobs/daily_digest.py`, `email_triage.py`, `firm_report.py`, `style_profile.py`, `transcription.py` (all new)
- **What:** Worker with `WorkerSettings` class. Each job module has an async function matching the ARQ registration.
- **Verify:** Worker module imports cleanly.

### Step 14: Main Application and Cleanup
- **Files:** `src/app/main.py` (rewrite), delete `src/app/app.py`, delete `src/app/api/` directory
- **What:** `create_app()` factory with lifespan, middleware registration (CORS → RequestId → TenantContext → StructuredLogging), exception handlers, router inclusion. Module-level `app = create_app()`.
- **Verify:** App boots, all middleware and routers registered.

### Step 15: Package Init and Dependencies
- **Files:** `src/app/__init__.py` (update), `pyproject.toml` (update)
- **What:** Update init to export from `app.main`. Ensure pyproject.toml has all required deps.
- **Verify:** `uv sync` succeeds.

### Step 16: Tests
- **Files:** `tests/test_health.py` (rewrite), new test files as needed
- **What:** Rewrite health tests for `/health` and `/ready`. Add middleware, config, error, and access scope tests.
- **Verify:** All tests pass.

## External Dependencies

| Package | Purpose | Already in pyproject.toml? |
|---|---|---|
| fastapi | Web framework | Yes |
| uvicorn | ASGI server | Yes |
| pydantic-settings | Configuration | Yes |
| pydantic-ai | Agent framework | Yes |
| httpx | HTTP client (platform API) | Yes |
| redis | Redis client | Yes |
| arq | Background job queue | Yes |
| structlog | Structured logging | Yes |
| langfuse | Observability | Yes |

All runtime dependencies are already declared. No new packages needed.

## Test Cases

- **T1:** `GET /health` returns `{"status": "ok"}` with 200
- **T2:** `GET /ready` returns structured checks JSON (mocked deps)
- **T3:** Request without `X-Tenant-ID` returns 400 with `MISSING_CONTEXT` error
- **T4:** Request with valid tenant headers attaches `RequestContext` to `request.state`
- **T5:** `X-Request-ID` header is propagated; missing header generates UUID
- **T6:** `AccessScope(visibility_mode="full_tenant").allows_household("any")` returns True
- **T7:** `AccessScope(visibility_mode="scoped", household_ids=["h1"]).allows_household("h2")` returns False
- **T8:** `to_vector_filter()` includes tenant_id and OR filter for scoped mode
- **T9:** `cache_key("chat", "t1", "a1", "conv")` returns `"chat:t1:a1:conv"`
- **T10:** `SidecarError` subclasses carry correct status_code and error_code
- **T11:** Settings loads with defaults when no env vars are set
- **T12:** `ScopeViolationError` returns 403 with correct JSON envelope

## Scope Boundaries

### In scope
- All files under `src/app/` as specified in the implementation order
- Rewriting `tests/test_health.py` and adding new unit tests
- Updating `pyproject.toml` if any deps are missing
- Deleting `src/app/app.py` and `src/app/api/` directory

### Out of scope
- Full VectorStore implementation (stub only)
- Full PlatformClient business methods beyond `close()` (stub only)
- Full Retriever implementation (stub only)
- Domain router endpoint implementations (empty stubs only)
- Job function implementations (empty stubs only)
- Dockerfile and docker-compose.yml creation (deployment artifacts, not Python code)
- Integration tests requiring Redis, pgvector, or external services
- Langfuse instrumentation wiring (config only, not trace decorators)
