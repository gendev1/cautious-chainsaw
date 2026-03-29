"""app/observability/langfuse_client.py — Langfuse singleton."""
from __future__ import annotations

from langfuse import Langfuse

from app.config import Settings

_langfuse: Langfuse | None = None


def get_langfuse_client(settings: Settings) -> Langfuse:
    global _langfuse  # noqa: PLW0603
    if _langfuse is None:
        _langfuse = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host or settings.langfuse_base_url,
            enabled=settings.langfuse_enabled,
        )
    return _langfuse


def shutdown_langfuse() -> None:
    global _langfuse  # noqa: PLW0603
    if _langfuse is not None:
        _langfuse.flush()
        _langfuse.shutdown()
        _langfuse = None
