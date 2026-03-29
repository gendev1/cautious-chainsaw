"""Tests for RequestScopedCache — per-request in-memory cache."""
from __future__ import annotations

from app.services.request_cache import RequestScopedCache


def test_cache_miss_returns_none() -> None:
    """Unknown key returns None."""
    cache = RequestScopedCache()
    assert cache.get("nonexistent") is None


def test_cache_hit_returns_stored_value() -> None:
    """Stored value is returned on subsequent get."""
    cache = RequestScopedCache()
    cache.set("key1", {"data": 42})
    assert cache.get("key1") == {"data": 42}


def test_evicts_oldest_at_max_capacity() -> None:
    """Oldest entry evicted when cache reaches max size."""
    cache = RequestScopedCache(max_entries=2)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)  # should evict "a"
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3


def test_stats_track_hits_and_misses() -> None:
    """Stats dict reports hits, misses, and entries."""
    cache = RequestScopedCache()
    cache.set("x", 10)
    cache.get("x")      # hit
    cache.get("y")      # miss
    stats = cache.stats
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["entries"] == 1


def test_clear_empties_cache() -> None:
    """Clear removes all entries."""
    cache = RequestScopedCache()
    cache.set("a", 1)
    cache.set("b", 2)
    cache.clear()
    assert cache.get("a") is None
    assert cache.stats["entries"] == 0
