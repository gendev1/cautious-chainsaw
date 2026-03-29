"""
app/middleware/tenant.py — Extract tenant context from platform-set headers.
"""
from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.context import RequestContext
from app.models.access_scope import AccessScope

logger = logging.getLogger("sidecar.middleware.tenant")

# Health endpoints bypass tenant context extraction.
_SKIP_PATHS: set[str] = {"/health", "/ready", "/docs", "/openapi.json"}


class TenantContextMiddleware(BaseHTTPMiddleware):
    """
    Reads identity and access-scope headers injected by the platform API.

    Required headers (except on skip paths):
        X-Tenant-ID     — firm identifier
        X-Actor-ID      — user identifier
        X-Actor-Type    — "advisor" | "admin" | "system"

    Optional headers:
        X-Conversation-ID   — multi-turn session identifier
        X-Access-Scope      — JSON-encoded AccessScope object
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.url.path in _SKIP_PATHS:
            return await call_next(request)

        tenant_id = request.headers.get("x-tenant-id")
        actor_id = request.headers.get("x-actor-id")
        actor_type = request.headers.get("x-actor-type")

        if not tenant_id or not actor_id or not actor_type:
            logger.warning(
                "missing required tenant headers",
                extra={
                    "path": request.url.path,
                    "has_tenant": bool(tenant_id),
                    "has_actor": bool(actor_id),
                },
            )
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": {
                        "code": "MISSING_CONTEXT",
                        "category": "validation",
                        "message": (
                            "X-Tenant-ID, X-Actor-ID, and X-Actor-Type"
                            " headers are required."
                        ),
                        "detail": None,
                        "request_id": None,
                    },
                },
            )

        # Parse optional access scope
        access_scope: AccessScope | None = None
        raw_scope = request.headers.get("x-access-scope")
        if raw_scope:
            try:
                access_scope = AccessScope.model_validate_json(raw_scope)
            except Exception:
                logger.warning(
                    "invalid X-Access-Scope header, ignoring",
                    extra={"raw": raw_scope[:200]},
                )

        ctx = RequestContext(
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_type=actor_type,
            request_id=getattr(request.state, "request_id", "unknown"),
            conversation_id=request.headers.get("x-conversation-id"),
            access_scope=access_scope,
        )

        request.state.context = ctx

        logger.debug(
            "tenant context attached",
            extra={"tenant_id": ctx.tenant_id, "actor_id": ctx.actor_id},
        )

        return await call_next(request)
