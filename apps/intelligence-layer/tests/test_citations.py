"""Tests for citation tracker — deduplication and excerpt truncation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.rag.citations import CitationTracker


@dataclass
class FakeChunk:
    chunk_id: str = "c_001"
    source_type: str = "document"
    source_id: str = "doc_001"
    chunk_index: int = 0
    text: str = "Sample text"
    cosine_distance: float = 0.2
    relevance_score: float = 0.8
    created_at: str = "2026-03-28T00:00:00Z"
    household_id: str | None = None
    client_id: str | None = None
    account_id: str | None = None
    advisor_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def test_deduplicates_by_source() -> None:
    """T8: Multiple chunks from same source produce one citation."""
    tracker = CitationTracker()
    chunks = [
        FakeChunk(source_id="doc_001", chunk_index=0),
        FakeChunk(source_id="doc_001", chunk_index=1),
        FakeChunk(source_id="doc_002", chunk_index=0),
    ]
    citations = tracker.build_citations(chunks)
    assert len(citations) == 2
    source_ids = {c.source_id for c in citations}
    assert source_ids == {"doc_001", "doc_002"}


def test_excerpt_truncated_to_200_chars() -> None:
    """T9: Citation excerpt is truncated to 200 characters."""
    tracker = CitationTracker()
    long_text = "a" * 500
    chunks = [FakeChunk(text=long_text)]
    citations = tracker.build_citations(chunks)
    assert len(citations[0].excerpt) <= 200


def test_empty_chunks_produces_no_citations() -> None:
    """No chunks means no citations."""
    tracker = CitationTracker()
    assert tracker.build_citations([]) == []


def test_citation_uses_title_from_metadata() -> None:
    """Citation title comes from metadata if available."""
    tracker = CitationTracker()
    chunks = [
        FakeChunk(
            metadata={"title": "Smith Tax Return 2025"}
        )
    ]
    citations = tracker.build_citations(chunks)
    assert citations[0].title == "Smith Tax Return 2025"
