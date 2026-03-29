"""
app/rag/chunking.py — Token-aware text chunking with metadata preservation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import tiktoken


@dataclass
class ChunkMetadata:
    """Metadata preserved on every chunk."""

    source_type: str
    source_id: str
    tenant_id: str
    household_id: str | None = None
    client_id: str | None = None
    account_id: str | None = None
    advisor_id: str | None = None
    visibility_tags: list[str] = field(default_factory=list)
    title: str | None = None
    author: str | None = None
    created_at: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Chunk:
    text: str
    chunk_index: int
    token_count: int
    metadata: ChunkMetadata


class TextChunker:
    """Token-aware chunker with overlap and metadata preservation."""

    def __init__(
        self,
        chunk_size: int = 512,
        chunk_overlap: int = 64,
        encoding_name: str = "cl100k_base",
    ) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.enc = tiktoken.get_encoding(encoding_name)

    def chunk_text(
        self, text: str, metadata: ChunkMetadata
    ) -> list[Chunk]:
        """Split text into overlapping token-bounded chunks."""
        tokens = self.enc.encode(text)
        if not tokens:
            return []

        chunks: list[Chunk] = []
        start = 0
        chunk_index = 0

        while start < len(tokens):
            end = min(start + self.chunk_size, len(tokens))
            chunk_tokens = tokens[start:end]
            chunk_text = self.enc.decode(chunk_tokens)

            chunks.append(
                Chunk(
                    text=chunk_text,
                    chunk_index=chunk_index,
                    token_count=len(chunk_tokens),
                    metadata=metadata,
                )
            )

            step = max(
                self.chunk_size - self.chunk_overlap, 1
            )
            start += step
            chunk_index += 1

        return chunks
