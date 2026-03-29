# Implementation Context: RAG Pipeline

## Chosen Approach

Approach A: Bottom-Up, Pure Modules First. Build pure Python modules (chunking, reranking, context, citations) → embedding client → Docker+SQL → indexing pipeline → retrieval service → jobs → router → DI wiring → cleanup → tests.

## Implementation Order

### Step 1: Chunking
- **Files:** `src/app/rag/chunking.py` (new), `src/app/rag/source_chunkers.py` (new)
- **What:** TextChunker with 512-token chunks, 64-token overlap, tiktoken cl100k_base. ChunkMetadata and Chunk dataclasses. Source-specific chunkers: DocumentChunker, EmailChunker, CRMNoteChunker, TranscriptChunker.
- **Verify:** Chunking produces correct token counts and overlap.

### Step 2: Reranking
- **Files:** `src/app/rag/reranking.py` (new)
- **What:** ChunkReranker with composite scoring (0.60 relevance + 0.25 recency + 0.15 association). RerankConfig dataclass. Exponential recency decay with 30-day half-life.
- **Verify:** Scoring formula and ranking order are correct.

### Step 3: Context Window Builder
- **Files:** `src/app/rag/context.py` (new)
- **What:** ContextWindowBuilder with token budgeting (120K total, 2K system, 8K history, 12K context, 4K response). Truncation priority: oldest history → lowest chunks → never truncate system prompt. ContextBudget dataclass.
- **Verify:** Token budget allocation and truncation work correctly.

### Step 4: Citation Tracker
- **Files:** `src/app/rag/citations.py` (new)
- **What:** CitationTracker converting RetrievedChunk to Citation. Deduplication by source, excerpt truncation to 200 chars. Citation Pydantic model.
- **Verify:** Deduplication and excerpt truncation correct.

### Step 5: Embedding Client
- **Files:** `src/app/rag/embeddings.py` (new)
- **What:** EmbeddingClient with httpx, batch embedding, concurrency semaphore. EmbeddingSettings, EmbeddingResult. Normalization verification and fix utilities.
- **Verify:** Batch splitting and result ordering correct (mock httpx for unit tests).

### Step 6: Docker + SQL Migration
- **Files:** `docker-compose.test.yml` (new at project root), `migrations/001_rag_chunks.sql` (new)
- **What:** pgvector Docker container. SQL schema with rag_chunks table, all indexes.
- **Verify:** Container starts, schema applies.

### Step 7: Indexing Pipeline
- **Files:** `src/app/rag/indexing.py` (new)
- **What:** IndexingPipeline with chunk→embed→upsert. Idempotent delete-then-insert per source_id within transaction. IndexingResult dataclass.
- **Verify:** Integration test with Docker pgvector.

### Step 8: Retrieval Service
- **Files:** `src/app/rag/retrieval.py` (new)
- **What:** RetrieverService with tenant-scoped vector search. Builds WHERE clauses from AccessScope. RetrievedChunk dataclass. Uses existing AccessScope model.
- **Verify:** Integration test with Docker pgvector + scoped queries.

### Step 9: ARQ Jobs
- **Files:** `src/app/jobs/indexing_jobs.py` (new), `src/app/jobs/gc_jobs.py` (new)
- **What:** index_document_job, delete_source_job, gc_stale_chunks_job, gc_refresh_stale_embeddings_job.
- **Verify:** Job function signatures match ARQ expectations.

### Step 10: Indexing Router
- **Files:** `src/app/routers/indexing.py` (new)
- **What:** POST /internal/indexing/event webhook receiver. IndexEvent and DeleteEvent models. Enqueues ARQ jobs.
- **Verify:** Endpoint accepts events and returns enqueued status.

### Step 11: DI + Lifespan Updates
- **Files:** `src/app/dependencies.py` (edit), `src/app/main.py` (edit)
- **What:** Add asyncpg pool init/close to lifespan. Add EmbeddingClient init/close. Add indexing router. Add get_db_pool, get_embedding_client dependencies.
- **Verify:** App boots with asyncpg pool.

### Step 12: Dependencies + Worker
- **Files:** `pyproject.toml` (edit), `src/app/jobs/worker.py` (edit)
- **What:** Add asyncpg, tiktoken, numpy to deps. Register indexing and GC jobs in worker.
- **Verify:** uv sync succeeds.

### Step 13: Cleanup Stubs
- **Files:** Delete `src/app/rag/retriever.py`, `src/app/services/vector_store.py`. Update `src/app/agents/deps.py`, `src/app/dependencies.py` to remove old Retriever/VectorStore references.
- **Verify:** No import errors, existing tests still pass.

### Step 14: Tests
- **Files:** `tests/test_chunking.py`, `tests/test_reranking.py`, `tests/test_context.py`, `tests/test_citations.py`, `tests/test_embeddings.py`, `tests/test_indexing_integration.py`, `tests/test_retrieval_integration.py`
- **Verify:** All tests pass. Integration tests require Docker pgvector.

## External Dependencies

| Package | Purpose | Already in pyproject.toml? |
|---|---|---|
| asyncpg | Postgres async driver | No |
| tiktoken | Token counting for chunking | No |
| numpy | Embedding normalization | No |
| pgvector (Docker) | Vector similarity search | Docker only |

## Test Cases

- **T1:** TextChunker splits 1024-token text into 3 chunks (512, 512, ~overlap)
- **T2:** TextChunker preserves metadata on every chunk
- **T3:** ChunkReranker ranks by composite score correctly
- **T4:** Recency decay: 30-day-old chunk gets ~0.5 score, today gets ~1.0
- **T5:** Association score: client match = 1.0, advisor match = 0.5, none = 0.0
- **T6:** ContextWindowBuilder fits chunks within budget
- **T7:** ContextWindowBuilder truncates oldest history first
- **T8:** CitationTracker deduplicates by source_id
- **T9:** Citation excerpt truncated to 200 chars
- **T10:** EmbeddingClient batch splitting correct
- **T11:** [Integration] IndexingPipeline upserts and retrieves chunks from pgvector
- **T12:** [Integration] RetrieverService returns tenant-scoped results
- **T13:** [Integration] Scoped retrieval excludes unauthorized chunks
- **T14:** verify_normalized detects unnormalized vectors

## Scope Boundaries

### In scope
- All files under src/app/rag/ (new modules)
- src/app/jobs/indexing_jobs.py, gc_jobs.py (new)
- src/app/routers/indexing.py (new)
- docker-compose.test.yml, migrations/ (new)
- src/app/dependencies.py, main.py, worker.py (edits)
- pyproject.toml (add deps)
- Delete retriever.py, vector_store.py stubs
- All new test files

### Out of scope
- Full platform client document/email/note fetch methods
- Langfuse instrumentation
- PDF text extraction (pdfplumber/pymupdf)
- Production deployment of pgvector
- Kafka event consumers
