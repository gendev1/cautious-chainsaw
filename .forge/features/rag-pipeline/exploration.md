# Exploration: RAG Pipeline

## Most Similar Feature

The existing `app/rag/retriever.py` is a stub with the correct interface shape (search method with query, tenant_id, access_scope). It delegates to VectorStore which is also a stub. The RAG pipeline will replace both with real implementations backed by asyncpg+pgvector.

**What to reuse:** The Retriever interface pattern, AccessScope model from app/models/access_scope.py.
**What to replace:** app/rag/retriever.py (stub → full RetrieverService), app/services/vector_store.py (stub → no longer needed as indexing goes direct to asyncpg).

## Architecture Map

```
Platform Event → Webhook Router → ARQ Job Queue
                                       ↓
                              IndexingPipeline
                              ├── TextChunker (512 tokens, 64 overlap)
                              ├── EmbeddingClient (OpenAI, 1024 dims)
                              └── asyncpg upsert → rag_chunks table

Advisor Query → RetrieverService
                ├── EmbeddingClient (embed query)
                ├── asyncpg query (tenant + scope WHERE)
                ├── ChunkReranker (relevance + recency + association)
                ├── ContextWindowBuilder (token budgeting)
                └── CitationTracker → Response
```

## Structural Patterns

### asyncpg connection pool [grep-fallback]
- New pattern — no existing asyncpg usage in the codebase
- Will follow asyncpg.create_pool() pattern with lifespan management
- [insufficient-sample: 0 matches]

### Dataclass for pipeline data [grep-fallback]
- `@dataclass` with typed fields for Chunk, ChunkMetadata, RetrievedChunk, etc.
- Matches existing patterns in agents/deps.py, agents/base_deps.py
- Match count: 3 [insufficient-sample]

## Key Files

### Reference reading
- `src/app/rag/retriever.py` — existing stub (will be replaced)
- `src/app/services/vector_store.py` — existing stub (may be deprecated)
- `src/app/models/access_scope.py` — AccessScope model (reuse for retrieval)
- `src/app/dependencies.py` — DI wiring (needs asyncpg pool)
- `src/app/jobs/worker.py` — ARQ worker (needs new job registrations)

### Expected new files
- `src/app/rag/embeddings.py` — EmbeddingClient
- `src/app/rag/chunking.py` — TextChunker, ChunkMetadata, Chunk
- `src/app/rag/source_chunkers.py` — DocumentChunker, EmailChunker, etc.
- `src/app/rag/indexing.py` — IndexingPipeline
- `src/app/rag/retrieval.py` — RetrieverService, RetrievedChunk
- `src/app/rag/reranking.py` — ChunkReranker, RerankConfig
- `src/app/rag/context.py` — ContextWindowBuilder, ContextBudget
- `src/app/rag/citations.py` — CitationTracker, Citation
- `src/app/jobs/indexing_jobs.py` — ARQ indexing jobs
- `src/app/jobs/gc_jobs.py` — garbage collection jobs
- `src/app/routers/indexing.py` — webhook receiver
- `migrations/001_rag_chunks.sql` — pgvector schema
- `docker-compose.test.yml` — pgvector for tests
- `tests/conftest_db.py` — database fixtures

### Expected edits
- `src/app/rag/__init__.py` — update exports
- `src/app/dependencies.py` — add asyncpg pool init/close, embedding client
- `src/app/main.py` — add indexing router, asyncpg lifespan
- `src/app/jobs/worker.py` — register new jobs
- `pyproject.toml` — add asyncpg, tiktoken, numpy

### Expected deletions
- `src/app/rag/retriever.py` — replaced by retrieval.py
- `src/app/services/vector_store.py` — replaced by direct asyncpg access

### Expected tests
- `tests/test_chunking.py` — chunker token boundaries, overlap, metadata
- `tests/test_reranking.py` — scoring formula, recency decay, association
- `tests/test_context.py` — token budgeting, truncation priority
- `tests/test_citations.py` — deduplication, excerpt truncation
- `tests/test_indexing_integration.py` — full pipeline with Docker pgvector
- `tests/test_retrieval_integration.py` — scoped retrieval with pgvector
