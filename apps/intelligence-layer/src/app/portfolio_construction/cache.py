"""Theme score cache: deterministic key computation and Redis-backed caching."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.portfolio_construction.models import ThemeScoreResult


class ThemeScoreCache:
    """Cache for theme scores with deterministic key computation."""

    def __init__(self, redis: Any, ttl_s: int = 21600) -> None:
        self._redis = redis
        self._ttl_s = ttl_s
        self._local: dict[str, list[ThemeScoreResult]] = {}

    def compute_key(
        self,
        themes: list[str],
        anti_goals: list[str],
        tickers: list[str],
        scorer_model: str,
        prompt_version: str,
        universe_snapshot_id: str,
    ) -> str:
        """Compute a deterministic cache key from sorted, canonical inputs."""
        canonical = json.dumps(
            {
                "themes": sorted(themes),
                "anti_goals": sorted(anti_goals),
                "tickers": sorted(tickers),
                "scorer_model": scorer_model,
                "prompt_version": prompt_version,
                "universe_snapshot_id": universe_snapshot_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    async def get(self, key: str) -> list[ThemeScoreResult] | None:
        """Look up cached scores: local dict first, then Redis."""
        if key in self._local:
            return self._local[key]

        raw = await self._redis.get(key)
        if raw is None:
            return None

        data = json.loads(raw)
        scores = [ThemeScoreResult.model_validate(item) for item in data]
        self._local[key] = scores
        return scores

    async def set(self, key: str, scores: list[ThemeScoreResult]) -> None:
        """Write scores to both local cache and Redis."""
        self._local[key] = scores
        serialized = json.dumps([s.model_dump() for s in scores])
        await self._redis.set(key, serialized, ex=self._ttl_s)
