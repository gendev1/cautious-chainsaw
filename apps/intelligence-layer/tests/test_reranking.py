"""Tests for chunk reranking — scoring formula, decay, association."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.rag.reranking import ChunkReranker, RerankConfig


@dataclass
class FakeChunk:
    chunk_id: str = "c_001"
    source_type: str = "document"
    source_id: str = "doc_001"
    chunk_index: int = 0
    text: str = "sample text"
    cosine_distance: float = 0.2
    relevance_score: float = 0.8
    created_at: str = ""
    household_id: str | None = None
    client_id: str | None = None
    account_id: str | None = None
    advisor_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _make_chunk(**kwargs) -> FakeChunk:
    if "created_at" not in kwargs:
        kwargs["created_at"] = datetime.now(
            UTC
        ).isoformat()
    return FakeChunk(**kwargs)


def test_reranker_sorts_by_composite_score() -> None:
    """T3: Chunks are ranked by composite score."""
    reranker = ChunkReranker()
    now = datetime.now(UTC)
    high = _make_chunk(
        relevance_score=0.95,
        created_at=now.isoformat(),
    )
    low = _make_chunk(
        relevance_score=0.50,
        created_at=now.isoformat(),
    )
    result = reranker.rerank([low, high], now=now)
    assert result[0].relevance_score > result[1].relevance_score


def test_recency_decay() -> None:
    """T4: 30-day-old chunk gets ~0.5 recency score."""
    reranker = ChunkReranker()
    now = datetime.now(UTC)
    score_today = reranker._recency_score(
        now.isoformat(), now
    )
    score_30d = reranker._recency_score(
        (now - timedelta(days=30)).isoformat(), now
    )
    assert abs(score_today - 1.0) < 0.01
    assert abs(score_30d - 0.5) < 0.05


def test_association_score_client_match() -> None:
    """T5: Client match = 1.0, advisor = 0.5, none = 0.0."""
    reranker = ChunkReranker()
    client_chunk = _make_chunk(client_id="cl_001")
    advisor_chunk = _make_chunk(advisor_id="adv_001")
    none_chunk = _make_chunk()

    assert reranker._association_score(
        client_chunk, "cl_001", None
    ) == 1.0
    assert reranker._association_score(
        advisor_chunk, None, None
    ) == 0.5
    assert reranker._association_score(
        none_chunk, None, None
    ) == 0.0


def test_reranker_returns_top_k() -> None:
    """Reranker returns at most top_k chunks."""
    config = RerankConfig(top_k=3)
    reranker = ChunkReranker(config=config)
    now = datetime.now(UTC)
    chunks = [
        _make_chunk(
            relevance_score=0.5 + i * 0.05,
            created_at=now.isoformat(),
        )
        for i in range(10)
    ]
    result = reranker.rerank(chunks, now=now)
    assert len(result) == 3


def test_reranker_empty_input() -> None:
    """Empty input returns empty output."""
    reranker = ChunkReranker()
    assert reranker.rerank([]) == []
