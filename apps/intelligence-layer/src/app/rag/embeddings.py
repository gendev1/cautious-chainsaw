"""
app/rag/embeddings.py — Async batch embedding client with concurrency control.
"""
from __future__ import annotations

import asyncio
from collections.abc import Sequence

import httpx
import numpy as np
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class EmbeddingSettings(BaseSettings):
    openai_api_key: str
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1024
    embedding_batch_size: int = 64
    embedding_max_concurrent: int = 4
    embedding_base_url: str = "https://api.openai.com/v1"

    model_config = SettingsConfigDict(
        env_prefix="SIDECAR_",
    )


class EmbeddingResult(BaseModel):
    index: int
    embedding: list[float]
    token_count: int


class EmbeddingClient:
    """Async batch embedding client with concurrency control."""

    def __init__(self, settings: EmbeddingSettings) -> None:
        self._settings = settings
        self._semaphore = asyncio.Semaphore(
            settings.embedding_max_concurrent
        )
        self._http = httpx.AsyncClient(
            base_url=settings.embedding_base_url,
            headers={
                "Authorization": (
                    f"Bearer {settings.openai_api_key}"
                ),
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(60.0, connect=10.0),
        )

    async def embed_texts(
        self, texts: Sequence[str]
    ) -> list[EmbeddingResult]:
        """Embed texts in batches with concurrency control."""
        batches = self._split_batches(list(texts))
        tasks = [
            self._embed_batch(batch, offset)
            for offset, batch in batches
        ]
        nested = await asyncio.gather(*tasks)
        results = [
            r for batch_results in nested for r in batch_results
        ]
        results.sort(key=lambda r: r.index)
        return results

    async def embed_single(self, text: str) -> list[float]:
        """Embed a single text. Convenience for query embedding."""
        results = await self.embed_texts([text])
        return results[0].embedding

    def _split_batches(
        self, texts: list[str]
    ) -> list[tuple[int, list[str]]]:
        bs = self._settings.embedding_batch_size
        return [
            (i, texts[i : i + bs])
            for i in range(0, len(texts), bs)
        ]

    async def _embed_batch(
        self, texts: list[str], offset: int
    ) -> list[EmbeddingResult]:
        async with self._semaphore:
            resp = await self._http.post(
                "/embeddings",
                json={
                    "model": self._settings.embedding_model,
                    "input": texts,
                    "dimensions": (
                        self._settings.embedding_dimensions
                    ),
                },
            )
            resp.raise_for_status()
            data = resp.json()

            results: list[EmbeddingResult] = []
            for item in data["data"]:
                results.append(
                    EmbeddingResult(
                        index=offset + item["index"],
                        embedding=item["embedding"],
                        token_count=data["usage"][
                            "total_tokens"
                        ],
                    )
                )
            return results

    async def close(self) -> None:
        await self._http.aclose()


def verify_normalized(
    embedding: list[float], tolerance: float = 1e-3
) -> bool:
    """Check if an embedding vector is L2-normalized."""
    norm = float(np.linalg.norm(embedding))
    return abs(norm - 1.0) < tolerance


def normalize(embedding: list[float]) -> list[float]:
    """L2-normalize an embedding vector."""
    arr = np.array(embedding, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm == 0:
        return embedding
    return (arr / norm).tolist()
