# Discovery: RAG Pipeline

## Requirements

- **R1: Embedding Client** — `app/rag/embeddings.py` with `EmbeddingClient` using OpenAI `text-embedding-3-small`, 1024 dimensions, batch embedding with concurrency control (semaphore), `embed_texts()` and `embed_single()` methods. `EmbeddingSettings` for configuration. `EmbeddingResult` model. Normalization verification and re-normalization utility.
- **R2: Text Chunking** — `app/rag/chunking.py` with `TextChunker` (512-token chunks, 64-token overlap, tiktoken cl100k_base). `ChunkMetadata` dataclass with source_type, source_id, tenant_id, ownership IDs, visibility_tags. `Chunk` dataclass with text, chunk_index, token_count, metadata.
- **R3: Source-Specific Chunkers** — `app/rag/source_chunkers.py` with `DocumentChunker`, `EmailChunker`, `CRMNoteChunker`, `TranscriptChunker`. Each wraps TextChunker with source-specific pre-processing.
- **R4: Index Schema** — SQL migration creating `rag_chunks` table with pgvector `vector(1024)` column, tenant isolation indexes, composite scope indexes, IVFFlat vector similarity index, GIN index for visibility_tags, unique constraint on (tenant_id, source_id, chunk_index).
- **R5: Indexing Pipeline** — `app/rag/indexing.py` with `IndexingPipeline` class. Chunks text, embeds, upserts into pgvector. Idempotent via delete-then-insert per source_id within a transaction. `IndexingResult` dataclass.
- **R6: ARQ Indexing Jobs** — `app/jobs/indexing_jobs.py` with `index_document_job` and `delete_source_job`. Triggered by platform events.
- **R7: Indexing Webhook Router** — `app/routers/indexing.py` with `POST /internal/indexing/event` endpoint. Receives platform events, enqueues ARQ jobs. `IndexEvent` and `DeleteEvent` models.
- **R8: Tenant-Scoped Retrieval** — `app/rag/retrieval.py` with `RetrieverService` class. Embeds query, builds WHERE clause with mandatory tenant_id + access scope filters, runs pgvector cosine distance search. `RetrievedChunk` dataclass. `AccessScope` dataclass (local to RAG, references the shared model).
- **R9: Reranking** — `app/rag/reranking.py` with `ChunkReranker`. Scores by relevance (0.60), recency (0.25), association (0.15). Exponential recency decay with 30-day half-life. Returns top-K (default 8). `RerankConfig` dataclass.
- **R10: Context Window Management** — `app/rag/context.py` with `ContextWindowBuilder`. Token budgeting (120K total, 2K system prompt, 8K history, 12K context, 4K response). Truncation priority: oldest history first, then lowest-ranked chunks. `ContextBudget` dataclass.
- **R11: Citation Tracking** — `app/rag/citations.py` with `CitationTracker`. Converts included chunks to `Citation` objects, deduplicates by source, truncates excerpts to 200 chars.
- **R12: Garbage Collection Jobs** — `app/jobs/gc_jobs.py` with `gc_stale_chunks_job` (remove orphaned chunks) and `gc_refresh_stale_embeddings_job` (re-index old embeddings). Daily cron schedule.
- **R13: Full Pipeline Integration** — `answer_with_citations()` function wiring retrieve → rerank → context → citations for agent use.

## Decisions Already Made

- **D1:** OpenAI text-embedding-3-small at 1024 dimensions.
- **D2:** pgvector for vector storage with asyncpg.
- **D3:** 512-token chunks with 64-token overlap using tiktoken cl100k_base.
- **D4:** IVFFlat index (lists=100) for vector similarity.
- **D5:** Full asyncpg + Docker pgvector for tests (user chose this).
- **D6:** Module paths follow existing project structure (app/rag/, not sidecar/rag/).
- **D7:** Reranking formula: 0.60 relevance + 0.25 recency + 0.15 association.

## Constraints

- **C1:** Builds on core infrastructure (spec 01) and agents-and-tools (spec 02). Must not break existing 58 tests.
- **C2:** Existing `app/rag/retriever.py` is a stub — will be replaced by `retrieval.py` with full implementation.
- **C3:** New dependencies needed: asyncpg, tiktoken, numpy, pgvector (Docker).
- **C4:** The spec's `AccessScope` in retrieval.py is a local dataclass — should use the existing `app/models/access_scope.py` Pydantic model instead.
- **C5:** Docker pgvector needed for integration tests. Docker Compose for test infrastructure.

## Open Questions

- [x] **Q1:** DB layer approach — **Full asyncpg with Docker pgvector.**
- [x] **Q2:** Module paths — **Use app/rag/ following existing project structure.**
