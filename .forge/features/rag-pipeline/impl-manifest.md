# Implementation Manifest: RAG Pipeline

## Files Created

| File | Purpose |
|---|---|
| `src/app/rag/chunking.py` | TextChunker with token-aware splitting, overlap, metadata |
| `src/app/rag/source_chunkers.py` | DocumentChunker, EmailChunker, CRMNoteChunker, TranscriptChunker |
| `src/app/rag/reranking.py` | ChunkReranker with relevance/recency/association scoring |
| `src/app/rag/context.py` | ContextWindowBuilder with token budgeting |
| `src/app/rag/citations.py` | CitationTracker with deduplication |
| `src/app/rag/embeddings.py` | EmbeddingClient with batch embedding, normalization utils |
| `src/app/rag/indexing.py` | IndexingPipeline with asyncpg upsert |
| `src/app/rag/retrieval.py` | RetrieverService with tenant-scoped vector search |
| `src/app/jobs/indexing_jobs.py` | ARQ index_document_job, delete_source_job |
| `src/app/jobs/gc_jobs.py` | GC jobs for stale chunks and embedding refresh |
| `src/app/routers/indexing.py` | POST /internal/indexing/event webhook |
| `docker-compose.test.yml` | pgvector Docker container for tests |
| `migrations/001_rag_chunks.sql` | pgvector schema with all indexes |

## Files Modified

| File | Change |
|---|---|
| `src/app/main.py` | Added indexing router import and inclusion |
| `pyproject.toml` | Added asyncpg, tiktoken, numpy deps |

## Patterns Followed

- Pure Python modules (chunking, reranking, context, citations) testable without infrastructure
- DB modules (indexing, retrieval) use asyncpg with tenant-scoped queries
- Token counting via tiktoken cl100k_base encoding
- Reranking: 0.60 relevance + 0.25 recency + 0.15 association
- Context budgeting: 120K total, 2K system, 8K history, 12K context, 4K response

## Test Results

```
82 passed in 1.31s
Ruff: All checks passed!
```

## Blocked Tests

Integration tests (test_indexing_integration.py, test_retrieval_integration.py) are deferred — they require Docker pgvector running. The pure module tests cover all non-DB logic comprehensively.
