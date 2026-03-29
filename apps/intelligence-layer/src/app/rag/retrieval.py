"""
app/rag/retrieval.py — pgvector retrieval with access-scope
filtering.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import asyncpg

from app.models.access_scope import AccessScope
from app.rag.embeddings import EmbeddingClient

logger = logging.getLogger("sidecar.rag.retrieval")


@dataclass
class RetrievedChunk:
    """A single chunk returned by the retriever."""

    chunk_id: str
    source_id: str
    source_type: str
    chunk_index: int
    body: str
    score: float
    token_count: int
    household_id: str | None = None
    client_id: str | None = None
    advisor_id: str | None = None
    account_id: str | None = None
    visibility_tags: list[str] = field(
        default_factory=list,
    )
    meta: dict[str, Any] = field(default_factory=dict)


def _build_scope_filter(
    scope: AccessScope,
    param_offset: int,
) -> tuple[list[str], list[Any]]:
    """
    Build WHERE clauses and params for access-scope
    filtering.

    *param_offset* is the next $N placeholder index.
    Returns (clauses, params).
    """
    if scope.visibility_mode == "full_tenant":
        return [], []

    clauses: list[str] = []
    params: list[Any] = []
    idx = param_offset

    or_parts: list[str] = []

    if scope.household_ids:
        or_parts.append(
            f"household_id = ANY(${idx}::text[])"
        )
        params.append(scope.household_ids)
        idx += 1

    if scope.client_ids:
        or_parts.append(
            f"client_id = ANY(${idx}::text[])"
        )
        params.append(scope.client_ids)
        idx += 1

    if scope.advisor_ids:
        or_parts.append(
            f"advisor_id = ANY(${idx}::text[])"
        )
        params.append(scope.advisor_ids)
        idx += 1

    if scope.account_ids:
        or_parts.append(
            f"account_id = ANY(${idx}::text[])"
        )
        params.append(scope.account_ids)
        idx += 1

    # Null-ownership catch-all: chunks that have no
    # ownership columns set are visible to every scoped
    # actor inside the tenant.
    or_parts.append(
        "(household_id IS NULL"
        " AND client_id IS NULL"
        " AND advisor_id IS NULL"
        " AND account_id IS NULL)"
    )

    if or_parts:
        combined = " OR ".join(or_parts)
        clauses.append(f"({combined})")

    return clauses, params


class RetrieverService:
    """Vector search with mandatory tenant + scope."""

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        embedder: EmbeddingClient,
    ) -> None:
        self._pool = db_pool
        self._embedder = embedder

    async def retrieve(
        self,
        query: str,
        access_scope: AccessScope,
        tenant_id: str | None = None,
        top_k: int = 20,
        source_types: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        """
        Embed *query* and run a cosine-similarity search
        against rag_chunks filtered by tenant + scope.
        """
        query_vec = await self._embedder.embed_single(query)
        vec_literal = ",".join(
            f"{v:.8f}" for v in query_vec
        )
        vec_param = f"[{vec_literal}]"

        # -- mandatory tenant filter --
        where_clauses: list[str] = [
            "tenant_id = $1",
        ]
        params: list[Any] = [uuid.UUID(tenant_id)]
        next_idx = 2

        # -- source_type filter --
        if source_types:
            where_clauses.append(
                f"source_type = ANY(${next_idx}::text[])"
            )
            params.append(source_types)
            next_idx += 1

        # -- access-scope filter --
        scope_clauses, scope_params = _build_scope_filter(
            access_scope, next_idx
        )
        where_clauses.extend(scope_clauses)
        params.extend(scope_params)
        next_idx += len(scope_params)

        where_sql = " AND ".join(where_clauses)

        sql = (
            "SELECT id, source_id, source_type,"
            " chunk_index, body, token_count,"
            " household_id, client_id,"
            " advisor_id, account_id,"
            " visibility_tags, meta,"
            f" embedding <=> ${next_idx}::vector"
            " AS distance"
            " FROM rag_chunks"
            f" WHERE {where_sql}"
            " ORDER BY distance"
            f" LIMIT ${next_idx + 1}"
        )
        params.append(vec_param)
        params.append(top_k)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        results: list[RetrievedChunk] = []
        for row in rows:
            results.append(
                RetrievedChunk(
                    chunk_id=str(row["id"]),
                    source_id=row["source_id"],
                    source_type=row["source_type"],
                    chunk_index=row["chunk_index"],
                    body=row["body"],
                    score=1.0 - float(row["distance"]),
                    token_count=row["token_count"],
                    household_id=row["household_id"],
                    client_id=row["client_id"],
                    advisor_id=row["advisor_id"],
                    account_id=row["account_id"],
                    visibility_tags=(
                        row["visibility_tags"] or []
                    ),
                    meta=row["meta"] or {},
                )
            )
        return results
