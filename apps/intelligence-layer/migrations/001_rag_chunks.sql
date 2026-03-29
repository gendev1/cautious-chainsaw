-- 001_rag_chunks.sql
-- RAG chunks table with pgvector support and
-- row-level access-scope columns.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS rag_chunks (
    id            BIGINT GENERATED ALWAYS AS IDENTITY
                    PRIMARY KEY,
    tenant_id     UUID        NOT NULL,
    source_type   TEXT        NOT NULL,
    source_id     TEXT        NOT NULL,
    chunk_index   INT         NOT NULL,
    body          TEXT        NOT NULL,
    embedding     vector(1024) NOT NULL,
    token_count   INT         NOT NULL DEFAULT 0,

    -- ownership / access-scope columns
    household_id  TEXT,
    client_id     TEXT,
    advisor_id    TEXT,
    account_id    TEXT,
    visibility_tags TEXT[]    NOT NULL DEFAULT '{}',

    -- flexible metadata
    meta          JSONB       NOT NULL DEFAULT '{}',

    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_chunk
        UNIQUE (tenant_id, source_id, chunk_index)
);

-- Tenant isolation (every query includes tenant_id)
CREATE INDEX IF NOT EXISTS idx_chunks_tenant
    ON rag_chunks (tenant_id);

-- Ownership filters
CREATE INDEX IF NOT EXISTS idx_chunks_household
    ON rag_chunks (tenant_id, household_id)
    WHERE household_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_chunks_client
    ON rag_chunks (tenant_id, client_id)
    WHERE client_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_chunks_advisor
    ON rag_chunks (tenant_id, advisor_id)
    WHERE advisor_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_chunks_account
    ON rag_chunks (tenant_id, account_id)
    WHERE account_id IS NOT NULL;

-- Source lookup (for delete-then-insert)
CREATE INDEX IF NOT EXISTS idx_chunks_source
    ON rag_chunks (tenant_id, source_id);

-- IVFFlat vector index (cosine distance)
-- Lists tuned for < 1M rows; re-tune after growth.
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_ivfflat
    ON rag_chunks
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Temporal queries / GC
CREATE INDEX IF NOT EXISTS idx_chunks_created_at
    ON rag_chunks (created_at);

-- Tag-based visibility
CREATE INDEX IF NOT EXISTS idx_chunks_visibility_tags
    ON rag_chunks
    USING GIN (visibility_tags);
