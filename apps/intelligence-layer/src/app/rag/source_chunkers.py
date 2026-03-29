"""
app/rag/source_chunkers.py — Source-specific chunking strategies.
"""
from __future__ import annotations

from app.rag.chunking import Chunk, ChunkMetadata, TextChunker


class DocumentChunker:
    """Chunks uploaded documents (PDFs, tax returns, estate plans)."""

    def __init__(
        self, chunker: TextChunker | None = None
    ) -> None:
        self.chunker = chunker or TextChunker()

    def chunk_document(
        self,
        extracted_text: str,
        metadata: ChunkMetadata,
        section_headings: list[tuple[int, str]] | None = None,
    ) -> list[Chunk]:
        chunks = self.chunker.chunk_text(
            extracted_text, metadata
        )
        if section_headings:
            for chunk in chunks:
                heading = self._find_heading(
                    chunk.text, section_headings, extracted_text
                )
                if heading:
                    chunk.metadata.extra["section"] = heading
        return chunks

    def _find_heading(
        self,
        chunk_text: str,
        headings: list[tuple[int, str]],
        full_text: str,
    ) -> str | None:
        pos = full_text.find(chunk_text[:80])
        if pos < 0:
            return None
        best = None
        for offset, heading in headings:
            if offset <= pos:
                best = heading
        return best


class EmailChunker:
    """Chunks email messages."""

    def __init__(
        self, chunker: TextChunker | None = None
    ) -> None:
        self.chunker = chunker or TextChunker()

    def chunk_email(
        self,
        subject: str,
        body: str,
        metadata: ChunkMetadata,
    ) -> list[Chunk]:
        full_text = f"Subject: {subject}\n\n{body}"
        return self.chunker.chunk_text(full_text, metadata)


class CRMNoteChunker:
    """Chunks CRM notes and activity entries."""

    def __init__(
        self, chunker: TextChunker | None = None
    ) -> None:
        self.chunker = chunker or TextChunker()

    def chunk_note(
        self, text: str, metadata: ChunkMetadata
    ) -> list[Chunk]:
        return self.chunker.chunk_text(text, metadata)


class TranscriptChunker:
    """Chunks meeting transcripts."""

    def __init__(
        self, chunker: TextChunker | None = None
    ) -> None:
        self.chunker = chunker or TextChunker()

    def chunk_transcript(
        self,
        transcript_text: str,
        metadata: ChunkMetadata,
    ) -> list[Chunk]:
        return self.chunker.chunk_text(
            transcript_text, metadata
        )
