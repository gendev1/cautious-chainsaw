"""Tests for embedding client — batch splitting, normalization."""
from __future__ import annotations

import numpy as np

from app.rag.embeddings import (
    EmbeddingSettings,
    normalize,
    verify_normalized,
)


def test_verify_normalized_accepts_unit_vector() -> None:
    """T14a: verify_normalized returns True for unit vectors."""
    vec = [0.0] * 1024
    vec[0] = 1.0
    assert verify_normalized(vec) is True


def test_verify_normalized_rejects_unnormalized() -> None:
    """T14b: verify_normalized returns False for non-unit vectors."""
    vec = [2.0] * 1024
    assert verify_normalized(vec) is False


def test_normalize_produces_unit_vector() -> None:
    """normalize produces a vector with L2 norm ~1.0."""
    vec = [3.0, 4.0] + [0.0] * 1022
    result = normalize(vec)
    norm = float(np.linalg.norm(result))
    assert abs(norm - 1.0) < 1e-5


def test_normalize_handles_zero_vector() -> None:
    """normalize returns zero vector unchanged."""
    vec = [0.0] * 1024
    result = normalize(vec)
    assert result == vec


def test_embedding_settings_defaults() -> None:
    """EmbeddingSettings has correct defaults."""
    settings = EmbeddingSettings(openai_api_key="test")
    assert settings.embedding_model == "text-embedding-3-small"
    assert settings.embedding_dimensions == 1024
    assert settings.embedding_batch_size == 64


def test_batch_splitting() -> None:
    """EmbeddingClient splits texts into correct batches."""
    from app.rag.embeddings import EmbeddingClient

    settings = EmbeddingSettings(openai_api_key="test")
    client = EmbeddingClient(settings)
    texts = [f"text {i}" for i in range(150)]
    batches = client._split_batches(texts)
    # 150 texts / 64 batch size = 3 batches
    assert len(batches) == 3
    assert batches[0][0] == 0    # offset
    assert batches[1][0] == 64
    assert batches[2][0] == 128
    assert len(batches[0][1]) == 64
    assert len(batches[2][1]) == 22  # 150 - 128
