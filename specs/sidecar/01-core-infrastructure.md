# 01 — Core Infrastructure: Implementation Guide

This document specifies the exact implementation of the Python sidecar's core infrastructure layer. It covers the FastAPI application shell, configuration, dependency injection, request context propagation, access scope enforcement, error handling, health checks, and deployment topology. Every code block is intended to be production-ready Python 3.12+ using FastAPI, Pydantic v2, and Pydantic AI.

---

## 1. FastAPI Application Setup

### 1.1 `app/main.py`

The application entry point wires lifespan management, middleware, routers, and the global exception handler. Middleware ordering matters: tenant context must be resolved before request-id generation, and both must be resolved before structured logging can emit scoped log lines.

```python
"""
app/main.py — FastAPI application entry point.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.dependencies import (
    close_platform_client,
    close_redis,
    close_vector_store,
    init_platform_client,
    init_redis,
    init_vector_store,
)
from app.errors import SidecarError
from app.middleware.logging import StructuredLoggingMiddleware
from app.middleware.request_id import RequestIdMiddleware
from app.middleware.tenant import TenantContextMiddleware
from app.routers import (
    chat,
    digest,
    documents,
    email,
    health,
    meetings,
    portfolio,
    reports,
    tasks,
    tax,
)

logger = logging.getLogger("sidecar")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Startup: initialize shared resources (Redis, vector store, platform client).
    Shutdown: close connections gracefully.

    Resources are stored on app.state so that dependency-injection functions
    can retrieve them without module-level singletons.
    """
    settings = get_settings()

    # --- Startup -----------------------------------------------------------
    logger.info("sidecar starting", extra={"env": settings.environment})

    app.state.settings = settings
    app.state.redis = await init_redis(settings)
    app.state.vector_store = await init_vector_store(settings)
    app.state.platform_client = await init_platform_client(settings)

    yield

    # --- Shutdown ----------------------------------------------------------
    logger.info("sidecar shutting down")

    await close_platform_client(app.state.platform_client)
    await close_vector_store(app.state.vector_store)
    await close_redis(app.state.redis)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Wealth Advisor — AI Sidecar",
        version="0.1.0",
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # ------------------------------------------------------------------
    # Middleware — order matters.
    #
    # FastAPI/Starlette applies middleware in REVERSE registration order.
    # The first middleware registered is the OUTERMOST layer (runs first
    # on the way in, last on the way out).
    #
    # Desired execution order on each request:
    #   1. CORS (outermost — must run before anything reads the body)
    #   2. RequestId (generate or forward X-Request-ID)
    #   3. TenantContext (extract X-Tenant-ID, X-Actor-ID, access scope)
    #   4. StructuredLogging (log with tenant + request id in scope)
    #
    # Therefore we register in this order (outermost first):
    # ------------------------------------------------------------------

    # 1. CORS — outermost
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 2. Request-ID propagation
    app.add_middleware(RequestIdMiddleware)

    # 3. Tenant context extraction
    app.add_middleware(TenantContextMiddleware)

    # 4. Structured logging — innermost, can read tenant + request id
    app.add_middleware(StructuredLoggingMiddleware)

    # ------------------------------------------------------------------
    # Global exception handler
    # ------------------------------------------------------------------
    @app.exception_handler(SidecarError)
    async def sidecar_error_handler(request: Request, exc: SidecarError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "ok": False,
                "error": {
                    "code": exc.error_code,
                    "category": exc.category,
                    "message": exc.message,
                    "detail": exc.detail,
                    "request_id": getattr(request.state, "request_id", None),
                },
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled exception", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "category": "internal",
                    "message": "An unexpected error occurred.",
                    "detail": None,
                    "request_id": getattr(request.state, "request_id", None),
                },
            },
        )

    # ------------------------------------------------------------------
    # Routers
    # ------------------------------------------------------------------
    app.include_router(health.router)
    app.include_router(chat.router, prefix="/ai")
    app.include_router(digest.router, prefix="/ai")
    app.include_router(email.router, prefix="/ai")
    app.include_router(tasks.router, prefix="/ai")
    app.include_router(meetings.router, prefix="/ai")
    app.include_router(tax.router, prefix="/ai")
    app.include_router(portfolio.router, prefix="/ai")
    app.include_router(reports.router, prefix="/ai")
    app.include_router(documents.router, prefix="/ai")

    return app


app = create_app()
```

### 1.2 Middleware chain — execution order rationale

The middleware stack runs in this fixed order on every inbound request:

```
Request ──> CORS ──> RequestId ──> TenantContext ──> StructuredLogging ──> Router
                                                                            │
Response <── CORS <── RequestId <── TenantContext <── StructuredLogging <────┘
```

Why this ordering:

| Layer | Reason it sits here |
|---|---|
| CORS | Must set response headers before any middleware short-circuits with 4xx. |
| RequestId | Generate or propagate a trace-level correlation ID before the tenant context object is built. |
| TenantContext | Every downstream component (logging, agents, retrieval) needs tenant_id and actor_id. It runs after RequestId so the attached request context includes the resolved request ID. |
| StructuredLogging | Innermost middleware. By the time it runs, both tenant context and request_id are on `request.state`, so every log line within the request lifecycle carries full context. |

---

## 2. Configuration

### 2.1 `app/config.py`

All configuration is loaded from environment variables using Pydantic Settings v2. No configuration is read from files at runtime. The settings object is a frozen singleton created once at import time and retrieved via `get_settings()`.

```python
"""
app/config.py — Pydantic Settings for all sidecar configuration.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    All sidecar configuration surfaces as env vars.
    Prefixed with SIDECAR_ to avoid collisions.
    """

    model_config = SettingsConfigDict(
        env_prefix="SIDECAR_",
        env_file=".env",
        env_file_encoding="utf-8",
        frozen=True,
    )

    # ── General ────────────────────────────────────────────────────────
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"
    cors_allowed_origins: list[str] = ["*"]

    # ── Platform API ───────────────────────────────────────────────────
    platform_api_url: HttpUrl = Field(
        default="http://localhost:3000",
        description="Base URL of the platform API (NestJS backend).",
    )
    platform_api_key: str = Field(
        default="",
        description="Shared secret or service token for sidecar → platform reads.",
    )
    platform_api_timeout_s: float = Field(default=30.0)

    # ── LLM Providers ─────────────────────────────────────────────────
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    together_api_key: str = ""
    groq_api_key: str = ""

    # Models per tier
    copilot_model: str = "anthropic:claude-sonnet-4-6"
    copilot_fallback_model: str = "openai:gpt-4o"
    batch_model: str = "anthropic:claude-haiku-4-5"
    batch_fallback_model: str = "together:meta-llama/Llama-3.3-70B"
    analysis_model: str = "anthropic:claude-opus-4-6"
    extraction_model: str = "anthropic:claude-haiku-4-5"
    embedding_model: str = "openai:text-embedding-3-small"

    # ── Redis ──────────────────────────────────────────────────────────
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL. Used for cache, conversation memory, and ARQ queue.",
    )
    redis_max_connections: int = 20

    # ── ARQ Worker ─────────────────────────────────────────────────────
    arq_queue_name: str = "sidecar:queue"
    arq_max_jobs: int = 10
    arq_job_timeout_s: int = 600
    arq_retry_count: int = 3

    # ── Vector Store ───────────────────────────────────────────────────
    vector_store_provider: Literal["pgvector", "qdrant"] = "pgvector"
    vector_store_url: str = Field(
        default="postgresql+asyncpg://localhost:5432/sidecar_vectors",
        description="Connection string for the vector store.",
    )
    vector_store_collection: str = "documents"
    vector_search_top_k: int = 20
    vector_rerank_top_k: int = 8

    # ── Transcription ──────────────────────────────────────────────────
    transcription_provider: Literal["whisper", "deepgram"] = "whisper"
    deepgram_api_key: str = ""
    whisper_model: str = "whisper-1"
    max_audio_duration_s: int = 7200  # 2 hours

    # ── Langfuse Observability ─────────────────────────────────────────
    langfuse_enabled: bool = True
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # ── Conversation Memory ────────────────────────────────────────────
    conversation_ttl_s: int = 7200  # 2 hours
    conversation_max_messages: int = 50

    # ── Cache TTLs ─────────────────────────────────────────────────────
    style_profile_ttl_s: int = 604800  # 7 days
    digest_cache_ttl_s: int = 86400  # 1 day

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton settings instance."""
    return Settings()
```

### 2.2 Environment variable reference

Every setting maps to a `SIDECAR_` prefixed env var. For example:

```bash
# .env.example
SIDECAR_ENVIRONMENT=production
SIDECAR_PLATFORM_API_URL=http://platform-api:3000
SIDECAR_PLATFORM_API_KEY=sk-platform-xxxxx
SIDECAR_ANTHROPIC_API_KEY=sk-ant-xxxxx
SIDECAR_OPENAI_API_KEY=sk-xxxxx
SIDECAR_REDIS_URL=redis://redis:6379/0
SIDECAR_VECTOR_STORE_URL=postgresql+asyncpg://user:pass@pgvector:5432/vectors
SIDECAR_LANGFUSE_PUBLIC_KEY=pk-lf-xxxxx
SIDECAR_LANGFUSE_SECRET_KEY=sk-lf-xxxxx
SIDECAR_TRANSCRIPTION_PROVIDER=deepgram
SIDECAR_DEEPGRAM_API_KEY=xxxxx
```

---

## 3. Dependency Injection

### 3.1 `app/dependencies.py`

FastAPI's `Depends()` system is the sole mechanism for injecting shared resources into route handlers. No module-level singletons, no global state. Every dependency is derived from `request.app.state` (populated during lifespan) or from request-scoped context.

```python
"""
app/dependencies.py — Dependency injection wiring for FastAPI.
"""
from __future__ import annotations

import logging
from typing import Annotated, AsyncIterator

import httpx
from fastapi import Depends, Request
from redis.asyncio import ConnectionPool, Redis

from app.config import Settings, get_settings
from app.context import RequestContext
from app.services.platform_client import PlatformClient
from app.services.vector_store import VectorStore

logger = logging.getLogger("sidecar.deps")


# ---------------------------------------------------------------------------
# Lifespan init/close helpers (called from main.py lifespan)
# ---------------------------------------------------------------------------

async def init_redis(settings: Settings) -> Redis:
    pool = ConnectionPool.from_url(
        settings.redis_url,
        max_connections=settings.redis_max_connections,
        decode_responses=True,
    )
    client = Redis(connection_pool=pool)
    await client.ping()
    logger.info("redis connected", extra={"url": settings.redis_url})
    return client


async def close_redis(redis: Redis) -> None:
    await redis.aclose()
    logger.info("redis closed")


async def init_vector_store(settings: Settings) -> VectorStore:
    store = VectorStore(
        provider=settings.vector_store_provider,
        url=settings.vector_store_url,
        collection=settings.vector_store_collection,
    )
    await store.connect()
    logger.info("vector store connected", extra={"provider": settings.vector_store_provider})
    return store


async def close_vector_store(store: VectorStore) -> None:
    await store.disconnect()
    logger.info("vector store closed")


async def init_platform_client(settings: Settings) -> PlatformClient:
    http = httpx.AsyncClient(
        base_url=str(settings.platform_api_url),
        headers={"Authorization": f"Bearer {settings.platform_api_key}"},
        timeout=httpx.Timeout(settings.platform_api_timeout_s),
    )
    client = PlatformClient(http=http)
    logger.info("platform client initialized", extra={"url": str(settings.platform_api_url)})
    return client


async def close_platform_client(client: PlatformClient) -> None:
    await client.close()
    logger.info("platform client closed")


# ---------------------------------------------------------------------------
# FastAPI Depends() callables
# ---------------------------------------------------------------------------

def get_redis(request: Request) -> Redis:
    """Retrieve the shared Redis client from app state."""
    return request.app.state.redis


def get_vector_store(request: Request) -> VectorStore:
    """Retrieve the shared vector store from app state."""
    return request.app.state.vector_store


def get_platform_client(request: Request) -> PlatformClient:
    """Retrieve the shared platform API client from app state."""
    return request.app.state.platform_client


def get_request_context(request: Request) -> RequestContext:
    """
    Retrieve the RequestContext attached by TenantContextMiddleware.
    Raises 500 if middleware did not run (indicates a configuration bug).
    """
    ctx: RequestContext | None = getattr(request.state, "context", None)
    if ctx is None:
        raise RuntimeError("RequestContext not found — TenantContextMiddleware may not be installed.")
    return ctx


# ---------------------------------------------------------------------------
# Annotated type aliases for cleaner route signatures
# ---------------------------------------------------------------------------

RedisClient = Annotated[Redis, Depends(get_redis)]
VectorStoreClient = Annotated[VectorStore, Depends(get_vector_store)]
Platform = Annotated[PlatformClient, Depends(get_platform_client)]
Ctx = Annotated[RequestContext, Depends(get_request_context)]
AppSettings = Annotated[Settings, Depends(get_settings)]
```

### 3.2 Usage in route handlers

Route handlers declare their dependencies using the annotated type aliases. No handler ever imports a global resource directly.

```python
"""
app/routers/chat.py — example route using dependency injection.
"""
from fastapi import APIRouter

from app.dependencies import Ctx, Platform, RedisClient, VectorStoreClient

router = APIRouter(tags=["chat"])


@router.post("/chat")
async def chat(
    ctx: Ctx,
    platform: Platform,
    redis: RedisClient,
    vector_store: VectorStoreClient,
):
    # ctx.tenant_id, ctx.actor_id, ctx.access_scope are all available
    # platform, redis, vector_store are shared instances from app.state
    ...
```

### 3.3 Injecting agents into routes

Pydantic AI agents are stateless objects — they hold configuration but no per-request state. They are instantiated at module level and used inside route handlers. Per-request context (tenant, actor, access scope) is passed through the agent's `deps` parameter at call time, not through DI.

```python
from pydantic_ai import Agent

from app.agents.deps import AgentDeps
from app.config import get_settings

settings = get_settings()

copilot_agent = Agent(
    model=settings.copilot_model,
    fallback_model=settings.copilot_fallback_model,
    result_type=HazelCopilot,
    system_prompt="...",
    tools=[...],
)

# In the route handler:
async def chat(ctx: Ctx, platform: Platform, redis: RedisClient, ...):
    deps = AgentDeps(
        context=ctx,
        platform=platform,
        redis=redis,
    )
    result = await copilot_agent.run(user_message, deps=deps)
    return result.data
```

---

## 4. Request Context

### 4.1 `app/context.py`

The `RequestContext` is a frozen dataclass that carries identity and scope information for a single request. It is constructed by the `TenantContextMiddleware` and attached to `request.state`. Downstream code receives it via `Depends(get_request_context)`.

```python
"""
app/context.py — Per-request context propagated through the sidecar.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.models.access_scope import AccessScope


@dataclass(frozen=True, slots=True)
class RequestContext:
    """
    Immutable context for a single sidecar request.

    Populated by TenantContextMiddleware from inbound HTTP headers.
    Every downstream component (agents, platform client, retrieval,
    cache, logging) reads from this object — never from raw headers.
    """

    tenant_id: str
    """Firm-level isolation boundary. Always required."""

    actor_id: str
    """The user (advisor, admin, system) making the request."""

    actor_type: Literal["advisor", "admin", "service"]
    """Role of the actor. Determines some default scope behaviour."""

    request_id: str
    """Trace-level correlation ID (UUID). Generated or forwarded."""

    conversation_id: str | None = None
    """Set for multi-turn chat sessions. None for single-shot endpoints."""

    access_scope: AccessScope | None = None
    """
    Structured visibility scope provided by the platform.
    Determines which households, clients, accounts, and documents
    the actor may access in this request.
    """
```

### 4.2 `app/middleware/tenant.py`

The tenant middleware extracts identity and scope from headers set by the platform API server. The platform is responsible for authentication and authorization — the sidecar trusts these headers because requests should only arrive from the platform, not directly from end users.

```python
"""
app/middleware/tenant.py — Extract tenant context from platform-set headers.
"""
from __future__ import annotations

import json
import logging

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.context import RequestContext
from app.models.access_scope import AccessScope

logger = logging.getLogger("sidecar.middleware.tenant")

# Health endpoints bypass tenant context extraction.
_SKIP_PATHS: set[str] = {"/health", "/ready", "/docs", "/openapi.json"}


class TenantContextMiddleware(BaseHTTPMiddleware):
    """
    Reads identity and access-scope headers injected by the platform API.

    Required headers (except on skip paths):
        X-Tenant-ID     — firm identifier
        X-Actor-ID      — user identifier
        X-Actor-Type    — "advisor" | "admin" | "system"

    Optional headers:
        X-Conversation-ID   — multi-turn session identifier
        X-Access-Scope      — JSON-encoded AccessScope object
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.url.path in _SKIP_PATHS:
            return await call_next(request)

        tenant_id = request.headers.get("x-tenant-id")
        actor_id = request.headers.get("x-actor-id")
        actor_type = request.headers.get("x-actor-type")

        if not tenant_id or not actor_id or not actor_type:
            logger.warning(
                "missing required tenant headers",
                extra={
                    "path": request.url.path,
                    "has_tenant": bool(tenant_id),
                    "has_actor": bool(actor_id),
                },
            )
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": {
                        "code": "MISSING_CONTEXT",
                        "category": "validation",
                        "message": "X-Tenant-ID, X-Actor-ID, and X-Actor-Type headers are required.",
                        "detail": None,
                        "request_id": None,
                    },
                },
            )

        # Parse optional access scope
        access_scope: AccessScope | None = None
        raw_scope = request.headers.get("x-access-scope")
        if raw_scope:
            try:
                access_scope = AccessScope.model_validate_json(raw_scope)
            except Exception:
                logger.warning("invalid X-Access-Scope header, ignoring", extra={"raw": raw_scope[:200]})

        ctx = RequestContext(
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_type=actor_type,
            request_id=getattr(request.state, "request_id", "unknown"),
            conversation_id=request.headers.get("x-conversation-id"),
            access_scope=access_scope,
        )

        request.state.context = ctx

        logger.debug(
            "tenant context attached",
            extra={"tenant_id": ctx.tenant_id, "actor_id": ctx.actor_id},
        )

        return await call_next(request)
```

### 4.3 `app/middleware/request_id.py`

```python
"""
app/middleware/request_id.py — Generate or propagate X-Request-ID.
"""
from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class RequestIdMiddleware(BaseHTTPMiddleware):
    """
    If the inbound request carries X-Request-ID, propagate it.
    Otherwise generate a new UUID4. Attach to request.state and
    echo back on the response.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
```

### 4.4 `app/middleware/logging.py`

```python
"""
app/middleware/logging.py — Structured request/response logging.
"""
from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("sidecar.http")


class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    """
    Logs every request with tenant, actor, request_id, method, path,
    status code, and duration. Uses structlog-compatible extra fields.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        start = time.perf_counter()

        response = await call_next(request)

        duration_ms = (time.perf_counter() - start) * 1000

        ctx = getattr(request.state, "context", None)
        logger.info(
            "http request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round(duration_ms, 2),
                "request_id": getattr(request.state, "request_id", None),
                "tenant_id": ctx.tenant_id if ctx else None,
                "actor_id": ctx.actor_id if ctx else None,
            },
        )

        return response
```

---

## 5. Access Scope

### 5.1 `app/models/access_scope.py`

The `AccessScope` model is the structured representation of what the actor is permitted to see within their tenant. It is computed by the platform and delivered to the sidecar as a JSON header. The sidecar never derives or escalates scope — it only enforces what the platform provides.

```python
"""
app/models/access_scope.py — Structured access scope for retrieval filtering.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AccessScope(BaseModel):
    """
    Defines the visibility boundary for a single request.

    The platform computes this from the actor's role, team assignments,
    and any explicit sharing rules. The sidecar treats it as immutable
    truth for the duration of the request.
    """

    visibility_mode: Literal["full_tenant", "scoped"] = Field(
        description=(
            "'full_tenant' — actor can see all data in the tenant (e.g., firm admin). "
            "'scoped' — actor can only see the resource sets listed below."
        ),
    )

    household_ids: list[str] = Field(default_factory=list)
    client_ids: list[str] = Field(default_factory=list)
    account_ids: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    advisor_ids: list[str] = Field(default_factory=list)

    def allows_household(self, household_id: str) -> bool:
        if self.visibility_mode == "full_tenant":
            return True
        return household_id in self.household_ids

    def allows_client(self, client_id: str) -> bool:
        if self.visibility_mode == "full_tenant":
            return True
        return client_id in self.client_ids

    def allows_account(self, account_id: str) -> bool:
        if self.visibility_mode == "full_tenant":
            return True
        return account_id in self.account_ids

    def allows_document(self, document_id: str) -> bool:
        if self.visibility_mode == "full_tenant":
            return True
        return document_id in self.document_ids

    def to_vector_filter(self, tenant_id: str) -> dict:
        """
        Build a metadata filter dict for vector store queries.
        Always includes tenant_id for hard isolation.
        Adds resource-level filters when scope is not full_tenant.
        """
        base: dict = {"tenant_id": tenant_id}

        if self.visibility_mode == "full_tenant":
            return base

        # Build an OR filter across all allowed resource IDs.
        # The vector store adapter translates this into its native
        # filter syntax (pgvector WHERE clause, Qdrant filter, etc.)
        allowed: dict[str, list[str]] = {}
        if self.household_ids:
            allowed["household_id"] = self.household_ids
        if self.client_ids:
            allowed["client_id"] = self.client_ids
        if self.account_ids:
            allowed["account_id"] = self.account_ids
        if self.advisor_ids:
            allowed["advisor_id"] = self.advisor_ids

        if allowed:
            base["_or"] = allowed

        return base
```

### 5.2 Scope enforcement in retrieval

Every read path — platform client, vector store, Redis cache — must receive and apply the access scope. The scope is not optional.

```python
# In app/services/platform_client.py
class PlatformClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def get_household_summary(
        self, household_id: str, access_scope: AccessScope
    ) -> HouseholdSummary:
        """
        Fetch a household summary. The platform API also enforces scope,
        but we pre-check here to fail fast and avoid unnecessary network calls.
        """
        if not access_scope.allows_household(household_id):
            raise ScopeViolationError(
                resource_type="household",
                resource_id=household_id,
            )
        resp = await self._http.get(
            f"/api/v1/households/{household_id}/summary",
            headers={"X-Access-Scope": access_scope.model_dump_json()},
        )
        resp.raise_for_status()
        return HouseholdSummary.model_validate(resp.json())

    async def close(self) -> None:
        await self._http.aclose()
```

```python
# In app/rag/retriever.py
class Retriever:
    def __init__(self, vector_store: VectorStore) -> None:
        self._store = vector_store

    async def search(
        self,
        query: str,
        tenant_id: str,
        access_scope: AccessScope,
        top_k: int = 20,
    ) -> list[RetrievedChunk]:
        """
        Vector search with mandatory tenant + scope filtering.
        Scope filters are applied at the storage layer BEFORE
        results are returned — never post-hoc.
        """
        metadata_filter = access_scope.to_vector_filter(tenant_id)
        return await self._store.similarity_search(
            query=query,
            filter=metadata_filter,
            limit=top_k,
        )
```

### 5.3 Cache key scoping

Every cache read and write is namespaced by tenant and actor to prevent cross-tenant or cross-advisor leakage.

```python
# In app/utils/cache.py
def cache_key(namespace: str, tenant_id: str, actor_id: str, *parts: str) -> str:
    """
    Build a scoped Redis cache key.

    Examples:
        cache_key("chat", "t_1", "a_1", "conv_xyz")
            → "chat:t_1:a_1:conv_xyz"
        cache_key("digest", "t_1", "a_1", "2026-03-26")
            → "digest:t_1:a_1:2026-03-26"
        cache_key("style_profile", "t_1", "a_1")
            → "style_profile:t_1:a_1"
    """
    segments = [namespace, tenant_id, actor_id, *parts]
    return ":".join(segments)
```

---

## 6. Error Handling

### 6.1 `app/errors.py`

All sidecar errors derive from `SidecarError`. Each error carries a machine-readable `error_code`, a `category` for failure classification, an HTTP status code, a human-readable message, and optional detail.

```python
"""
app/errors.py — Sidecar error hierarchy and classification.
"""
from __future__ import annotations

from typing import Any, Literal


ErrorCategory = Literal[
    "platform_read",
    "model_provider",
    "validation",
    "transcription",
    "internal",
]


class SidecarError(Exception):
    """Base error for all sidecar failures."""

    def __init__(
        self,
        *,
        error_code: str,
        category: ErrorCategory,
        status_code: int = 500,
        message: str,
        detail: Any = None,
    ) -> None:
        self.error_code = error_code
        self.category = category
        self.status_code = status_code
        self.message = message
        self.detail = detail
        super().__init__(message)


# ---------------------------------------------------------------------------
# Platform read failures
# ---------------------------------------------------------------------------

class PlatformReadError(SidecarError):
    """The sidecar failed to read data from the platform API."""

    def __init__(self, resource: str, status: int | None = None, detail: Any = None) -> None:
        super().__init__(
            error_code="PLATFORM_READ_FAILED",
            category="platform_read",
            status_code=502,
            message=f"Failed to read {resource} from platform API.",
            detail={"resource": resource, "upstream_status": status, **(detail or {})},
        )


class PlatformTimeoutError(SidecarError):
    """Platform API read timed out."""

    def __init__(self, resource: str) -> None:
        super().__init__(
            error_code="PLATFORM_READ_TIMEOUT",
            category="platform_read",
            status_code=504,
            message=f"Platform API timed out reading {resource}.",
            detail={"resource": resource},
        )


# ---------------------------------------------------------------------------
# Model provider failures
# ---------------------------------------------------------------------------

class ModelProviderError(SidecarError):
    """An LLM, embedding, or reranking model provider failed."""

    def __init__(self, provider: str, detail: Any = None) -> None:
        super().__init__(
            error_code="MODEL_PROVIDER_FAILED",
            category="model_provider",
            status_code=502,
            message=f"Model provider '{provider}' returned an error.",
            detail={"provider": provider, **(detail or {})},
        )


class ModelProviderRateLimitError(SidecarError):
    """Model provider rate-limited the request."""

    def __init__(self, provider: str, retry_after: float | None = None) -> None:
        super().__init__(
            error_code="MODEL_PROVIDER_RATE_LIMITED",
            category="model_provider",
            status_code=429,
            message=f"Model provider '{provider}' rate-limited the request.",
            detail={"provider": provider, "retry_after_s": retry_after},
        )


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------

class ValidationError(SidecarError):
    """Request payload or header validation failed."""

    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__(
            error_code="VALIDATION_FAILED",
            category="validation",
            status_code=422,
            message=message,
            detail=detail,
        )


class ScopeViolationError(SidecarError):
    """The actor attempted to access a resource outside their access scope."""

    def __init__(self, resource_type: str, resource_id: str) -> None:
        super().__init__(
            error_code="SCOPE_VIOLATION",
            category="validation",
            status_code=403,
            message=f"Access denied: {resource_type} '{resource_id}' is outside the provided access scope.",
            detail={"resource_type": resource_type, "resource_id": resource_id},
        )


# ---------------------------------------------------------------------------
# Transcription failures
# ---------------------------------------------------------------------------

class TranscriptionError(SidecarError):
    """Audio transcription failed."""

    def __init__(self, provider: str, detail: Any = None) -> None:
        super().__init__(
            error_code="TRANSCRIPTION_FAILED",
            category="transcription",
            status_code=502,
            message=f"Transcription via '{provider}' failed.",
            detail={"provider": provider, **(detail or {})},
        )


class TranscriptionTooLongError(SidecarError):
    """Audio exceeds the maximum allowed duration."""

    def __init__(self, duration_s: float, max_s: int) -> None:
        super().__init__(
            error_code="TRANSCRIPTION_TOO_LONG",
            category="transcription",
            status_code=422,
            message=f"Audio duration ({duration_s}s) exceeds maximum ({max_s}s).",
            detail={"duration_s": duration_s, "max_s": max_s},
        )


# ---------------------------------------------------------------------------
# Internal failures
# ---------------------------------------------------------------------------

class InternalError(SidecarError):
    """Catch-all for unexpected internal failures."""

    def __init__(self, message: str = "An internal error occurred.", detail: Any = None) -> None:
        super().__init__(
            error_code="INTERNAL_ERROR",
            category="internal",
            status_code=500,
            message=message,
            detail=detail,
        )


class RedisUnavailableError(SidecarError):
    """Redis is unreachable."""

    def __init__(self) -> None:
        super().__init__(
            error_code="REDIS_UNAVAILABLE",
            category="internal",
            status_code=503,
            message="Redis is unavailable.",
        )


class VectorStoreUnavailableError(SidecarError):
    """Vector store is unreachable."""

    def __init__(self) -> None:
        super().__init__(
            error_code="VECTOR_STORE_UNAVAILABLE",
            category="internal",
            status_code=503,
            message="Vector store is unavailable.",
        )
```

### 6.2 Error response envelope

Every error response — whether from `SidecarError` or unhandled exceptions — uses the same JSON envelope. This is enforced by the exception handlers in `main.py`.

```json
{
  "ok": false,
  "error": {
    "code": "PLATFORM_READ_FAILED",
    "category": "platform_read",
    "message": "Failed to read household from platform API.",
    "detail": {
      "resource": "household",
      "upstream_status": 503
    },
    "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
  }
}
```

The five failure categories map directly to operational dashboards:

| Category | Meaning | Typical HTTP code | Alerting posture |
|---|---|---|---|
| `platform_read` | Platform API returned an error or timed out | 502, 504 | Alert if sustained; likely a platform outage |
| `model_provider` | LLM, embedding, or reranking provider failed | 429, 502 | Alert on rate limits; trigger fallback chain |
| `validation` | Bad request payload or scope violation | 403, 422 | Do not alert; caller error |
| `transcription` | Audio transcription provider failed | 422, 502 | Alert if sustained |
| `internal` | Bug or infrastructure failure inside the sidecar | 500, 503 | Always alert |

---

## 7. Health and Readiness

### 7.1 `app/routers/health.py`

Two endpoints: `/health` for liveness (is the process alive?) and `/ready` for readiness (can the process serve traffic?). The readiness probe checks all critical dependencies.

```python
"""
app/routers/health.py — Liveness and readiness probes.
"""
from __future__ import annotations

import logging
import time

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("sidecar.health")

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    """
    Liveness probe.
    Returns 200 if the process is alive. No dependency checks.
    Used by container orchestrators to decide whether to restart the process.
    """
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    """
    Readiness probe.
    Checks Redis, vector store, and platform API reachability.
    Returns 200 only if ALL dependencies are healthy.
    Returns 503 with a breakdown if any dependency is unhealthy.
    Used by load balancers to decide whether to route traffic to this instance.
    """
    checks: dict[str, dict] = {}

    # --- Redis ---
    checks["redis"] = await _check_redis(request)

    # --- Vector store ---
    checks["vector_store"] = await _check_vector_store(request)

    # --- Platform API ---
    checks["platform_api"] = await _check_platform_api(request)

    # --- LLM provider (lightweight, optional) ---
    checks["llm_provider"] = await _check_llm_provider(request)

    all_healthy = all(c["status"] == "ok" for c in checks.values())
    status_code = 200 if all_healthy else 503

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if all_healthy else "degraded",
            "checks": checks,
        },
    )


async def _check_redis(request: Request) -> dict:
    try:
        start = time.perf_counter()
        await request.app.state.redis.ping()
        latency_ms = (time.perf_counter() - start) * 1000
        return {"status": "ok", "latency_ms": round(latency_ms, 2)}
    except Exception as exc:
        logger.warning("redis health check failed", exc_info=exc)
        return {"status": "error", "error": str(exc)}


async def _check_vector_store(request: Request) -> dict:
    try:
        start = time.perf_counter()
        await request.app.state.vector_store.health_check()
        latency_ms = (time.perf_counter() - start) * 1000
        return {"status": "ok", "latency_ms": round(latency_ms, 2)}
    except Exception as exc:
        logger.warning("vector store health check failed", exc_info=exc)
        return {"status": "error", "error": str(exc)}


async def _check_platform_api(request: Request) -> dict:
    try:
        start = time.perf_counter()
        resp = await request.app.state.platform_client._http.get("/health")
        latency_ms = (time.perf_counter() - start) * 1000
        if resp.status_code == 200:
            return {"status": "ok", "latency_ms": round(latency_ms, 2)}
        return {"status": "error", "error": f"HTTP {resp.status_code}"}
    except Exception as exc:
        logger.warning("platform API health check failed", exc_info=exc)
        return {"status": "error", "error": str(exc)}


async def _check_llm_provider(request: Request) -> dict:
    """
    Lightweight check: verify the primary LLM provider API key is set
    and (optionally) that the provider endpoint is reachable.
    This is not a full inference test — just a connectivity check.
    """
    settings = request.app.state.settings
    if not settings.anthropic_api_key and not settings.openai_api_key:
        return {"status": "error", "error": "No LLM provider API key configured."}

    # Optionally ping the provider's models endpoint
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            if settings.anthropic_api_key:
                resp = await client.get(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": settings.anthropic_api_key,
                        "anthropic-version": "2023-06-01",
                    },
                )
                # A 401/405 means the endpoint is reachable (key works but
                # GET is not allowed — that's fine for a health check).
                # Only network errors or 5xx indicate real problems.
                if resp.status_code < 500:
                    return {"status": "ok"}
                return {"status": "error", "error": f"HTTP {resp.status_code}"}
    except Exception as exc:
        logger.warning("LLM provider health check failed", exc_info=exc)
        return {"status": "error", "error": str(exc)}

    return {"status": "ok"}
```

### 7.2 Probe configuration for Kubernetes / Docker

```yaml
# In a Kubernetes deployment spec:
livenessProbe:
  httpGet:
    path: /health
    port: 8000
  initialDelaySeconds: 5
  periodSeconds: 10
  failureThreshold: 3

readinessProbe:
  httpGet:
    path: /ready
    port: 8000
  initialDelaySeconds: 10
  periodSeconds: 15
  failureThreshold: 2
```

---

## 8. Docker and Deployment

### 8.1 Dockerfile

A single multi-stage Dockerfile produces the image for both `sidecar-api` and `sidecar-worker`. The entrypoint command determines which process starts.

```dockerfile
# ---------- build stage ----------
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefix=/install .

# ---------- runtime stage ----------
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY app/ ./app/

# Non-root user
RUN groupadd -r sidecar && useradd -r -g sidecar sidecar
USER sidecar

# Default: run the API server
# Override with CMD to run the worker instead.
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

### 8.2 `docker-compose.yml`

The compose file defines the two sidecar processes (API and worker), Redis, and pgvector. The platform API (NestJS) is assumed to be running separately or in its own compose stack.

```yaml
version: "3.9"

services:
  # ── Sidecar API ──────────────────────────────────────────────────
  sidecar-api:
    build:
      context: ./sidecar
      dockerfile: Dockerfile
    command:
      - uvicorn
      - app.main:app
      - --host=0.0.0.0
      - --port=8000
      - --workers=4
      - --log-level=info
    ports:
      - "8000:8000"
    environment:
      SIDECAR_ENVIRONMENT: development
      SIDECAR_REDIS_URL: redis://redis:6379/0
      SIDECAR_VECTOR_STORE_URL: postgresql+asyncpg://sidecar:sidecar@pgvector:5432/sidecar_vectors
      SIDECAR_PLATFORM_API_URL: http://host.docker.internal:3000
      SIDECAR_PLATFORM_API_KEY: ${PLATFORM_API_KEY}
      SIDECAR_ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      SIDECAR_OPENAI_API_KEY: ${OPENAI_API_KEY}
      SIDECAR_LANGFUSE_PUBLIC_KEY: ${LANGFUSE_PUBLIC_KEY}
      SIDECAR_LANGFUSE_SECRET_KEY: ${LANGFUSE_SECRET_KEY}
    depends_on:
      redis:
        condition: service_healthy
      pgvector:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 15s

  # ── Sidecar Worker ───────────────────────────────────────────────
  sidecar-worker:
    build:
      context: ./sidecar
      dockerfile: Dockerfile
    command:
      - python
      - -m
      - app.jobs.worker
    environment:
      SIDECAR_ENVIRONMENT: development
      SIDECAR_REDIS_URL: redis://redis:6379/0
      SIDECAR_VECTOR_STORE_URL: postgresql+asyncpg://sidecar:sidecar@pgvector:5432/sidecar_vectors
      SIDECAR_PLATFORM_API_URL: http://host.docker.internal:3000
      SIDECAR_PLATFORM_API_KEY: ${PLATFORM_API_KEY}
      SIDECAR_ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      SIDECAR_OPENAI_API_KEY: ${OPENAI_API_KEY}
      SIDECAR_LANGFUSE_PUBLIC_KEY: ${LANGFUSE_PUBLIC_KEY}
      SIDECAR_LANGFUSE_SECRET_KEY: ${LANGFUSE_SECRET_KEY}
    depends_on:
      redis:
        condition: service_healthy
      pgvector:
        condition: service_healthy

  # ── Redis ────────────────────────────────────────────────────────
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  # ── pgvector ─────────────────────────────────────────────────────
  pgvector:
    image: pgvector/pgvector:pg16
    ports:
      - "5433:5432"
    environment:
      POSTGRES_USER: sidecar
      POSTGRES_PASSWORD: sidecar
      POSTGRES_DB: sidecar_vectors
    volumes:
      - pgvector-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U sidecar"]
      interval: 5s
      timeout: 3s
      retries: 5

volumes:
  redis-data:
  pgvector-data:
```

### 8.3 `sidecar-api` vs `sidecar-worker` — process split

The same Docker image runs both processes. The difference is the entrypoint command.

| Process | Command | Responsibility |
|---|---|---|
| `sidecar-api` | `uvicorn app.main:app --workers 4` | Serves FastAPI routes. Handles synchronous and streaming requests. Must respond within seconds. Scales on request concurrency. |
| `sidecar-worker` | `python -m app.jobs.worker` | Runs the ARQ worker loop. Executes background jobs (digest generation, transcription, firm-wide reports, style profile refresh, RAG indexing). Jobs may run for minutes. Scales on queue depth. |

Both processes share the same `Settings`, the same Redis instance, and the same vector store. They do not share in-process memory.

### 8.4 `app/jobs/worker.py`

```python
"""
app/jobs/worker.py — ARQ worker entry point.
"""
from __future__ import annotations

from arq import func
from arq.connections import RedisSettings

from app.config import get_settings
from app.jobs.daily_digest import run_daily_digest
from app.jobs.email_triage import run_email_triage
from app.jobs.firm_report import run_firm_report
from app.jobs.style_profile import run_style_profile_refresh
from app.jobs.transcription import run_transcription


def get_redis_settings() -> RedisSettings:
    settings = get_settings()
    return RedisSettings.from_dsn(settings.redis_url)


class WorkerSettings:
    """ARQ worker configuration."""

    redis_settings = get_redis_settings()
    queue_name = get_settings().arq_queue_name
    max_jobs = get_settings().arq_max_jobs
    job_timeout = get_settings().arq_job_timeout_s

    functions = [
        func(run_daily_digest, name="daily_digest"),
        func(run_email_triage, name="email_triage"),
        func(run_transcription, name="transcription"),
        func(run_firm_report, name="firm_report"),
        func(run_style_profile_refresh, name="style_profile_refresh"),
    ]

    # Cron jobs
    cron_jobs = [
        # Daily digest: runs at 6:00 AM UTC (per-advisor scheduling
        # is handled inside the job itself)
        # cron(run_daily_digest, hour=6, minute=0),
    ]
```

### 8.5 Enqueuing jobs from the API process

The API process enqueues work to ARQ via the shared Redis connection:

```python
"""
Example: enqueuing a transcription job from a route handler.
"""
from arq.connections import ArqRedis

from app.config import get_settings


async def enqueue_transcription(
    redis: ArqRedis,
    *,
    tenant_id: str,
    actor_id: str,
    meeting_id: str,
    audio_url: str,
    access_scope_json: str,
) -> str:
    settings = get_settings()
    job = await redis.enqueue_job(
        "transcription",
        tenant_id=tenant_id,
        actor_id=actor_id,
        meeting_id=meeting_id,
        audio_url=audio_url,
        access_scope_json=access_scope_json,
        _queue_name=settings.arq_queue_name,
    )
    return job.job_id
```

The route handler returns HTTP 202 with the job ID so the platform can poll for completion:

```python
@router.post("/meetings/transcribe", status_code=202)
async def transcribe_meeting(
    body: TranscribeRequest,
    ctx: Ctx,
    redis: RedisClient,
) -> dict:
    job_id = await enqueue_transcription(
        redis,
        tenant_id=ctx.tenant_id,
        actor_id=ctx.actor_id,
        meeting_id=body.meeting_id,
        audio_url=body.audio_url,
        access_scope_json=ctx.access_scope.model_dump_json() if ctx.access_scope else "{}",
    )
    return {"job_id": job_id, "status": "accepted"}
```

---

## Appendix A: `AgentDeps` — Bridging DI and Pydantic AI

Pydantic AI agents receive a typed `deps` object that provides all read capabilities for tool functions. This is the bridge between FastAPI's dependency injection (which provides the shared clients) and the agent framework.

```python
"""
app/agents/deps.py — Shared dependency container for Pydantic AI agents.
"""
from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from app.context import RequestContext
from app.rag.retriever import Retriever
from app.services.platform_client import PlatformClient


@dataclass(frozen=True, slots=True)
class AgentDeps:
    """
    Immutable dependency bundle passed to every Pydantic AI agent run.

    Contains the request context (tenant, actor, scope) and the shared
    clients needed by agent tool functions.
    """

    context: RequestContext
    platform: PlatformClient
    redis: Redis
    retriever: Retriever | None = None

    @property
    def tenant_id(self) -> str:
        return self.context.tenant_id

    @property
    def access_scope(self):
        return self.context.access_scope
```

Agent tool functions receive `AgentDeps` as their first argument and use it to make scoped reads:

```python
from pydantic_ai import RunContext

from app.agents.deps import AgentDeps


async def search_documents(
    ctx: RunContext[AgentDeps],
    query: str,
    max_results: int = 5,
) -> list[dict]:
    """Search documents within the actor's access scope."""
    deps = ctx.deps
    if deps.retriever is None:
        return []

    chunks = await deps.retriever.search(
        query=query,
        tenant_id=deps.tenant_id,
        access_scope=deps.access_scope,
        top_k=max_results,
    )
    return [
        {"source_id": c.source_id, "title": c.title, "excerpt": c.text, "score": c.score}
        for c in chunks
    ]
```

---

## Appendix B: File inventory

For reference, the files described in this document and their locations within the `sidecar/` project root:

```
sidecar/
├── app/
│   ├── main.py                    # Section 1 — application factory, lifespan, middleware, exception handlers
│   ├── config.py                  # Section 2 — Pydantic Settings with all env vars
│   ├── context.py                 # Section 4 — RequestContext dataclass
│   ├── dependencies.py            # Section 3 — DI wiring, init/close helpers, Depends() callables
│   ├── errors.py                  # Section 6 — SidecarError hierarchy
│   ├── middleware/
│   │   ├── tenant.py              # Section 4.2 — tenant/actor/scope extraction
│   │   ├── request_id.py          # Section 4.3 — X-Request-ID propagation
│   │   └── logging.py             # Section 4.4 — structured request logging
│   ├── models/
│   │   └── access_scope.py        # Section 5 — AccessScope model and filter helpers
│   ├── routers/
│   │   └── health.py              # Section 7 — /health, /ready endpoints
│   ├── agents/
│   │   └── deps.py                # Appendix A — AgentDeps bridge
│   ├── jobs/
│   │   └── worker.py              # Section 8.4 — ARQ worker entry point
│   ├── services/
│   │   └── platform_client.py     # Section 5.2 — scoped platform reads
│   ├── rag/
│   │   └── retriever.py           # Section 5.2 — scoped vector search
│   └── utils/
│       └── cache.py               # Section 5.3 — scoped cache key builder
├── Dockerfile                     # Section 8.1
├── docker-compose.yml             # Section 8.2
└── pyproject.toml
```
