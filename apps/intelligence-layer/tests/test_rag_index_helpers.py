"""Tests for RAG index chunking and ID generation."""
from __future__ import annotations

from app.jobs.rag_index import chunk_text, make_chunk_id


def test_short_text_single_chunk() -> None:
    """Text shorter than chunk_size returns one chunk."""
    text = "Hello world."
    chunks = chunk_text(text)
    assert chunks == ["Hello world."]


def test_long_text_splits_at_paragraphs() -> None:
    """Text with paragraphs splits at paragraph boundaries when possible."""
    para1 = "First paragraph about investments. " * 30
    para2 = "Second paragraph about taxes. " * 30
    text = para1 + "\n\n" + para2
    chunks = chunk_text(text)
    assert len(chunks) >= 2


def test_make_chunk_id_deterministic() -> None:
    """Same inputs produce same chunk ID."""
    id1 = make_chunk_id("t1", "src1", 0)
    id2 = make_chunk_id("t1", "src1", 0)
    assert id1 == id2
    id3 = make_chunk_id("t1", "src1", 1)
    assert id1 != id3
    assert len(id1) == 24
