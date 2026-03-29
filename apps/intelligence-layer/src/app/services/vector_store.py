"""
app/services/vector_store.py — Vector store abstraction.

Stub implementation. Full implementation in a later spec.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("sidecar.vector_store")


class VectorStore:
    """
    Abstraction over pgvector or Qdrant.
    Stub: all methods are no-ops or return empty results.
    """

    def __init__(
        self,
        provider: str = "pgvector",
        url: str = "",
        collection: str = "documents",
    ) -> None:
        self.provider = provider
        self.url = url
        self.collection = collection

    async def connect(self) -> None:
        logger.info("vector store connect (stub)", extra={"provider": self.provider})

    async def disconnect(self) -> None:
        logger.info("vector store disconnect (stub)")

    async def health_check(self) -> None:
        """Raises on failure. No-op in stub."""

    async def similarity_search(
        self,
        query: str,
        filter: dict | None = None,
        limit: int = 20,
    ) -> list[Any]:
        """Return matching chunks. Stub returns empty list."""
        return []
