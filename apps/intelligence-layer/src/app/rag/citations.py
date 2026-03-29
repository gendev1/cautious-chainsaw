"""
app/rag/citations.py — Citation tracking for RAG responses.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Citation(BaseModel):
    """A citation linking a response to a source artifact."""

    source_type: str
    source_id: str
    title: str
    excerpt: str
    relevance_score: float
    source_date: str | None
    chunk_index: int
    metadata: dict


class CitationTracker:
    """Maps retrieved chunks to citations for the response."""

    def build_citations(
        self,
        included_chunks: list[Any],
    ) -> list[Citation]:
        """Convert included chunks to citation objects.

        Deduplicates by source — if multiple chunks come from
        the same source, uses the highest-scoring one.
        """
        citations: list[Citation] = []
        seen_sources: set[str] = set()

        for chunk in included_chunks:
            source_key = (
                f"{chunk.source_type}:{chunk.source_id}"
            )
            if source_key in seen_sources:
                continue
            seen_sources.add(source_key)

            meta = getattr(chunk, "metadata", {})
            if not isinstance(meta, dict):
                meta = {}

            title = (
                meta.get("title")
                or meta.get("subject")
                or chunk.source_id
            )

            created = getattr(chunk, "created_at", "")
            source_date = (
                created[:10] if created else None
            )

            citations.append(
                Citation(
                    source_type=chunk.source_type,
                    source_id=chunk.source_id,
                    title=title,
                    excerpt=chunk.text[:200].strip(),
                    relevance_score=round(
                        chunk.relevance_score, 4
                    ),
                    source_date=source_date,
                    chunk_index=chunk.chunk_index,
                    metadata={
                        k: v
                        for k, v in meta.items()
                        if k
                        in (
                            "sender",
                            "recipients",
                            "participants",
                            "meeting_date",
                            "file_type",
                            "section",
                        )
                    },
                )
            )

        return citations
