"""app/middleware/metrics.py — Prometheus HTTP metrics."""
from __future__ import annotations

import time

from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)
from starlette.requests import Request
from starlette.responses import Response

from app.observability.metrics import REQUEST_COUNT, REQUEST_LATENCY


class PrometheusMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        duration = time.monotonic() - start

        labels = {
            "method": request.method,
            "endpoint": request.url.path,
            "status_code": str(response.status_code),
        }
        REQUEST_LATENCY.labels(**labels).observe(duration)
        REQUEST_COUNT.labels(**labels).inc()
        return response
