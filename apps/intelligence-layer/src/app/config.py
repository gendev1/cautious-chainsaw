"""
app/config.py — Pydantic Settings for all sidecar configuration.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    All sidecar configuration surfaces as env vars.
    Prefixed with SIDECAR_ to avoid collisions.
    """

    model_config = SettingsConfigDict(
        env_prefix="SIDECAR_",
        env_file=".env",
        env_file_encoding="utf-8",
        frozen=True,
    )

    # ── General ────────────────────────────────────────────────────────
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"
    cors_allowed_origins: list[str] = ["*"]

    # ── Platform API ───────────────────────────────────────────────────
    platform_api_url: str = Field(
        default="http://localhost:3000",
        description="Base URL of the platform API (NestJS backend).",
    )
    platform_api_key: str = Field(
        default="",
        description="Shared secret or service token for sidecar → platform reads.",
    )
    platform_api_timeout_s: float = Field(default=30.0)

    # ── LLM Providers ─────────────────────────────────────────────────
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    together_api_key: str = ""
    groq_api_key: str = ""

    # Models per tier
    copilot_model: str = "anthropic:claude-sonnet-4-6"
    copilot_fallback_model: str = "openai:gpt-4o"
    batch_model: str = "anthropic:claude-haiku-4-5"
    batch_fallback_model: str = "together:meta-llama/Llama-3.3-70B"
    analysis_model: str = "anthropic:claude-opus-4-6"
    extraction_model: str = "anthropic:claude-haiku-4-5"
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    # ── Redis ──────────────────────────────────────────────────────────
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL. Used for cache, conversation memory, and ARQ queue.",
    )
    redis_max_connections: int = 20

    # ── ARQ Worker ─────────────────────────────────────────────────────
    arq_queue_name: str = "sidecar:queue"
    arq_max_jobs: int = 10
    arq_job_timeout_s: int = 600
    arq_retry_count: int = 3

    # ── Vector Store ───────────────────────────────────────────────────
    vector_store_provider: Literal["pgvector", "qdrant"] = "pgvector"
    vector_store_url: str = Field(
        default="postgresql+asyncpg://localhost:5432/sidecar_vectors",
        description="Connection string for the vector store.",
    )
    vector_store_collection: str = "documents"
    vector_search_top_k: int = 20
    vector_rerank_top_k: int = 8

    # ── Transcription ──────────────────────────────────────────────────
    transcription_provider: Literal["whisper", "deepgram"] = "whisper"
    deepgram_api_key: str = ""
    whisper_model: str = "whisper-1"
    max_audio_duration_s: int = 7200  # 2 hours

    # ── Langfuse Observability ─────────────────────────────────────────
    langfuse_enabled: bool = True
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_base_url: str = ""

    # ── Conversation Memory ────────────────────────────────────────────
    conversation_ttl_s: int = 7200  # 2 hours
    conversation_max_messages: int = 50

    # ── Token Budget ───────────────────────────────────────────────────
    token_budget_redis_prefix: str = "sidecar:token_budget"
    default_daily_token_limit: int = 2_000_000

    # ── Cache TTLs ─────────────────────────────────────────────────────
    style_profile_ttl_s: int = 604800  # 7 days
    digest_cache_ttl_s: int = 86400  # 1 day

    # ── Portfolio Construction ─────────────────────────────────────────
    portfolio_freshness_warn_s: int = 86400  # 1 day
    portfolio_theme_cache_ttl_s: int = 21600  # 6 hours

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton settings instance."""
    return Settings()
