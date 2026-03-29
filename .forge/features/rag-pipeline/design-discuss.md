# Design Discussion: RAG Pipeline

## Resolved Decisions

### DD1: Full Docker pgvector integration (blocking)
- **Decision:** Use asyncpg with Docker pgvector for both implementation and tests.
- **Rationale:** User chose full integration over stubs.
- **Constraint for architect:** docker-compose.test.yml with pgvector container. Tests connect to real Postgres. Lifespan manages asyncpg pool.

### DD2: Use existing app/ structure (blocking)
- **Decision:** Map spec paths (sidecar/rag/) to project paths (app/rag/).
- **Constraint for architect:** All modules under app/rag/, jobs under app/jobs/, routers under app/routers/.

### DD3: Reuse existing AccessScope model (informing)
- **Decision:** Use `app.models.access_scope.AccessScope` (Pydantic model) instead of creating a local dataclass in retrieval.py.
- **Constraint for architect:** RetrieverService accepts the existing AccessScope. The spec's local AccessScope dataclass is for illustration only.

### DD4: Replace stubs, don't bridge (informing)
- **Decision:** Delete the existing retriever.py stub and vector_store.py stub. The indexing pipeline goes direct to asyncpg. RetrieverService replaces the old Retriever.
- **Constraint for architect:** Update imports in agents/deps.py that reference the old Retriever. Update dependencies.py.

## Open Questions

None — all questions resolved.

## Summary for Architect

1. **Docker pgvector** for real database integration from the start.
2. **app/ structure** — not sidecar/ prefix.
3. **Reuse AccessScope** model, don't duplicate.
4. **Replace stubs** — retriever.py and vector_store.py are deprecated.
5. New deps: asyncpg, tiktoken, numpy.
6. The chunking, reranking, context, and citation modules are pure Python with no DB dependency — test them in isolation.
7. The indexing pipeline and retrieval need asyncpg+pgvector — test with Docker.
