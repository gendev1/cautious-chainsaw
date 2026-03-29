"""
app/utils/cache.py — Scoped Redis cache key builder.
"""
from __future__ import annotations


def cache_key(namespace: str, tenant_id: str, actor_id: str, *parts: str) -> str:
    """
    Build a scoped Redis cache key.

    Examples:
        cache_key("chat", "t_1", "a_1", "conv_xyz")
            → "chat:t_1:a_1:conv_xyz"
        cache_key("digest", "t_1", "a_1", "2026-03-26")
            → "digest:t_1:a_1:2026-03-26"
        cache_key("style_profile", "t_1", "a_1")
            → "style_profile:t_1:a_1"
    """
    segments = [namespace, tenant_id, actor_id, *parts]
    return ":".join(segments)
