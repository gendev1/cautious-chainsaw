"""Tests for ContextCache — Redis-backed context fragment caching."""
from __future__ import annotations

import asyncio

import fakeredis.aioredis
import pytest

from app.services.context_cache import ContextCache, DEFAULT_CONTEXT_TTL_S


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis()


@pytest.fixture
def cache(redis):
    return ContextCache(redis=redis, ttl_s=DEFAULT_CONTEXT_TTL_S)


# ---------------------------------------------------------------------------
# Basic get/set
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cache_miss_returns_none(cache: ContextCache) -> None:
    """Getting a non-existent fragment returns None."""
    result = await cache.get("tenant_1", "client_list")
    assert result is None


@pytest.mark.anyio
async def test_cache_set_and_get_roundtrip(cache: ContextCache) -> None:
    """Setting a fragment and getting it back returns the same data."""
    data = {"clients": ["Alice", "Bob"], "count": 2}
    await cache.set("tenant_1", "client_list", data)

    result = await cache.get("tenant_1", "client_list")
    assert result == data


# ---------------------------------------------------------------------------
# TTL
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cache_respects_ttl(redis) -> None:
    """Cached data expires after TTL."""
    cache = ContextCache(redis=redis, ttl_s=1)  # 1 second TTL
    await cache.set("tenant_1", "client_list", {"clients": []})

    # Data should be available immediately
    result = await cache.get("tenant_1", "client_list")
    assert result is not None

    # Wait for TTL to expire
    await asyncio.sleep(1.1)

    result = await cache.get("tenant_1", "client_list")
    assert result is None, "Data should have expired after TTL"


# ---------------------------------------------------------------------------
# get_or_load
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_or_load_calls_loader_on_miss(cache: ContextCache) -> None:
    """get_or_load invokes the loader function on cache miss."""

    async def async_loader():
        return {"clients": ["Alice"]}

    data, hit = await cache.get_or_load("tenant_1", "client_list", async_loader)

    assert data == {"clients": ["Alice"]}
    assert hit is False


@pytest.mark.anyio
async def test_get_or_load_returns_cached_on_hit(cache: ContextCache) -> None:
    """get_or_load returns cached data without calling loader on cache hit."""
    await cache.set("tenant_1", "client_list", {"clients": ["Alice"]})

    call_count = 0

    async def counting_loader():
        nonlocal call_count
        call_count += 1
        return {"clients": ["should not be called"]}

    data, hit = await cache.get_or_load("tenant_1", "client_list", counting_loader)

    assert data == {"clients": ["Alice"]}
    assert hit is True
    assert call_count == 0, "Loader should not be called on cache hit"


@pytest.mark.anyio
async def test_get_or_load_returns_cache_hit_flag(cache: ContextCache) -> None:
    """get_or_load returns (data, True) on hit and (data, False) on miss."""
    async def loader():
        return {"value": 42}

    # First call: miss
    data1, hit1 = await cache.get_or_load("tenant_1", "counter", loader)
    assert hit1 is False

    # Second call: hit
    data2, hit2 = await cache.get_or_load("tenant_1", "counter", loader)
    assert hit2 is True


# ---------------------------------------------------------------------------
# Invalidation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_invalidate_removes_specific_fragment(cache: ContextCache) -> None:
    """Invalidating a specific fragment removes only that fragment."""
    await cache.set("tenant_1", "client_list", {"clients": []})
    await cache.set("tenant_1", "household_summary", {"summary": "..."})

    await cache.invalidate("tenant_1", "client_list")

    assert await cache.get("tenant_1", "client_list") is None
    assert await cache.get("tenant_1", "household_summary") is not None


@pytest.mark.anyio
async def test_invalidate_all_removes_tenant_fragments(cache: ContextCache) -> None:
    """Invalidating without fragment_name removes all fragments for the tenant."""
    await cache.set("tenant_1", "client_list", {"clients": []})
    await cache.set("tenant_1", "household_summary", {"summary": "..."})

    await cache.invalidate("tenant_1")

    assert await cache.get("tenant_1", "client_list") is None
    assert await cache.get("tenant_1", "household_summary") is None


# ---------------------------------------------------------------------------
# Key scoping
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cache_key_is_tenant_scoped(cache: ContextCache) -> None:
    """Cache keys are scoped per tenant -- different tenants do not share data."""
    await cache.set("tenant_1", "client_list", {"clients": ["Alice"]})
    await cache.set("tenant_2", "client_list", {"clients": ["Bob"]})

    result_1 = await cache.get("tenant_1", "client_list")
    result_2 = await cache.get("tenant_2", "client_list")

    assert result_1 == {"clients": ["Alice"]}
    assert result_2 == {"clients": ["Bob"]}
