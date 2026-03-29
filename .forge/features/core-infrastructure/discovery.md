# Discovery: Core Infrastructure

## Requirements

- **R1: FastAPI application shell** — Create `app/main.py` with `create_app()` factory, lifespan manager for startup/shutdown of shared resources (Redis, vector store, platform client), and module-level `app` instance.
- **R2: Middleware chain** — Register four middleware layers in strict order: CORS (outermost) → RequestId → TenantContext → StructuredLogging (innermost). FastAPI/Starlette applies middleware in reverse registration order.
- **R3: Configuration via Pydantic Settings v2** — `app/config.py` with `Settings(BaseSettings)` using `SIDECAR_` env prefix. Covers: general, platform API, LLM providers (Anthropic/OpenAI/Together/Groq), model tiers (copilot/batch/analysis/extraction/embedding), Redis, ARQ worker, vector store (pgvector/qdrant), transcription (whisper/deepgram), Langfuse observability, conversation memory, and cache TTLs. Singleton via `@lru_cache`.
- **R4: Dependency injection** — `app/dependencies.py` with lifespan init/close helpers and `Depends()` callables for Redis, VectorStore, PlatformClient, RequestContext, and Settings. Annotated type aliases: `RedisClient`, `VectorStoreClient`, `Platform`, `Ctx`, `AppSettings`.
- **R5: Request context** — `app/context.py` with frozen `RequestContext` dataclass carrying `tenant_id`, `actor_id`, `actor_type`, `request_id`, `conversation_id`, and `access_scope`.
- **R6: Tenant context middleware** — `app/middleware/tenant.py` extracts `X-Tenant-ID`, `X-Actor-ID`, `X-Actor-Type` (required), `X-Conversation-ID`, `X-Access-Scope` (optional) from headers. Skips health/docs paths. Returns 400 JSON on missing required headers.
- **R7: Request ID middleware** — `app/middleware/request_id.py` generates or propagates `X-Request-ID` (UUID4). Attaches to `request.state` and echoes on response.
- **R8: Structured logging middleware** — `app/middleware/logging.py` logs method, path, status, duration_ms, request_id, tenant_id, actor_id on every request.
- **R9: Access scope model** — `app/models/access_scope.py` with `AccessScope(BaseModel)` supporting `full_tenant` and `scoped` visibility modes. Includes `allows_*()` check methods and `to_vector_filter()` for vector store queries.
- **R10: Error hierarchy** — `app/errors.py` with `SidecarError` base class and subclasses: `PlatformReadError`, `PlatformTimeoutError`, `ModelProviderError`, `ModelProviderRateLimitError`, `ValidationError`, `ScopeViolationError`, `TranscriptionError`, `TranscriptionTooLongError`, `InternalError`, `RedisUnavailableError`, `VectorStoreUnavailableError`. Five error categories: `platform_read`, `model_provider`, `validation`, `transcription`, `internal`.
- **R11: Global exception handlers** — `SidecarError` handler returns structured JSON envelope `{ok, error: {code, category, message, detail, request_id}}`. Unhandled `Exception` handler returns 500 with `INTERNAL_ERROR`.
- **R12: Health endpoints** — `GET /health` (liveness, no deps) and `GET /ready` (readiness, checks Redis/vector store/platform API/LLM provider).
- **R13: Router registration** — Include routers for: health, chat, digest, email, tasks, meetings, tax, portfolio, reports, documents (all under `/ai` prefix except health).
- **R14: Cache key scoping** — `app/utils/cache.py` with `cache_key()` function namespaced by `tenant_id` and `actor_id`.
- **R15: Platform client** — `app/services/platform_client.py` with scope-enforced reads via httpx `AsyncClient`. Pre-checks access scope before network calls.
- **R16: AgentDeps bridge** — `app/agents/deps.py` with frozen `AgentDeps` dataclass bridging FastAPI DI to Pydantic AI agent `deps` parameter.
- **R17: ARQ worker** — `app/jobs/worker.py` with `WorkerSettings` class registering job functions (daily_digest, email_triage, transcription, firm_report, style_profile_refresh).
- **R18: Docker deployment** — Multi-stage Dockerfile (builder + runtime), docker-compose with sidecar-api, sidecar-worker, Redis, and pgvector services.

## Decisions Already Made

- **D1:** FastAPI with Pydantic v2 and Pydantic AI as the framework stack.
- **D2:** Python 3.12+ target.
- **D3:** Middleware ordering is fixed: CORS → RequestId → TenantContext → StructuredLogging.
- **D4:** The sidecar trusts platform-set headers for auth — no independent auth layer.
- **D5:** All config via `SIDECAR_` prefixed env vars, no file-based config at runtime.
- **D6:** No module-level singletons — all shared resources through `app.state` and `Depends()`.
- **D7:** `pgvector` is the default vector store provider; `qdrant` is an alternative.
- **D8:** ARQ (Redis-backed) for background job processing.
- **D9:** Langfuse for observability/tracing.
- **D10:** The existing app uses `app/app.py` + `app/api/routes.py` structure which will be restructured to match the spec's `app/main.py` + `app/routers/` structure.

## Constraints

- **C1:** Target directory is `apps/intelligence-layer/src/app/` — must work within the existing project layout and `pyproject.toml`.
- **C2:** Existing health endpoints (`/healthz`, `/readyz`) in `app/api/routes.py` will be superseded by the spec's `/health` and `/ready` in `app/routers/health.py`.
- **C3:** The existing `app/config.py` uses a simpler settings class that must be expanded to match the full spec.
- **C4:** No direct end-user authentication — the sidecar sits behind the platform API.
- **C5:** Access scope is never derived or escalated by the sidecar — only enforced as provided by the platform.
- **C6:** Every cache key, vector query, and platform read must be tenant+scope scoped.
- **C7:** Router stubs for feature domains (chat, digest, email, etc.) are needed even though their full implementation is in later specs.

## Open Questions

- [x] **Q1:** Replace existing `app/app.py` and `app/api/routes.py` entirely with the spec's structure.
- [x] **Q2:** Create minimal stub implementations for VectorStore, PlatformClient, Retriever with the interfaces core infrastructure needs.
- [x] **Q3:** Create empty stub modules for all job functions and router registrations so imports don't error.
- [x] **Q4:** Update `pyproject.toml` with all new dependencies needed by core infrastructure.
