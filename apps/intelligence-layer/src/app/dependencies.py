"""
app/dependencies.py — Dependency injection wiring for FastAPI.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import ConnectionPool, Redis

from app.config import Settings, get_settings
from app.context import RequestContext
from app.services.platform_client import (
    PlatformClient,
    PlatformClientConfig,
)
from app.services.request_cache import RequestScopedCache
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
    config = PlatformClientConfig(
        base_url=str(settings.platform_api_url),
        service_token=settings.platform_api_key,
        timeout_s=settings.platform_api_timeout_s,
    )
    client = PlatformClient(config)
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


def get_request_cache() -> RequestScopedCache:
    """Fresh cache per request. Garbage collected when request ends."""
    return RequestScopedCache()


def get_platform_client(request: Request) -> PlatformClient:
    """Retrieve the shared platform API client from app state."""
    return request.app.state.platform_client


def get_langfuse(request: Request):
    """Retrieve the shared Langfuse client from app state."""
    from langfuse import Langfuse

    lf = getattr(request.app.state, "langfuse", None)
    if lf is None:
        lf = Langfuse()
        request.app.state.langfuse = lf
    return lf


def get_request_context(request: Request) -> RequestContext:
    """
    Retrieve the RequestContext attached by TenantContextMiddleware.
    Raises 500 if middleware did not run (indicates a configuration bug).
    """
    ctx: RequestContext | None = getattr(request.state, "context", None)
    if ctx is None:
        raise RuntimeError(
            "RequestContext not found — TenantContextMiddleware may not be installed."
        )
    return ctx


# ---------------------------------------------------------------------------
# Annotated type aliases for cleaner route signatures
# ---------------------------------------------------------------------------

RedisClient = Annotated[Redis, Depends(get_redis)]
VectorStoreClient = Annotated[VectorStore, Depends(get_vector_store)]
Platform = Annotated[PlatformClient, Depends(get_platform_client)]
Ctx = Annotated[RequestContext, Depends(get_request_context)]
AppSettings = Annotated[Settings, Depends(get_settings)]


# ---------------------------------------------------------------------------
# Agent-layer dependencies
# ---------------------------------------------------------------------------

def get_agent_deps(request: Request):
    """Build AgentDeps (base_deps) from request state for agent runs."""
    from app.agents.base_deps import AgentDeps
    from app.models.access_scope import AccessScope

    ctx = get_request_context(request)
    platform = get_platform_client(request)
    redis = get_redis(request)
    return AgentDeps(
        platform=platform,
        access_scope=ctx.access_scope or AccessScope(
            visibility_mode="full_tenant"
        ),
        tenant_id=ctx.tenant_id,
        actor_id=ctx.actor_id,
        redis=redis,
    )


def get_conversation_memory(request: Request):
    """Retrieve ConversationMemory backed by the shared Redis."""
    from app.services.conversation_memory import (
        ConversationMemory,
    )

    redis_client = get_redis(request)
    return ConversationMemory(redis_client)


async def build_worker_dependencies() -> dict:
    """Build shared dependencies for the ARQ worker process.

    Called once at worker startup. Returns a dict that is
    spread into the ARQ ctx.
    """
    import httpx
    from langfuse import Langfuse

    settings = get_settings()
    platform_client = await init_platform_client(settings)
    redis = await init_redis(settings)
    langfuse = Langfuse()
    http_client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))

    return {
        "platform_client": platform_client,
        "redis": redis,
        "langfuse": langfuse,
        "http_client": http_client,
        "settings": settings,
    }
