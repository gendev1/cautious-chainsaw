# Exploration: Core Infrastructure

## Most Similar Feature

The existing codebase itself is the closest reference — a minimal FastAPI shell with:
- `app/app.py`: factory `create_app()` with lifespan context manager
- `app/config.py`: `Settings(BaseSettings)` with `@lru_cache` singleton
- `app/api/routes.py`: health endpoints (`/healthz`, `/readyz`)

**What to reuse:** Factory pattern, lifespan approach, settings singleton pattern.
**What to replace:** The entire file structure will be restructured. `app/app.py` → `app/main.py`, `app/api/routes.py` → `app/routers/health.py`, health endpoints change from `/healthz`+`/readyz` to `/health`+`/ready`.

## Architecture Map

Layers relevant to core infrastructure:

```
Request → Middleware Stack → Router → Handler → Dependencies (DI)
              │                                      │
              ├── CORS                               ├── Redis client
              ├── RequestId                          ├── VectorStore client
              ├── TenantContext                      ├── PlatformClient
              └── StructuredLogging                  ├── RequestContext
                                                     └── Settings

App State (lifespan-managed)
    ├── settings
    ├── redis
    ├── vector_store
    └── platform_client

Background Jobs (ARQ)
    └── Worker reads from Redis queue
```

## Structural Patterns

### Settings pattern [grep-fallback]
- `Settings(BaseSettings)` with `model_config = SettingsConfigDict(...)` and `@lru_cache def get_settings()`
- Example: `src/app/config.py` lines 6-25
- Match count: 1 [insufficient-sample: 1 match]

### Factory pattern [grep-fallback]
- `def create_app() -> FastAPI:` with lifespan context manager
- Example: `src/app/app.py` lines 10-27
- Match count: 1 [insufficient-sample: 1 match]

### Router pattern [grep-fallback]
- `router = APIRouter()` with `@router.get(...)` decorated handlers
- Example: `src/app/api/routes.py` lines 11-38
- Match count: 1 [insufficient-sample: 1 match]

### Test pattern [grep-fallback]
- `TestClient(create_app())` for synchronous endpoint testing
- Example: `tests/test_health.py` lines 7-16
- Match count: 1 [insufficient-sample: 1 match]

### Dependency access via request.app.state [grep-fallback]
- `request.app.state.settings` to access lifespan-stored objects
- Example: `src/app/api/routes.py` line 18
- Match count: 2 [insufficient-sample: 2 matches]

Note: All patterns have insufficient sample sizes because this is a nascent codebase. Verify should treat them as non-binding.

## Key Files

### Reference reading
- `src/app/config.py` — current settings shape (will be expanded)
- `src/app/app.py` — current factory and lifespan (will be replaced by `main.py`)
- `src/app/api/routes.py` — current health endpoints (will be replaced)
- `pyproject.toml` — current dependencies and project metadata

### Expected edits
- `src/app/main.py` — rewrite as spec's application entry point
- `src/app/config.py` — expand to full SIDECAR_ prefixed settings
- `pyproject.toml` — add missing dependencies

### Expected new files
- `src/app/context.py` — RequestContext dataclass
- `src/app/errors.py` — error hierarchy
- `src/app/dependencies.py` — DI wiring
- `src/app/middleware/tenant.py` — tenant context middleware
- `src/app/middleware/request_id.py` — request ID middleware
- `src/app/middleware/logging.py` — structured logging middleware
- `src/app/models/access_scope.py` — access scope model
- `src/app/routers/health.py` — liveness and readiness
- `src/app/routers/*.py` — stub routers (chat, digest, email, etc.)
- `src/app/services/platform_client.py` — platform client stub
- `src/app/services/vector_store.py` — vector store stub
- `src/app/rag/retriever.py` — retriever stub
- `src/app/utils/cache.py` — cache key utility
- `src/app/agents/deps.py` — AgentDeps bridge
- `src/app/jobs/worker.py` — ARQ worker entry point
- `src/app/jobs/*.py` — stub job modules

### Expected deletions
- `src/app/app.py` — replaced by `main.py`
- `src/app/api/routes.py` — replaced by `routers/health.py`
- `src/app/api/__init__.py` — directory replaced

### Expected tests
- `tests/test_health.py` — rewrite for new `/health` and `/ready` endpoints
- `tests/test_middleware.py` — middleware chain tests
- `tests/test_config.py` — settings validation tests
- `tests/test_errors.py` — error hierarchy tests
- `tests/test_access_scope.py` — scope enforcement tests
