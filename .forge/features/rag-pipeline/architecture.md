# Architecture: RAG Pipeline

## Approach A: Bottom-Up, Pure Modules First

Build pure-Python modules first (chunking, reranking, context, citations), then DB-dependent modules (embeddings, indexing, retrieval), then integration (router, jobs, DI wiring).

**Implementation order:**
1. Chunking (app/rag/chunking.py, source_chunkers.py) — pure Python + tiktoken
2. Reranking (app/rag/reranking.py) — pure Python + math
3. Context window builder (app/rag/context.py) — pure Python + tiktoken
4. Citation tracker (app/rag/citations.py) — pure Python
5. Embedding client (app/rag/embeddings.py) — httpx to OpenAI API
6. Docker + SQL migration — docker-compose.test.yml + migrations/001_rag_chunks.sql
7. Indexing pipeline (app/rag/indexing.py) — asyncpg + embedding client
8. Retrieval service (app/rag/retrieval.py) — asyncpg + embedding client
9. ARQ jobs (app/jobs/indexing_jobs.py, gc_jobs.py)
10. Indexing router (app/routers/indexing.py)
11. DI wiring + lifespan updates (dependencies.py, main.py)
12. Update pyproject.toml + worker registration
13. Delete old stubs (retriever.py, vector_store.py), update refs
14. Tests (pure module tests first, then integration tests with Docker pgvector)

**Trade-offs:**
- (+) Pure modules (steps 1-4) can be tested immediately without Docker
- (+) DB modules (steps 5-8) tested with Docker pgvector
- (+) Clear separation of testable layers
- (-) More steps, but each is small and focused

## Approach B: Full Vertical Slice

Build the entire pipeline end-to-end for one source type first.

**Trade-offs:**
- (+) Working end-to-end slice quickly
- (-) Requires Docker from step 1
- (-) Harder to test incrementally

## Recommendation

**Approach A: Bottom-Up, Pure Modules First.** Steps 1-4 produce immediately testable code with zero infrastructure. Steps 5-8 add the DB layer. This lets us catch logic bugs early before Docker is in the loop.

## Task Breakdown (recommended approach)

| Step | Files | Depends on |
|---|---|---|
| 1. Chunking | `app/rag/chunking.py`, `app/rag/source_chunkers.py` | tiktoken |
| 2. Reranking | `app/rag/reranking.py` | ��� |
| 3. Context builder | `app/rag/context.py` | tiktoken |
| 4. Citation tracker | `app/rag/citations.py` | — |
| 5. Embedding client | `app/rag/embeddings.py` | httpx, numpy |
| 6. Docker + SQL | `docker-compose.test.yml`, `migrations/001_rag_chunks.sql` | Docker |
| 7. Indexing pipeline | `app/rag/indexing.py` | steps 1, 5, 6 |
| 8. Retrieval service | `app/rag/retrieval.py` | steps 5, 6 |
| 9. ARQ jobs | `app/jobs/indexing_jobs.py`, `app/jobs/gc_jobs.py` | step 7 |
| 10. Indexing router | `app/routers/indexing.py` | step 9 |
| 11. DI + lifespan | `app/dependencies.py`, `app/main.py` | steps 5, 7, 8 |
| 12. Deps + worker | `pyproject.toml`, `app/jobs/worker.py` | all |
| 13. Cleanup stubs | delete `retriever.py`, `vector_store.py`, update refs | all |
| 14. Tests | all test files | all |
