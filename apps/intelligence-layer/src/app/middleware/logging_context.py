"""app/middleware/logging_context.py — structlog context binding."""
from __future__ import annotations

import structlog
from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)
from starlette.requests import Request
from starlette.responses import Response


class LoggingContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        ctx = getattr(request.state, "context", None)
        if ctx is None:
            return await call_next(request)

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            tenant_id=ctx.tenant_id,
            actor_id=ctx.actor_id,
            request_id=ctx.request_id,
            conversation_id=(
                getattr(ctx, "conversation_id", None) or "none"
            ),
        )
        return await call_next(request)
