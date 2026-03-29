"""Tests for analytical model registry."""
from __future__ import annotations

import pytest

from app.analytics.registry import (
    ModelCategory,
    ModelKind,
    ModelMetadata,
    ModelRegistry,
)


def _make_meta(name: str = "test_model", version: str = "1.0.0") -> ModelMetadata:
    return ModelMetadata(
        name=name,
        version=version,
        owner="test",
        category=ModelCategory.TAX,
        kind=ModelKind.DETERMINISTIC,
        description="Test model",
        use_case="Testing",
        input_freshness_seconds=86400,
        known_limitations=("test only",),
    )


class _FakeModel:
    def __init__(self, name: str = "test_model", version: str = "1.0.0"):
        self.metadata = _make_meta(name, version)

    def score(self, inputs):
        return {"result": "ok"}


def test_register_and_get() -> None:
    reg = ModelRegistry()
    model = _FakeModel()
    reg.register(model)
    assert reg.get("test_model") is model


def test_get_specific_version() -> None:
    reg = ModelRegistry()
    v1 = _FakeModel("m", "1.0.0")
    v2 = _FakeModel("m", "2.0.0")
    reg.register(v1)
    reg.register(v2)
    assert reg.get("m", "1.0.0") is v1
    assert reg.get("m") is v2  # latest


def test_duplicate_raises() -> None:
    reg = ModelRegistry()
    reg.register(_FakeModel())
    with pytest.raises(ValueError):
        reg.register(_FakeModel())


def test_invoke_adds_metadata() -> None:
    reg = ModelRegistry()
    reg.register(_FakeModel())
    result = reg.invoke("test_model", {})
    assert result["_model"] == "test_model"
    assert result["_version"] == "1.0.0"
    assert "_scored_at" in result


def test_list_models() -> None:
    reg = ModelRegistry()
    reg.register(_FakeModel("a", "1.0.0"))
    reg.register(_FakeModel("b", "1.0.0"))
    models = reg.list_models()
    names = {m.name for m in models}
    assert names == {"a", "b"}
