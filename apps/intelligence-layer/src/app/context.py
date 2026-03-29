"""
app/context.py — Per-request context propagated through the sidecar.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.models.access_scope import AccessScope


@dataclass(frozen=True, slots=True)
class RequestContext:
    """
    Immutable context for a single sidecar request.

    Populated by TenantContextMiddleware from inbound HTTP headers.
    Every downstream component (agents, platform client, retrieval,
    cache, logging) reads from this object — never from raw headers.
    """

    tenant_id: str
    """Firm-level isolation boundary. Always required."""

    actor_id: str
    """The user (advisor, admin, system) making the request."""

    actor_type: Literal["advisor", "admin", "service"]
    """Role of the actor. Determines some default scope behaviour."""

    request_id: str
    """Trace-level correlation ID (UUID). Generated or forwarded."""

    conversation_id: str | None = None
    """Set for multi-turn chat sessions. None for single-shot endpoints."""

    access_scope: AccessScope | None = None
    """
    Structured visibility scope provided by the platform.
    Determines which households, clients, accounts, and documents
    the actor may access in this request.
    """
