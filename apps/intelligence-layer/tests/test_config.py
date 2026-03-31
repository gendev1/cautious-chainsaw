"""Tests for configuration settings."""
from __future__ import annotations

from app.config import Settings, get_settings


def test_settings_loads_with_defaults(monkeypatch) -> None:
    """T11: Settings loads with defaults when no env vars are set."""
    # Clear env vars so we test true defaults, not .env file values
    for key in (
        "SIDECAR_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY",
        "SIDECAR_OPENAI_API_KEY", "OPENAI_API_KEY",
        "SIDECAR_REDIS_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    assert settings.environment == "development"
    assert settings.debug is False
    assert settings.log_level == "INFO"
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.anthropic_api_key == ""
    assert settings.copilot_model == "anthropic:claude-sonnet-4-6"


def test_settings_singleton() -> None:
    """get_settings returns the same cached instance."""
    get_settings.cache_clear()
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


def test_cors_origins_parsed_from_comma_string() -> None:
    """cors_allowed_origins parses comma-separated strings."""
    settings = Settings(cors_allowed_origins="http://a.com, http://b.com")
    assert settings.cors_allowed_origins == ["http://a.com", "http://b.com"]


def test_cors_origins_accepts_list() -> None:
    """cors_allowed_origins accepts a list directly."""
    settings = Settings(cors_allowed_origins=["http://a.com"])
    assert settings.cors_allowed_origins == ["http://a.com"]
