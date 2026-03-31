"""
app/agents/base_deps.py — Base dependencies shared across all agents.

This is the simpler dependency shape that tools type against via
RunContext[AgentDeps]. It holds only what tools need: a read-only
platform client and scope information.

The richer deps.py (from core infrastructure) bridges FastAPI DI
to agents and includes Redis, Retriever, and full RequestContext.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.access_scope import AccessScope
from app.services.platform_client import PlatformClient


@dataclass
class AgentDeps:
    """Base dependencies shared across all agents.

    Tools receive this via RunContext[AgentDeps] and can only
    access read-only platform methods and scope information.
    """

    platform: PlatformClient
    access_scope: AccessScope
    tenant_id: str
    actor_id: str
    redis: Any = None  # Optional Redis client for tools that need direct cache access
