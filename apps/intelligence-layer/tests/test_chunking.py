"""Tests for text chunking — token boundaries, overlap, metadata."""
from __future__ import annotations

from app.rag.chunking import ChunkMetadata, TextChunker


def _make_metadata(**kwargs) -> ChunkMetadata:
    defaults = {
        "source_type": "document",
        "source_id": "doc_001",
        "tenant_id": "t_001",
    }
    defaults.update(kwargs)
    return ChunkMetadata(**defaults)


def test_chunker_splits_long_text() -> None:
    """T1: Splits text into multiple chunks with overlap."""
    chunker = TextChunker(chunk_size=50, chunk_overlap=10)
    # Generate a text that should produce multiple chunks
    text = "word " * 200  # ~200 tokens
    meta = _make_metadata()
    chunks = chunker.chunk_text(text, meta)
    assert len(chunks) > 1
    # Each chunk should be <= chunk_size tokens
    for chunk in chunks:
        assert chunk.token_count <= 50


def test_chunker_preserves_metadata() -> None:
    """T2: Every chunk carries the source metadata."""
    chunker = TextChunker(chunk_size=50, chunk_overlap=10)
    text = "word " * 200
    meta = _make_metadata(
        household_id="hh_001",
        client_id="cl_001",
    )
    chunks = chunker.chunk_text(text, meta)
    for chunk in chunks:
        assert chunk.metadata.tenant_id == "t_001"
        assert chunk.metadata.source_id == "doc_001"
        assert chunk.metadata.household_id == "hh_001"
        assert chunk.metadata.client_id == "cl_001"


def test_chunker_handles_empty_text() -> None:
    """Empty text produces no chunks."""
    chunker = TextChunker()
    chunks = chunker.chunk_text("", _make_metadata())
    assert chunks == []


def test_chunker_short_text_single_chunk() -> None:
    """Short text fits in a single chunk."""
    chunker = TextChunker(chunk_size=512)
    text = "This is a short sentence."
    chunks = chunker.chunk_text(text, _make_metadata())
    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0


def test_chunk_indexes_are_sequential() -> None:
    """Chunk indexes are 0, 1, 2, ..."""
    chunker = TextChunker(chunk_size=50, chunk_overlap=10)
    text = "word " * 200
    chunks = chunker.chunk_text(text, _make_metadata())
    for i, chunk in enumerate(chunks):
        assert chunk.chunk_index == i
