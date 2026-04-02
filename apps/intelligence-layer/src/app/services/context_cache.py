"""
app/services/context_cache.py — Redis-backed context fragment caching.

Ported from Claude Code's memoized context loading pattern
(context.ts: getSystemContext, getUserContext). Caches expensive
context fragments per-tenant with short TTL to avoid redundant
platform API calls across requests.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Coroutine

from redis.asyncio import Redis

logger = logging.getLogger("sidecar.context_cache")

DEFAULT_CONTEXT_TTL_S = 300  # 5 minutes


class ContextCache:
    """Redis-backed cache for expensive context fragments.

    Caches per-tenant context (e.g., client list, household summary)
    to avoid redundant platform API calls across requests.
    """

    def __init__(self, redis: Redis, ttl_s: int = DEFAULT_CONTEXT_TTL_S) -> None:  # type: ignore[type-arg]
        self._redis = redis
        self._ttl_s = ttl_s

    def _key(self, tenant_id: str, fragment_name: str) -> str:
        """Build Redis key: sidecar:context:{tenant_id}:{fragment_name}"""
        return f"sidecar:context:{tenant_id}:{fragment_name}"

    async def get(self, tenant_id: str, fragment_name: str) -> Any | None:
        """Get a cached context fragment. Returns None on miss."""
        key = self._key(tenant_id, fragment_name)
        raw = await self._redis.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

    async def set(self, tenant_id: str, fragment_name: str, data: Any) -> None:
        """Cache a context fragment with TTL."""
        key = self._key(tenant_id, fragment_name)
        await self._redis.set(key, json.dumps(data), ex=self._ttl_s)

    async def get_or_load(
        self,
        tenant_id: str,
        fragment_name: str,
        loader: Callable[..., Coroutine[Any, Any, Any]],
        **loader_kwargs: Any,
    ) -> tuple[Any, bool]:
        """Get from cache or call loader. Returns (data, cache_hit)."""
        cached = await self.get(tenant_id, fragment_name)
        if cached is not None:
            return cached, True

        data = await loader(**loader_kwargs)
        await self.set(tenant_id, fragment_name, data)
        return data, False

    async def invalidate(
        self, tenant_id: str, fragment_name: str | None = None
    ) -> None:
        """Invalidate a specific fragment or all fragments for a tenant."""
        if fragment_name is not None:
            key = self._key(tenant_id, fragment_name)
            await self._redis.delete(key)
        else:
            # Scan and delete all keys for this tenant
            pattern = f"sidecar:context:{tenant_id}:*"
            async for key in self._redis.scan_iter(match=pattern):
                await self._redis.delete(key)
