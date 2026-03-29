"""
app/rag/reranking.py — Chunk reranking by relevance, recency, and association.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol


class ChunkLike(Protocol):
    """Protocol for objects that can be reranked."""

    relevance_score: float
    created_at: str
    client_id: str | None
    household_id: str | None
    advisor_id: str | None


@dataclass
class RerankConfig:
    """Weights for the reranking formula."""

    relevance_weight: float = 0.60
    recency_weight: float = 0.25
    association_weight: float = 0.15
    recency_half_life_days: float = 30.0
    top_k: int = 8


class ChunkReranker:
    """Reranks retrieved chunks by relevance, recency, and association."""

    def __init__(
        self, config: RerankConfig | None = None
    ) -> None:
        self.config = config or RerankConfig()

    def rerank(
        self,
        chunks: list[Any],
        query_client_id: str | None = None,
        query_household_id: str | None = None,
        now: datetime | None = None,
    ) -> list[Any]:
        """Rerank chunks and return the top-K."""
        if not chunks:
            return []

        now = now or datetime.now(UTC)
        scored: list[tuple[float, Any]] = []

        for chunk in chunks:
            relevance = chunk.relevance_score
            recency = self._recency_score(
                chunk.created_at, now
            )
            association = self._association_score(
                chunk, query_client_id, query_household_id
            )

            final_score = (
                self.config.relevance_weight * relevance
                + self.config.recency_weight * recency
                + self.config.association_weight * association
            )

            chunk.relevance_score = final_score
            scored.append((final_score, chunk))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            chunk
            for _, chunk in scored[: self.config.top_k]
        ]

    def _recency_score(
        self, created_at: str, now: datetime
    ) -> float:
        """Exponential decay based on age in days."""
        try:
            created = datetime.fromisoformat(created_at)
        except (ValueError, TypeError):
            return 0.0

        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)

        age_days = max(
            (now - created).total_seconds() / 86400.0, 0.0
        )
        half_life = self.config.recency_half_life_days
        return math.pow(2.0, -age_days / half_life)

    def _association_score(
        self,
        chunk: Any,
        query_client_id: str | None,
        query_household_id: str | None,
    ) -> float:
        """Score based on association with query context."""
        if (
            query_client_id
            and getattr(chunk, "client_id", None)
            == query_client_id
        ):
            return 1.0
        if (
            query_household_id
            and getattr(chunk, "household_id", None)
            == query_household_id
        ):
            return 1.0
        if getattr(chunk, "advisor_id", None):
            return 0.5
        return 0.0
