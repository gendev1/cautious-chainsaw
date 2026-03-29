"""Tests for cache key utility."""
from __future__ import annotations

from app.utils.cache import cache_key


def test_cache_key_format() -> None:
    """T9: cache_key builds colon-separated key."""
    result = cache_key("chat", "t1", "a1", "conv")
    assert result == "chat:t1:a1:conv"


def test_cache_key_without_extra_parts() -> None:
    """cache_key works with only namespace, tenant, actor."""
    result = cache_key("style_profile", "t_1", "a_1")
    assert result == "style_profile:t_1:a_1"


def test_cache_key_with_date() -> None:
    """cache_key includes date part."""
    result = cache_key("digest", "t_1", "a_1", "2026-03-26")
    assert result == "digest:t_1:a_1:2026-03-26"
