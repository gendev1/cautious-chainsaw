"""
app/agents/deps.py — Shared dependency container for Pydantic AI agents.
"""
from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from app.context import RequestContext
from app.rag.retriever import Retriever
from app.services.platform_client import PlatformClient


@dataclass(frozen=True, slots=True)
class AgentDeps:
    """
    Immutable dependency bundle passed to every Pydantic AI agent run.

    Contains the request context (tenant, actor, scope) and the shared
    clients needed by agent tool functions.
    """

    context: RequestContext
    platform: PlatformClient
    redis: Redis
    retriever: Retriever | None = None

    @property
    def tenant_id(self) -> str:
        return self.context.tenant_id

    @property
    def access_scope(self):
        return self.context.access_scope
