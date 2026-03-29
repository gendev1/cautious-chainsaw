"""
app/middleware/logging.py — Structured request/response logging.
"""
from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("sidecar.http")


class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    """
    Logs every request with tenant, actor, request_id, method, path,
    status code, and duration. Uses structlog-compatible extra fields.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        start = time.perf_counter()

        response = await call_next(request)

        duration_ms = (time.perf_counter() - start) * 1000

        ctx = getattr(request.state, "context", None)
        logger.info(
            "http request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round(duration_ms, 2),
                "request_id": getattr(request.state, "request_id", None),
                "tenant_id": ctx.tenant_id if ctx else None,
                "actor_id": ctx.actor_id if ctx else None,
            },
        )

        return response
