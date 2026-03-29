"""app/middleware/tracing.py — Langfuse per-request trace."""
from __future__ import annotations

import time

from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)
from starlette.requests import Request
from starlette.responses import Response

from app.config import get_settings
from app.observability.langfuse_client import get_langfuse_client


class LangfuseTraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        ctx = getattr(request.state, "context", None)
        if ctx is None:
            return await call_next(request)

        langfuse = get_langfuse_client(get_settings())
        span_ctx = (
            langfuse._start_as_current_otel_span_with_processed_media(
                name=f"{request.method} {request.url.path}",
                metadata={
                    "tenant_id": ctx.tenant_id,
                    "actor_id": ctx.actor_id,
                    "request_id": ctx.request_id,
                },
            )
        )
        span = span_ctx.__enter__()
        request.state.langfuse_trace = span
        request.state.trace_start = time.monotonic()

        response = await call_next(request)

        span_ctx.__exit__(None, None, None)
        return response
