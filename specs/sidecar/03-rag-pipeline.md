# RAG Pipeline — Technical Implementation

This document specifies the Retrieval-Augmented Generation pipeline for the Python sidecar. It covers embedding generation, chunking, index schema, indexing jobs, tenant-scoped retrieval, access scope filtering, reranking, context window management, citation tracking, and index maintenance.

All code targets Python 3.12+, pgvector for vector storage, httpx for embedding API calls, and ARQ for background jobs.

---

## 1. Embedding Generation

### 1.1 Model choice

The pipeline uses OpenAI `text-embedding-3-small` for all embedding operations. This model produces 1536-dimensional vectors by default. We reduce to **1024 dimensions** via the API's `dimensions` parameter to balance retrieval quality against storage and search cost.

All embeddings are L2-normalized by the API when the `dimensions` parameter is set, so cosine distance reduces to inner product distance. We store them as-is and use pgvector's cosine distance operator for search.

### 1.2 Embedding client

```python
"""sidecar/rag/embeddings.py"""

from __future__ import annotations

import asyncio
from typing import Sequence

import httpx
import numpy as np
from pydantic import BaseModel
from pydantic_settings import BaseSettings


class EmbeddingSettings(BaseSettings):
    openai_api_key: str
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1024
    embedding_batch_size: int = 64
    embedding_max_concurrent: int = 4
    embedding_base_url: str = "https://api.openai.com/v1"

    model_config = {"env_prefix": "SIDECAR_"}


class EmbeddingResult(BaseModel):
    index: int
    embedding: list[float]
    token_count: int


class EmbeddingClient:
    """Async batch embedding client with concurrency control."""

    def __init__(self, settings: EmbeddingSettings) -> None:
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.embedding_max_concurrent)
        self._http = httpx.AsyncClient(
            base_url=settings.embedding_base_url,
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(60.0, connect=10.0),
        )

    async def embed_texts(self, texts: Sequence[str]) -> list[EmbeddingResult]:
        """Embed a list of texts in batches with concurrency control.

        Returns results in the same order as the input texts.
        """
        batches = self._split_batches(list(texts))
        tasks = [self._embed_batch(batch, offset) for offset, batch in batches]
        nested = await asyncio.gather(*tasks)
        results = [r for batch_results in nested for r in batch_results]
        results.sort(key=lambda r: r.index)
        return results

    async def embed_single(self, text: str) -> list[float]:
        """Embed a single text. Convenience method for query embedding."""
        results = await self.embed_texts([text])
        return results[0].embedding

    def _split_batches(
        self, texts: list[str]
    ) -> list[tuple[int, list[str]]]:
        bs = self._settings.embedding_batch_size
        return [(i, texts[i : i + bs]) for i in range(0, len(texts), bs)]

    async def _embed_batch(
        self, texts: list[str], offset: int
    ) -> list[EmbeddingResult]:
        async with self._semaphore:
            resp = await self._http.post(
                "/embeddings",
                json={
                    "model": self._settings.embedding_model,
                    "input": texts,
                    "dimensions": self._settings.embedding_dimensions,
                },
            )
            resp.raise_for_status()
            data = resp.json()

            results: list[EmbeddingResult] = []
            for item in data["data"]:
                results.append(
                    EmbeddingResult(
                        index=offset + item["index"],
                        embedding=item["embedding"],
                        token_count=data["usage"]["total_tokens"],
                    )
                )
            return results

    async def close(self) -> None:
        await self._http.aclose()
```

### 1.3 Normalization

`text-embedding-3-small` returns pre-normalized vectors when the `dimensions` parameter is specified. We verify normalization on ingest as a defensive measure:

```python
def verify_normalized(embedding: list[float], tolerance: float = 1e-3) -> bool:
    norm = float(np.linalg.norm(embedding))
    return abs(norm - 1.0) < tolerance
```

If a vector fails the check, re-normalize before storage:

```python
def normalize(embedding: list[float]) -> list[float]:
    arr = np.array(embedding, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm == 0:
        return embedding
    return (arr / norm).tolist()
```

---

## 2. Chunking Strategy

### 2.1 Parameters

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Chunk size | 512 tokens | Balances retrieval precision with sufficient context per chunk |
| Overlap | 64 tokens | Prevents information loss at chunk boundaries |
| Tokenizer | `tiktoken` with `cl100k_base` encoding | Matches the tokenizer used by `text-embedding-3-small` |

### 2.2 Chunker implementation

```python
"""sidecar/rag/chunking.py"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import tiktoken


@dataclass
class ChunkMetadata:
    """Metadata preserved on every chunk."""
    source_type: str          # "document", "email", "crm_note", "transcript", "activity"
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

    def chunk_text(self, text: str, metadata: ChunkMetadata) -> list[Chunk]:
        """Split text into overlapping token-bounded chunks.

        Each chunk preserves the full metadata from the source document.
        """
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

            # Advance by (chunk_size - overlap), but at least 1 token
            step = max(self.chunk_size - self.chunk_overlap, 1)
            start += step
            chunk_index += 1

        return chunks
```

### 2.3 Source-specific chunking strategies

Different source types require pre-processing before the generic chunker runs.

```python
"""sidecar/rag/source_chunkers.py"""

from __future__ import annotations

from sidecar.rag.chunking import ChunkMetadata, Chunk, TextChunker


class DocumentChunker:
    """Chunks uploaded documents (PDFs, tax returns, estate plans).

    Pre-processing: extract text via pdfplumber/pymupdf, strip headers/footers,
    preserve section headings as metadata.
    """

    def __init__(self, chunker: TextChunker | None = None) -> None:
        self.chunker = chunker or TextChunker()

    def chunk_document(
        self,
        extracted_text: str,
        metadata: ChunkMetadata,
        section_headings: list[tuple[int, str]] | None = None,
    ) -> list[Chunk]:
        chunks = self.chunker.chunk_text(extracted_text, metadata)

        # Annotate each chunk with its nearest section heading
        if section_headings:
            for chunk in chunks:
                heading = self._find_heading(chunk.text, section_headings, extracted_text)
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
        # Find the nearest preceding heading
        best = None
        for offset, heading in headings:
            if offset <= pos:
                best = heading
        return best


class EmailChunker:
    """Chunks email messages.

    Preserves sender, recipients, subject, and date in metadata.
    For long threads, each message is chunked independently.
    """

    def __init__(self, chunker: TextChunker | None = None) -> None:
        self.chunker = chunker or TextChunker()

    def chunk_email(
        self,
        subject: str,
        body: str,
        metadata: ChunkMetadata,
    ) -> list[Chunk]:
        # Prepend subject to give embedding context
        full_text = f"Subject: {subject}\n\n{body}"
        return self.chunker.chunk_text(full_text, metadata)


class CRMNoteChunker:
    """Chunks CRM notes and activity entries.

    Most CRM notes are short enough to be a single chunk.
    """

    def __init__(self, chunker: TextChunker | None = None) -> None:
        self.chunker = chunker or TextChunker()

    def chunk_note(self, text: str, metadata: ChunkMetadata) -> list[Chunk]:
        return self.chunker.chunk_text(text, metadata)


class TranscriptChunker:
    """Chunks meeting transcripts.

    Attempts to break on speaker turns when possible to preserve
    conversational coherence within chunks.
    """

    def __init__(self, chunker: TextChunker | None = None) -> None:
        self.chunker = chunker or TextChunker()

    def chunk_transcript(
        self,
        transcript_text: str,
        metadata: ChunkMetadata,
    ) -> list[Chunk]:
        # For speaker-diarized transcripts, each speaker turn is separated
        # by a line like "Speaker Name: ..." — we try to avoid splitting
        # mid-turn, but fall back to token-based chunking if turns are long.
        return self.chunker.chunk_text(transcript_text, metadata)
```

---

## 3. Index Schema

### 3.1 pgvector extension and table

```sql
-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Main chunk index table
CREATE TABLE IF NOT EXISTS rag_chunks (
    chunk_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    source_type     TEXT NOT NULL,       -- 'document', 'email', 'crm_note', 'transcript', 'activity'
    source_id       TEXT NOT NULL,       -- ID of the source artifact in the platform
    chunk_index     INTEGER NOT NULL,    -- Position of this chunk within the source
    household_id    TEXT,
    client_id       TEXT,
    account_id      TEXT,
    advisor_id      TEXT,
    visibility_tags TEXT[] NOT NULL DEFAULT '{}',
    text            TEXT NOT NULL,
    embedding       vector(1024) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata        JSONB NOT NULL DEFAULT '{}',

    -- Prevent duplicate chunks for the same source + position
    CONSTRAINT uq_source_chunk UNIQUE (tenant_id, source_id, chunk_index)
);

-- Tenant isolation index — every query MUST filter on tenant_id
CREATE INDEX idx_rag_chunks_tenant
    ON rag_chunks (tenant_id);

-- Composite index for scoped retrieval
CREATE INDEX idx_rag_chunks_tenant_household
    ON rag_chunks (tenant_id, household_id)
    WHERE household_id IS NOT NULL;

CREATE INDEX idx_rag_chunks_tenant_client
    ON rag_chunks (tenant_id, client_id)
    WHERE client_id IS NOT NULL;

CREATE INDEX idx_rag_chunks_tenant_advisor
    ON rag_chunks (tenant_id, advisor_id)
    WHERE advisor_id IS NOT NULL;

CREATE INDEX idx_rag_chunks_tenant_account
    ON rag_chunks (tenant_id, account_id)
    WHERE account_id IS NOT NULL;

-- Source lookup for idempotent re-indexing and deletion
CREATE INDEX idx_rag_chunks_source
    ON rag_chunks (tenant_id, source_id);

-- Vector similarity index (IVFFlat for production; HNSW alternative noted below)
-- IVFFlat: faster to build, good for moderate dataset sizes
CREATE INDEX idx_rag_chunks_embedding_ivfflat
    ON rag_chunks
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Alternative: HNSW index for higher recall at the cost of more memory
-- CREATE INDEX idx_rag_chunks_embedding_hnsw
--     ON rag_chunks
--     USING hnsw (embedding vector_cosine_ops)
--     WITH (m = 16, ef_construction = 64);

-- Recency filtering
CREATE INDEX idx_rag_chunks_created_at
    ON rag_chunks (tenant_id, created_at DESC);

-- GIN index for visibility_tags array containment queries
CREATE INDEX idx_rag_chunks_visibility_tags
    ON rag_chunks USING gin (visibility_tags);
```

### 3.2 Metadata JSONB conventions

The `metadata` column stores source-specific attributes that do not warrant dedicated columns:

| Source type | Typical metadata keys |
|---|---|
| document | `title`, `file_type`, `page_count`, `section` |
| email | `subject`, `sender`, `recipients`, `thread_id`, `date` |
| crm_note | `note_type`, `author`, `activity_date` |
| transcript | `meeting_id`, `duration_minutes`, `participants`, `meeting_date` |
| activity | `activity_type`, `description`, `occurred_at` |

---

## 4. Indexing Pipeline

### 4.1 Indexing job

New documents, emails, CRM notes, and transcripts trigger an ARQ job that fetches content, chunks it, generates embeddings, and upserts into pgvector. The job is idempotent: re-indexing the same source replaces existing chunks.

```python
"""sidecar/rag/indexing.py"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import asyncpg

from sidecar.rag.chunking import Chunk, ChunkMetadata, TextChunker
from sidecar.rag.embeddings import EmbeddingClient


@dataclass
class IndexingResult:
    source_id: str
    chunks_indexed: int
    chunks_deleted: int
    total_tokens: int


class IndexingPipeline:
    """Fetches content, chunks, embeds, and upserts into pgvector."""

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        embedding_client: EmbeddingClient,
        chunker: TextChunker | None = None,
    ) -> None:
        self.db = db_pool
        self.embeddings = embedding_client
        self.chunker = chunker or TextChunker()

    async def index_source(
        self,
        text: str,
        metadata: ChunkMetadata,
    ) -> IndexingResult:
        """Index a single source artifact. Idempotent via upsert.

        1. Chunk the text
        2. Embed all chunks in batch
        3. Delete stale chunks for this source (if chunk count changed)
        4. Upsert new chunks
        """
        chunks = self.chunker.chunk_text(text, metadata)
        if not chunks:
            deleted = await self._delete_source_chunks(
                metadata.tenant_id, metadata.source_id
            )
            return IndexingResult(
                source_id=metadata.source_id,
                chunks_indexed=0,
                chunks_deleted=deleted,
                total_tokens=0,
            )

        # Batch embed all chunk texts
        texts = [c.text for c in chunks]
        embed_results = await self.embeddings.embed_texts(texts)

        total_tokens = sum(r.token_count for r in embed_results)

        # Upsert into pgvector — delete existing chunks for this source
        # first, then insert new ones. This handles re-indexing cleanly.
        async with self.db.acquire() as conn:
            async with conn.transaction():
                deleted = await conn.fetchval(
                    """
                    DELETE FROM rag_chunks
                    WHERE tenant_id = $1 AND source_id = $2
                    RETURNING count(*)
                    """,
                    uuid.UUID(metadata.tenant_id),
                    metadata.source_id,
                ) or 0

                for chunk, embed_result in zip(chunks, embed_results):
                    await conn.execute(
                        """
                        INSERT INTO rag_chunks (
                            tenant_id, source_type, source_id, chunk_index,
                            household_id, client_id, account_id, advisor_id,
                            visibility_tags, text, embedding, metadata
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12
                        )
                        """,
                        uuid.UUID(metadata.tenant_id),
                        metadata.source_type,
                        metadata.source_id,
                        chunk.chunk_index,
                        metadata.household_id,
                        metadata.client_id,
                        metadata.account_id,
                        metadata.advisor_id,
                        metadata.visibility_tags,
                        chunk.text,
                        _to_pgvector(embed_result.embedding),
                        _metadata_jsonb(metadata),
                    )

        return IndexingResult(
            source_id=metadata.source_id,
            chunks_indexed=len(chunks),
            chunks_deleted=deleted,
            total_tokens=total_tokens,
        )

    async def delete_source(self, tenant_id: str, source_id: str) -> int:
        """Remove all chunks for a source. Called when a document is deleted."""
        return await self._delete_source_chunks(tenant_id, source_id)

    async def _delete_source_chunks(self, tenant_id: str, source_id: str) -> int:
        async with self.db.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM rag_chunks
                WHERE tenant_id = $1 AND source_id = $2
                """,
                uuid.UUID(tenant_id),
                source_id,
            )
            # asyncpg returns "DELETE N"
            return int(result.split()[-1])


def _to_pgvector(embedding: list[float]) -> str:
    """Format embedding list as pgvector literal."""
    return "[" + ",".join(str(v) for v in embedding) + "]"


def _metadata_jsonb(meta: ChunkMetadata) -> dict:
    """Build JSONB metadata payload from chunk metadata."""
    result: dict = {}
    if meta.title:
        result["title"] = meta.title
    if meta.author:
        result["author"] = meta.author
    if meta.created_at:
        result["created_at"] = meta.created_at
    result.update(meta.extra)
    return result
```

### 4.2 ARQ job definitions

```python
"""sidecar/jobs/indexing_jobs.py"""

from __future__ import annotations

from arq import ArqRedis

from sidecar.rag.chunking import ChunkMetadata
from sidecar.rag.indexing import IndexingPipeline


async def index_document_job(
    ctx: dict,
    tenant_id: str,
    source_id: str,
    source_type: str,
    text: str,
    household_id: str | None = None,
    client_id: str | None = None,
    account_id: str | None = None,
    advisor_id: str | None = None,
    visibility_tags: list[str] | None = None,
    title: str | None = None,
    extra_metadata: dict | None = None,
) -> dict:
    """ARQ job: index a single source artifact into pgvector.

    Triggered by platform events:
    - document.uploaded / document.updated
    - email.synced
    - crm_note.created / crm_note.updated
    - transcript.completed
    """
    pipeline: IndexingPipeline = ctx["indexing_pipeline"]

    metadata = ChunkMetadata(
        source_type=source_type,
        source_id=source_id,
        tenant_id=tenant_id,
        household_id=household_id,
        client_id=client_id,
        account_id=account_id,
        advisor_id=advisor_id,
        visibility_tags=visibility_tags or [],
        title=title,
        extra=extra_metadata or {},
    )

    result = await pipeline.index_source(text, metadata)

    return {
        "source_id": result.source_id,
        "chunks_indexed": result.chunks_indexed,
        "chunks_deleted": result.chunks_deleted,
        "total_tokens": result.total_tokens,
    }


async def delete_source_job(
    ctx: dict,
    tenant_id: str,
    source_id: str,
) -> dict:
    """ARQ job: remove all chunks for a deleted source artifact.

    Triggered by platform events:
    - document.deleted
    - email.deleted
    - crm_note.deleted
    """
    pipeline: IndexingPipeline = ctx["indexing_pipeline"]
    deleted = await pipeline.delete_source(tenant_id, source_id)
    return {"source_id": source_id, "chunks_deleted": deleted}
```

### 4.3 Triggering from the API layer

```python
"""sidecar/routers/indexing.py — webhook receiver for platform events."""

from __future__ import annotations

from arq import ArqRedis
from fastapi import APIRouter, Depends
from pydantic import BaseModel

router = APIRouter(prefix="/internal/indexing", tags=["indexing"])


class IndexEvent(BaseModel):
    event_type: str       # "document.uploaded", "email.synced", etc.
    tenant_id: str
    source_id: str
    source_type: str
    text: str
    household_id: str | None = None
    client_id: str | None = None
    account_id: str | None = None
    advisor_id: str | None = None
    visibility_tags: list[str] = []
    title: str | None = None
    extra_metadata: dict | None = None


class DeleteEvent(BaseModel):
    event_type: str       # "document.deleted", "email.deleted", etc.
    tenant_id: str
    source_id: str


@router.post("/event")
async def handle_index_event(
    event: IndexEvent,
    redis: ArqRedis = Depends(get_arq_redis),
) -> dict:
    """Receive platform indexing events and enqueue ARQ jobs."""
    if event.event_type.endswith(".deleted"):
        await redis.enqueue_job(
            "delete_source_job",
            tenant_id=event.tenant_id,
            source_id=event.source_id,
        )
    else:
        await redis.enqueue_job(
            "index_document_job",
            tenant_id=event.tenant_id,
            source_id=event.source_id,
            source_type=event.source_type,
            text=event.text,
            household_id=event.household_id,
            client_id=event.client_id,
            account_id=event.account_id,
            advisor_id=event.advisor_id,
            visibility_tags=event.visibility_tags,
            title=event.title,
            extra_metadata=event.extra_metadata,
        )
    return {"status": "enqueued", "source_id": event.source_id}
```

---

## 5. Tenant-Scoped Retrieval

Every vector search query MUST include `tenant_id` as a hard filter. This is non-negotiable. The pgvector query always applies `WHERE tenant_id = $1` before the vector similarity operation.

### 5.1 Retrieval query

```python
"""sidecar/rag/retrieval.py"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import asyncpg

from sidecar.rag.embeddings import EmbeddingClient


@dataclass
class AccessScope:
    """Access scope provided by the platform for the current actor."""
    tenant_id: str
    visibility_mode: str = "scoped"     # "full_tenant" or "scoped"
    household_ids: list[str] = field(default_factory=list)
    client_ids: list[str] = field(default_factory=list)
    account_ids: list[str] = field(default_factory=list)
    advisor_ids: list[str] = field(default_factory=list)
    document_ids: list[str] = field(default_factory=list)


@dataclass
class RetrievedChunk:
    chunk_id: str
    source_type: str
    source_id: str
    chunk_index: int
    text: str
    cosine_distance: float
    relevance_score: float     # 1 - cosine_distance
    created_at: str
    household_id: str | None
    client_id: str | None
    account_id: str | None
    advisor_id: str | None
    metadata: dict[str, Any]


class RetrieverService:
    """Tenant-scoped vector retrieval with access scope filtering."""

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        embedding_client: EmbeddingClient,
    ) -> None:
        self.db = db_pool
        self.embeddings = embedding_client

    async def retrieve(
        self,
        query: str,
        access_scope: AccessScope,
        top_k: int = 20,
        source_types: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        """Retrieve top-K chunks filtered by tenant and access scope.

        This method:
        1. Embeds the query
        2. Builds a WHERE clause enforcing tenant_id + access scope
        3. Runs pgvector cosine distance search
        4. Returns chunks that the actor is authorized to see
        """
        query_embedding = await self.embeddings.embed_single(query)
        embedding_literal = "[" + ",".join(str(v) for v in query_embedding) + "]"

        where_clauses, params = self._build_scope_filter(access_scope)

        if source_types:
            idx = len(params) + 1
            where_clauses.append(f"source_type = ANY(${idx})")
            params.append(source_types)

        where_sql = " AND ".join(where_clauses)
        idx = len(params) + 1
        params.append(embedding_literal)
        params.append(top_k)

        query_sql = f"""
            SELECT
                chunk_id,
                source_type,
                source_id,
                chunk_index,
                text,
                embedding <=> ${idx}::vector AS cosine_distance,
                created_at,
                household_id,
                client_id,
                account_id,
                advisor_id,
                metadata
            FROM rag_chunks
            WHERE {where_sql}
            ORDER BY embedding <=> ${idx}::vector ASC
            LIMIT ${idx + 1}
        """

        async with self.db.acquire() as conn:
            rows = await conn.fetch(query_sql, *params)

        return [
            RetrievedChunk(
                chunk_id=str(row["chunk_id"]),
                source_type=row["source_type"],
                source_id=row["source_id"],
                chunk_index=row["chunk_index"],
                text=row["text"],
                cosine_distance=row["cosine_distance"],
                relevance_score=1.0 - row["cosine_distance"],
                created_at=row["created_at"].isoformat(),
                household_id=row["household_id"],
                client_id=row["client_id"],
                account_id=row["account_id"],
                advisor_id=row["advisor_id"],
                metadata=dict(row["metadata"]) if row["metadata"] else {},
            )
            for row in rows
        ]

    def _build_scope_filter(
        self, scope: AccessScope
    ) -> tuple[list[str], list[Any]]:
        """Build WHERE clauses from access scope.

        Tenant ID is ALWAYS the first filter. This is the hard isolation boundary.
        """
        clauses: list[str] = []
        params: list[Any] = []

        # --- Mandatory tenant filter ---
        params.append(uuid.UUID(scope.tenant_id))
        clauses.append(f"tenant_id = $1")

        if scope.visibility_mode == "full_tenant":
            # Firm-wide admins see all data within the tenant
            return clauses, params

        # --- Actor access scope filters ---
        # The actor can see chunks that match ANY of their allowed scopes.
        # A chunk is visible if:
        #   - its household_id is in the actor's allowed households, OR
        #   - its client_id is in the actor's allowed clients, OR
        #   - its advisor_id is in the actor's allowed advisors, OR
        #   - its account_id is in the actor's allowed accounts, OR
        #   - it has no ownership fields set (tenant-wide resources)
        scope_conditions: list[str] = []
        idx = len(params) + 1

        if scope.household_ids:
            params.append(scope.household_ids)
            scope_conditions.append(f"household_id = ANY(${idx})")
            idx += 1

        if scope.client_ids:
            params.append(scope.client_ids)
            scope_conditions.append(f"client_id = ANY(${idx})")
            idx += 1

        if scope.advisor_ids:
            params.append(scope.advisor_ids)
            scope_conditions.append(f"advisor_id = ANY(${idx})")
            idx += 1

        if scope.account_ids:
            params.append(scope.account_ids)
            scope_conditions.append(f"account_id = ANY(${idx})")
            idx += 1

        # Tenant-wide resources with no ownership scoping
        scope_conditions.append(
            "(household_id IS NULL AND client_id IS NULL "
            "AND advisor_id IS NULL AND account_id IS NULL)"
        )

        if scope_conditions:
            clauses.append(f"({' OR '.join(scope_conditions)})")

        return clauses, params
```

### 5.2 Generated SQL example

For an advisor with access to households `hh_123` and `hh_456`, the generated SQL looks like:

```sql
SELECT
    chunk_id,
    source_type,
    source_id,
    chunk_index,
    text,
    embedding <=> $3::vector AS cosine_distance,
    created_at,
    household_id,
    client_id,
    account_id,
    advisor_id,
    metadata
FROM rag_chunks
WHERE tenant_id = $1
  AND (
      household_id = ANY($2)
      OR client_id IS NULL AND household_id IS NULL
         AND advisor_id IS NULL AND account_id IS NULL
  )
ORDER BY embedding <=> $3::vector ASC
LIMIT $4;

-- $1 = 'tenant_uuid'
-- $2 = ARRAY['hh_123', 'hh_456']
-- $3 = '[0.012, -0.034, ...]'  (query embedding vector)
-- $4 = 20
```

---

## 6. Access Scope Filtering

Access scope filtering is the second layer of data isolation, applied within a tenant. The platform computes the `AccessScope` for each request and the sidecar enforces it at the SQL level before any data reaches the LLM.

### 6.1 Scope resolution rules

| `visibility_mode` | Behavior |
|---|---|
| `full_tenant` | No additional scope filters. Actor sees all data in the tenant. Used for firm admins. |
| `scoped` | Only chunks matching the actor's `household_ids`, `client_ids`, `advisor_ids`, or `account_ids` are returned. Tenant-wide resources (all ownership fields null) are also included. |

### 6.2 Enforcement guarantees

The following invariants hold for every retrieval operation:

1. `tenant_id` is always included as a `WHERE` clause. There is no code path that omits it.
2. When `visibility_mode` is `scoped`, the ownership columns (`household_id`, `client_id`, `advisor_id`, `account_id`) are filtered against the actor's allowed sets.
3. Filtering happens in the SQL query, not after retrieval. The LLM never sees unauthorized chunks.
4. If the `AccessScope` contains empty lists for all ownership fields and the mode is `scoped`, the query returns only tenant-wide resources (all ownership fields null). This prevents a misconfigured scope from leaking data.

### 6.3 Middleware enforcement

```python
"""sidecar/middleware/scope.py"""

from __future__ import annotations

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

from sidecar.rag.retrieval import AccessScope


class ScopeEnforcementMiddleware(BaseHTTPMiddleware):
    """Ensures every request carries a valid access scope.

    Rejects requests that arrive without tenant_id or access_scope.
    """

    async def dispatch(self, request: Request, call_next):
        # Skip health checks and internal endpoints
        if request.url.path in ("/health", "/ready"):
            return await call_next(request)

        tenant_id = request.headers.get("x-tenant-id")
        if not tenant_id:
            raise HTTPException(status_code=400, detail="Missing x-tenant-id header")

        actor_id = request.headers.get("x-actor-id")
        if not actor_id:
            raise HTTPException(status_code=400, detail="Missing x-actor-id header")

        # Access scope is passed as a JSON-encoded header or in the request body.
        # The platform API is responsible for computing this before calling the sidecar.
        request.state.tenant_id = tenant_id
        request.state.actor_id = actor_id

        return await call_next(request)
```

---

## 7. Reranking

After the initial vector search returns the top 20 candidates, a reranking step scores each chunk on three dimensions: semantic relevance, recency, and client association strength. The final top 5-8 chunks are selected for the LLM context window.

### 7.1 Reranking implementation

```python
"""sidecar/rag/reranking.py"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from sidecar.rag.retrieval import RetrievedChunk


@dataclass
class RerankConfig:
    """Weights for the reranking formula."""
    relevance_weight: float = 0.60
    recency_weight: float = 0.25
    association_weight: float = 0.15

    # Recency decay: half-life in days. A chunk from 30 days ago
    # gets half the recency score of a chunk from today.
    recency_half_life_days: float = 30.0

    # Maximum chunks to return after reranking
    top_k: int = 8


class ChunkReranker:
    """Reranks retrieved chunks by relevance, recency, and client association."""

    def __init__(self, config: RerankConfig | None = None) -> None:
        self.config = config or RerankConfig()

    def rerank(
        self,
        chunks: list[RetrievedChunk],
        query_client_id: str | None = None,
        query_household_id: str | None = None,
        now: datetime | None = None,
    ) -> list[RetrievedChunk]:
        """Rerank chunks and return the top-K.

        Scoring formula:
            final_score = (relevance_weight * relevance_score)
                        + (recency_weight * recency_score)
                        + (association_weight * association_score)

        Where:
            relevance_score = 1 - cosine_distance  (from vector search)
            recency_score = 2^(-age_days / half_life)  (exponential decay)
            association_score = 1.0 if chunk is associated with the query's
                                client/household, 0.5 if same advisor, 0.0 otherwise
        """
        if not chunks:
            return []

        now = now or datetime.now(timezone.utc)
        scored: list[tuple[float, RetrievedChunk]] = []

        for chunk in chunks:
            relevance = chunk.relevance_score
            recency = self._recency_score(chunk.created_at, now)
            association = self._association_score(
                chunk, query_client_id, query_household_id
            )

            final_score = (
                self.config.relevance_weight * relevance
                + self.config.recency_weight * recency
                + self.config.association_weight * association
            )

            # Store the composite score back on the chunk for citation use
            chunk.relevance_score = final_score
            scored.append((final_score, chunk))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [chunk for _, chunk in scored[: self.config.top_k]]

    def _recency_score(self, created_at: str, now: datetime) -> float:
        """Exponential decay based on age in days."""
        try:
            created = datetime.fromisoformat(created_at)
        except (ValueError, TypeError):
            return 0.0

        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)

        age_days = max((now - created).total_seconds() / 86400.0, 0.0)
        half_life = self.config.recency_half_life_days
        return math.pow(2.0, -age_days / half_life)

    def _association_score(
        self,
        chunk: RetrievedChunk,
        query_client_id: str | None,
        query_household_id: str | None,
    ) -> float:
        """Score based on how closely the chunk is associated with the query context.

        1.0 = direct client or household match
        0.5 = same advisor (indirect association)
        0.0 = no specific association
        """
        if query_client_id and chunk.client_id == query_client_id:
            return 1.0
        if query_household_id and chunk.household_id == query_household_id:
            return 1.0
        # Advisor association is a weaker signal
        if chunk.advisor_id:
            return 0.5
        return 0.0
```

### 7.2 Reranking example

Given 20 vector search results for the query "What did we discuss about the Roth conversion for the Smith household?":

| Rank | Source | Cosine Dist | Age (days) | Client Match | Final Score |
|---|---|---|---|---|---|
| 1 | Meeting transcript (Smith review) | 0.15 | 7 | yes | 0.72 |
| 2 | Email re: Roth conversion | 0.18 | 14 | yes | 0.68 |
| 3 | CRM note on Smith tax plan | 0.20 | 30 | yes | 0.63 |
| 4 | Tax document 1040 | 0.22 | 90 | yes | 0.55 |
| ... | ... | ... | ... | ... | ... |

The top 8 after reranking are passed to the context window builder.

---

## 8. Context Window Management

### 8.1 Token budgeting

The LLM context window is a finite resource. The context builder allocates a token budget across four components:

```python
"""sidecar/rag/context.py"""

from __future__ import annotations

from dataclasses import dataclass

import tiktoken

from sidecar.rag.reranking import RetrievedChunk


@dataclass
class ContextBudget:
    """Token budget allocation for the LLM context window."""
    total_limit: int = 120_000       # Claude Sonnet context window
    system_prompt_reserve: int = 2_000
    conversation_history_reserve: int = 8_000
    retrieved_context_limit: int = 12_000
    response_reserve: int = 4_000

    @property
    def available_for_context(self) -> int:
        return (
            self.total_limit
            - self.system_prompt_reserve
            - self.conversation_history_reserve
            - self.response_reserve
        )


class ContextWindowBuilder:
    """Assembles the LLM context from retrieved chunks, conversation history,
    and system prompt within token budget constraints."""

    def __init__(
        self,
        budget: ContextBudget | None = None,
        encoding_name: str = "cl100k_base",
    ) -> None:
        self.budget = budget or ContextBudget()
        self.enc = tiktoken.get_encoding(encoding_name)

    def build_context(
        self,
        system_prompt: str,
        conversation_history: list[dict[str, str]],
        chunks: list[RetrievedChunk],
    ) -> tuple[str, list[dict[str, str]], list[RetrievedChunk]]:
        """Build the context window, returning:
        - final system prompt (with retrieved context injected)
        - truncated conversation history
        - the chunks that were actually included (for citation tracking)

        Truncation priority (lowest priority truncated first):
        1. Older conversation history messages
        2. Lower-ranked retrieved chunks
        3. System prompt is never truncated
        """
        system_tokens = self._count_tokens(system_prompt)

        # Step 1: Fit conversation history (keep most recent messages)
        history_budget = self.budget.conversation_history_reserve
        truncated_history = self._truncate_history(
            conversation_history, history_budget
        )

        # Step 2: Fit retrieved chunks (already ranked by reranker)
        context_budget = self.budget.retrieved_context_limit
        included_chunks = self._fit_chunks(chunks, context_budget)

        # Step 3: Build the retrieved context block
        context_block = self._format_context_block(included_chunks)

        # Step 4: Inject context into system prompt
        final_prompt = (
            f"{system_prompt}\n\n"
            f"## Retrieved Context\n\n{context_block}"
        )

        return final_prompt, truncated_history, included_chunks

    def _truncate_history(
        self,
        messages: list[dict[str, str]],
        budget: int,
    ) -> list[dict[str, str]]:
        """Keep the most recent messages that fit within the token budget."""
        result: list[dict[str, str]] = []
        used = 0

        for msg in reversed(messages):
            msg_tokens = self._count_tokens(msg.get("content", ""))
            if used + msg_tokens > budget:
                break
            result.append(msg)
            used += msg_tokens

        result.reverse()
        return result

    def _fit_chunks(
        self,
        chunks: list[RetrievedChunk],
        budget: int,
    ) -> list[RetrievedChunk]:
        """Include as many top-ranked chunks as fit in the budget."""
        included: list[RetrievedChunk] = []
        used = 0

        for chunk in chunks:
            chunk_tokens = self._count_tokens(chunk.text)
            # Reserve tokens for the chunk header (source attribution)
            header_tokens = 30
            if used + chunk_tokens + header_tokens > budget:
                break
            included.append(chunk)
            used += chunk_tokens + header_tokens

        return included

    def _format_context_block(self, chunks: list[RetrievedChunk]) -> str:
        """Format chunks into a structured context block for the LLM."""
        if not chunks:
            return "No relevant context was retrieved."

        parts: list[str] = []
        for i, chunk in enumerate(chunks, 1):
            source_label = _source_label(chunk)
            parts.append(
                f"[Source {i}: {source_label}]\n{chunk.text}\n"
            )
        return "\n".join(parts)

    def _count_tokens(self, text: str) -> int:
        return len(self.enc.encode(text))


def _source_label(chunk: RetrievedChunk) -> str:
    """Human-readable source label for a chunk."""
    title = chunk.metadata.get("title", chunk.source_id)
    type_labels = {
        "document": "Document",
        "email": "Email",
        "crm_note": "CRM Note",
        "transcript": "Meeting Transcript",
        "activity": "Activity",
    }
    type_label = type_labels.get(chunk.source_type, chunk.source_type)
    return f"{type_label} - {title} ({chunk.created_at[:10]})"
```

### 8.2 Truncation priority

When the total content exceeds the context window:

1. Drop the oldest conversation history messages first (keep the most recent exchanges).
2. Drop the lowest-ranked retrieved chunks (the reranker already sorted by composite score).
3. The system prompt is never truncated. If it alone exceeds the budget, that is a configuration error.

### 8.3 Budget allocation rationale

| Component | Budget | Rationale |
|---|---|---|
| System prompt | 2,000 tokens | Agent instructions, persona, tool descriptions |
| Conversation history | 8,000 tokens | Last several turns of multi-turn conversation |
| Retrieved context | 12,000 tokens | 5-8 chunks at 512 tokens each, plus headers |
| Response reserve | 4,000 tokens | Space for the LLM to generate an answer |
| Remaining | ~94,000 tokens | Available headroom for longer conversations or additional tool call results |

---

## 9. Citation Tracking

Every retrieved chunk that appears in the LLM context is tracked as a potential citation. The LLM response references specific sources, and the citation tracker maps those references back to the original artifacts.

### 9.1 Citation model

```python
"""sidecar/rag/citations.py"""

from __future__ import annotations

from pydantic import BaseModel

from sidecar.rag.retrieval import RetrievedChunk


class Citation(BaseModel):
    """A citation linking a response passage to a source artifact."""
    source_type: str         # "document", "email", "crm_note", "transcript", "activity"
    source_id: str           # Platform artifact ID
    title: str               # Human-readable title
    excerpt: str             # The chunk text used as context (truncated to 200 chars)
    relevance_score: float   # Composite score from reranker
    source_date: str | None  # When the source was created
    chunk_index: int         # Position within the source document
    metadata: dict           # Additional source-specific metadata


class CitationTracker:
    """Maps retrieved chunks to citations for the response."""

    def build_citations(
        self,
        included_chunks: list[RetrievedChunk],
    ) -> list[Citation]:
        """Convert included chunks to citation objects.

        These are attached to the LLM response so the advisor can
        verify and navigate to the original source.
        """
        citations: list[Citation] = []
        seen_sources: set[str] = set()

        for chunk in included_chunks:
            # Deduplicate: if multiple chunks come from the same source,
            # use the highest-scoring one
            source_key = f"{chunk.source_type}:{chunk.source_id}"
            if source_key in seen_sources:
                continue
            seen_sources.add(source_key)

            title = chunk.metadata.get("title") or chunk.metadata.get("subject") or chunk.source_id

            citations.append(
                Citation(
                    source_type=chunk.source_type,
                    source_id=chunk.source_id,
                    title=title,
                    excerpt=chunk.text[:200].strip(),
                    relevance_score=round(chunk.relevance_score, 4),
                    source_date=chunk.created_at[:10] if chunk.created_at else None,
                    chunk_index=chunk.chunk_index,
                    metadata={
                        k: v
                        for k, v in chunk.metadata.items()
                        if k in ("sender", "recipients", "participants",
                                 "meeting_date", "file_type", "section")
                    },
                )
            )

        return citations
```

### 9.2 Integration with the agent response

```python
"""Example: wiring citations into the Hazel copilot response."""

from sidecar.rag.citations import CitationTracker, Citation
from sidecar.rag.context import ContextWindowBuilder
from sidecar.rag.reranking import ChunkReranker
from sidecar.rag.retrieval import RetrieverService, AccessScope


async def answer_with_citations(
    query: str,
    access_scope: AccessScope,
    conversation_history: list[dict[str, str]],
    retriever: RetrieverService,
    reranker: ChunkReranker,
    context_builder: ContextWindowBuilder,
    citation_tracker: CitationTracker,
    system_prompt: str,
) -> tuple[str, list[dict[str, str]], list[Citation]]:
    """Full RAG pipeline: retrieve -> rerank -> build context -> track citations.

    Returns the final system prompt, truncated history, and citations.
    The caller passes these to the Pydantic AI agent for LLM generation.
    """
    # 1. Retrieve top-20 tenant-scoped chunks
    raw_chunks = await retriever.retrieve(
        query=query,
        access_scope=access_scope,
        top_k=20,
    )

    # 2. Rerank to top-8
    reranked = reranker.rerank(
        chunks=raw_chunks,
        query_client_id=None,   # Extracted from query intent if available
        query_household_id=None,
    )

    # 3. Build context window
    final_prompt, truncated_history, included_chunks = context_builder.build_context(
        system_prompt=system_prompt,
        conversation_history=conversation_history,
        chunks=reranked,
    )

    # 4. Build citations from included chunks
    citations = citation_tracker.build_citations(included_chunks)

    return final_prompt, truncated_history, citations
```

The `citations` list is attached to the `HazelCopilot` response model defined in the sidecar spec, so the platform can render source links in the advisor UI.

---

## 10. Index Maintenance

### 10.1 Staleness and garbage collection

Indexed chunks can become stale when source documents are updated or deleted. A periodic garbage collection job handles three cases:

1. **Document updated**: The platform sends a new `document.updated` event, which triggers re-indexing via the same `index_document_job`. The pipeline deletes all existing chunks for that `source_id` and inserts fresh ones. This is handled by the idempotent upsert logic in section 4.
2. **Document deleted**: The platform sends a `document.deleted` event, which triggers `delete_source_job` to remove all chunks.
3. **Orphaned chunks**: Chunks whose source artifacts no longer exist in the platform. A periodic sweep detects and removes these.

### 10.2 Garbage collection job

```python
"""sidecar/jobs/gc_jobs.py"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import asyncpg

from sidecar.platform_client import PlatformClient
from sidecar.rag.retrieval import AccessScope


async def gc_stale_chunks_job(
    ctx: dict,
    tenant_id: str,
    stale_threshold_days: int = 90,
    batch_size: int = 500,
) -> dict:
    """Garbage collection: remove chunks for sources that no longer exist.

    Runs periodically (daily recommended) per tenant.

    Strategy:
    1. Find chunks older than the stale threshold that haven't been re-indexed.
    2. Verify each source_id still exists in the platform.
    3. Delete chunks for sources that are gone.
    """
    db_pool: asyncpg.Pool = ctx["db_pool"]
    platform_client: PlatformClient = ctx["platform_client"]

    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_threshold_days)
    deleted_total = 0
    verified_total = 0

    async with db_pool.acquire() as conn:
        # Get distinct source_ids that haven't been updated recently
        stale_sources = await conn.fetch(
            """
            SELECT DISTINCT source_type, source_id
            FROM rag_chunks
            WHERE tenant_id = $1
              AND updated_at < $2
            ORDER BY source_id
            LIMIT $3
            """,
            tenant_id,
            cutoff,
            batch_size,
        )

        for row in stale_sources:
            source_type = row["source_type"]
            source_id = row["source_id"]

            # Check if the source still exists in the platform
            exists = await _source_exists(
                platform_client, tenant_id, source_type, source_id
            )

            if not exists:
                result = await conn.execute(
                    """
                    DELETE FROM rag_chunks
                    WHERE tenant_id = $1 AND source_id = $2
                    """,
                    tenant_id,
                    source_id,
                )
                count = int(result.split()[-1])
                deleted_total += count
            else:
                verified_total += 1

    return {
        "tenant_id": tenant_id,
        "sources_checked": len(stale_sources),
        "sources_verified": verified_total,
        "chunks_deleted": deleted_total,
    }


async def gc_refresh_stale_embeddings_job(
    ctx: dict,
    tenant_id: str,
    older_than_days: int = 180,
    batch_size: int = 100,
) -> dict:
    """Re-index sources with embeddings older than a threshold.

    If the embedding model changes or improves, this job forces
    re-embedding of old content so retrieval quality stays consistent.
    """
    db_pool: asyncpg.Pool = ctx["db_pool"]
    arq_redis = ctx["arq_redis"]

    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)

    async with db_pool.acquire() as conn:
        stale = await conn.fetch(
            """
            SELECT DISTINCT source_type, source_id
            FROM rag_chunks
            WHERE tenant_id = $1
              AND updated_at < $2
            ORDER BY updated_at ASC
            LIMIT $3
            """,
            tenant_id,
            cutoff,
            batch_size,
        )

    enqueued = 0
    for row in stale:
        await arq_redis.enqueue_job(
            "reindex_source_job",
            tenant_id=tenant_id,
            source_id=row["source_id"],
            source_type=row["source_type"],
        )
        enqueued += 1

    return {
        "tenant_id": tenant_id,
        "sources_enqueued_for_reindex": enqueued,
    }


async def _source_exists(
    client: PlatformClient,
    tenant_id: str,
    source_type: str,
    source_id: str,
) -> bool:
    """Check if a source artifact still exists in the platform."""
    try:
        scope = AccessScope(tenant_id=tenant_id, visibility_mode="full_tenant")
        if source_type == "document":
            await client.get_document_metadata(source_id, scope)
        elif source_type == "email":
            # Platform exposes an email metadata check
            await client.get_email_metadata(source_id, scope)
        elif source_type == "crm_note":
            await client.get_crm_note_metadata(source_id, scope)
        elif source_type == "transcript":
            await client.get_document_metadata(source_id, scope)
        else:
            # Unknown source type — assume it exists to avoid accidental deletion
            return True
        return True
    except Exception:
        # 404 or similar — source no longer exists
        return False
```

### 10.3 ARQ worker registration

```python
"""sidecar/workers/settings.py — ARQ worker configuration."""

from arq import cron
from arq.connections import RedisSettings

from sidecar.jobs.indexing_jobs import index_document_job, delete_source_job
from sidecar.jobs.gc_jobs import gc_stale_chunks_job, gc_refresh_stale_embeddings_job


class WorkerSettings:
    functions = [
        index_document_job,
        delete_source_job,
        gc_stale_chunks_job,
        gc_refresh_stale_embeddings_job,
    ]

    cron_jobs = [
        # Run GC daily at 3:00 AM UTC
        cron(gc_stale_chunks_job, hour=3, minute=0, run_at_startup=False),
        cron(gc_refresh_stale_embeddings_job, hour=4, minute=0, run_at_startup=False),
    ]

    redis_settings = RedisSettings()

    # Retry transient failures up to 3 times
    max_tries = 3

    # Job timeout: 10 minutes for indexing, 30 minutes for GC
    job_timeout = 600
```

### 10.4 Maintenance operations summary

| Operation | Trigger | Behavior |
|---|---|---|
| Index new source | Platform event (`document.uploaded`, `email.synced`, etc.) | Chunk, embed, insert into `rag_chunks` |
| Re-index updated source | Platform event (`document.updated`, etc.) | Delete existing chunks for `source_id`, re-chunk, re-embed, insert |
| Delete source | Platform event (`document.deleted`, etc.) | Delete all chunks for `source_id` |
| Garbage collect orphans | Daily cron (3:00 AM UTC) | Find old chunks, verify source exists in platform, delete orphans |
| Refresh stale embeddings | Daily cron (4:00 AM UTC) | Find chunks with old embeddings, enqueue re-index jobs |

### 10.5 Schema migration for updated_at tracking

The `updated_at` column in `rag_chunks` is set to `now()` on insert. For re-indexed sources, the delete-then-insert pattern in `IndexingPipeline.index_source` naturally resets `updated_at` to the current time. This means `updated_at` reflects when the chunk was last indexed, not when the source was created, which is the correct semantics for staleness detection.

---

## Appendix: Full Pipeline Flow

```text
Platform Event (document uploaded / email synced / note created)
    |
    v
Sidecar webhook receiver (/internal/indexing/event)
    |
    v
ARQ job enqueued (index_document_job)
    |
    v
Worker picks up job
    |
    +-- Fetch text content (from event payload or platform client)
    +-- TextChunker: split into 512-token chunks with 64-token overlap
    +-- EmbeddingClient: batch embed all chunks via text-embedding-3-small (1024 dims)
    +-- Delete existing chunks for this source_id (idempotent)
    +-- Insert new chunks into rag_chunks with full metadata
    |
    v
Chunks indexed and available for retrieval

---

Advisor asks a question via /ai/chat
    |
    v
Sidecar receives query + AccessScope
    |
    +-- EmbeddingClient: embed query text
    +-- RetrieverService: vector search with tenant_id + access scope WHERE clauses
    |     -> SELECT ... FROM rag_chunks WHERE tenant_id = $1 AND (scope filters)
    |        ORDER BY embedding <=> query_vector LIMIT 20
    |
    +-- ChunkReranker: score by relevance (0.60) + recency (0.25) + association (0.15)
    |     -> Return top 8
    |
    +-- ContextWindowBuilder: fit chunks + conversation history into token budget
    |     -> Truncate oldest history first, then lowest-ranked chunks
    |
    +-- CitationTracker: build Citation objects from included chunks
    |
    +-- Pydantic AI Agent: generate answer with system prompt + context + history
    |
    v
HazelCopilot response with answer, citations, confidence, recommended_actions
```
