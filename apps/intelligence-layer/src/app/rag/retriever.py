"""
app/rag/retriever.py — Vector search with scope filtering.

Stub implementation. Full implementation in a later spec.
"""
from __future__ import annotations

from typing import Any

from app.models.access_scope import AccessScope
from app.services.vector_store import VectorStore


class Retriever:
    """
    Wraps VectorStore with mandatory tenant + scope filtering.
    Stub: search returns empty list.
    """

    def __init__(self, vector_store: VectorStore) -> None:
        self._store = vector_store

    async def search(
        self,
        query: str,
        tenant_id: str,
        access_scope: AccessScope,
        top_k: int = 20,
    ) -> list[Any]:
        metadata_filter = access_scope.to_vector_filter(tenant_id)
        return await self._store.similarity_search(
            query=query,
            filter=metadata_filter,
            limit=top_k,
        )
