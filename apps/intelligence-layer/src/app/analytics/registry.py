"""
Central model registry for all analytical scoring and scenario models.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


# -------------------------------------------------------------------
# Model classification
# -------------------------------------------------------------------


class ModelKind(str, Enum):
    DETERMINISTIC = "deterministic"
    HEURISTIC = "heuristic"
    LEARNED = "learned"


class ModelCategory(str, Enum):
    TAX = "tax"
    PORTFOLIO = "portfolio"
    COMPLIANCE = "compliance"
    PERSONALIZATION = "personalization"
    FIRM_ANALYTICS = "firm_analytics"


# -------------------------------------------------------------------
# Governance metadata — every model must declare this
# -------------------------------------------------------------------


@dataclass(frozen=True)
class ModelMetadata:
    """Governance declaration attached to every registered model."""

    name: str
    version: str
    owner: str  # team or individual
    category: ModelCategory
    kind: ModelKind
    description: str
    use_case: str  # intended decision-support use case
    input_freshness_seconds: int  # max age of inputs before stale
    known_limitations: tuple[str, ...]
    reviewable: bool = True  # can an advisor inspect outputs?
    registered_at: datetime = field(
        default_factory=lambda: datetime.now(UTC)
    )


# -------------------------------------------------------------------
# Model protocol — the contract every model must satisfy
# -------------------------------------------------------------------


class AnalyticalModel(Protocol):
    """Structural protocol that every analytical model must implement."""

    metadata: ModelMetadata

    def score(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Run the model and return a structured result dict."""
        ...


# -------------------------------------------------------------------
# Registry
# -------------------------------------------------------------------


class ModelRegistry:
    """
    Singleton-style registry.  Populated at startup via ``register()``;
    queried at request time via ``get()`` or ``invoke()``.
    """

    def __init__(self) -> None:
        # key = (name, version), value = model instance
        self._models: dict[
            tuple[str, str], AnalyticalModel
        ] = {}
        # key = name, value = latest version string
        self._latest: dict[str, str] = {}

    # -- registration --------------------------------------------------

    def register(self, model: AnalyticalModel) -> None:
        meta = model.metadata
        key = (meta.name, meta.version)
        if key in self._models:
            raise ValueError(f"Model {key} already registered")
        self._models[key] = model
        # Track latest by lexicographic version (semver-friendly)
        if (
            meta.name not in self._latest
            or meta.version > self._latest[meta.name]
        ):
            self._latest[meta.name] = meta.version
        logger.info(
            "Registered model %s v%s (%s)",
            meta.name,
            meta.version,
            meta.kind.value,
        )

    # -- lookup --------------------------------------------------------

    def get(
        self, name: str, version: str | None = None
    ) -> AnalyticalModel:
        """Resolve a model by name and optional version."""
        ver = version or self._latest.get(name)
        if ver is None:
            raise KeyError(
                f"No model registered with name '{name}'"
            )
        key = (name, ver)
        if key not in self._models:
            raise KeyError(
                f"Model '{name}' version '{ver}' not found"
            )
        return self._models[key]

    # -- invocation ----------------------------------------------------

    def invoke(
        self,
        name: str,
        inputs: dict[str, Any],
        *,
        version: str | None = None,
    ) -> dict[str, Any]:
        """Resolve and score in one call."""
        model = self.get(name, version)
        result = model.score(inputs)
        result["_model"] = model.metadata.name
        result["_version"] = model.metadata.version
        result["_scored_at"] = (
            datetime.now(UTC).isoformat()
        )
        return result

    # -- introspection -------------------------------------------------

    def list_models(self) -> list[ModelMetadata]:
        """Return metadata for every registered model."""
        return [
            self._models[(name, ver)].metadata
            for name, ver in self._latest.items()
        ]

    def list_all_versions(self, name: str) -> list[str]:
        return sorted(
            ver for (n, ver) in self._models if n == name
        )


# -------------------------------------------------------------------
# Module-level singleton
# -------------------------------------------------------------------

_registry = ModelRegistry()


def get_registry() -> ModelRegistry:
    return _registry
