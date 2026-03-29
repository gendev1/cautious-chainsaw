"""
app/services/request_cache.py — Per-request in-memory cache for platform reads.
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("sidecar.request_cache")


class RequestScopedCache:
    """Per-request in-memory cache for platform reads.

    Created when a FastAPI request begins, discarded when it
    completes. Prevents duplicate platform reads within a
    single agent run.
    """

    def __init__(self, max_entries: int = 100) -> None:
        self._store: dict[str, tuple[Any, float]] = {}
        self._max_entries = max_entries
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is not None:
            self._hits += 1
            return entry[0]
        self._misses += 1
        return None

    def set(self, key: str, value: Any) -> None:
        if len(self._store) >= self._max_entries:
            oldest_key = min(
                self._store,
                key=lambda k: self._store[k][1],
            )
            del self._store[oldest_key]
        self._store[key] = (value, time.monotonic())

    def clear(self) -> None:
        self._store.clear()

    @property
    def stats(self) -> dict[str, int]:
        return {
            "hits": self._hits,
            "misses": self._misses,
            "entries": len(self._store),
        }
