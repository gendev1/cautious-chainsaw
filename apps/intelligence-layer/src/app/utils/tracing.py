"""
app/utils/tracing.py — Langfuse v4 tracing utilities for routers.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any

from langfuse import Langfuse

logger = logging.getLogger("sidecar.tracing")


@contextmanager
def traced_request(
    langfuse: Langfuse,
    feature: str,
    *,
    tenant_id: str,
    actor_id: str,
    request_id: str,
    extra_metadata: dict[str, Any] | None = None,
):
    """Context manager for tracing a router request
    with Langfuse v4 OTEL spans.
    """
    metadata = {
        "tenant_id": tenant_id,
        "actor_id": actor_id,
        "request_id": request_id,
        **(extra_metadata or {}),
    }
    start = time.monotonic()
    ctx = (
        langfuse._start_as_current_otel_span_with_processed_media(
            name=feature,
            metadata=metadata,
        )
    )
    span = ctx.__enter__()
    try:
        yield span
    except Exception:
        duration_ms = (time.monotonic() - start) * 1000
        logger.warning(
            "Traced request %s failed after %.0fms",
            feature,
            duration_ms,
        )
        ctx.__exit__(None, None, None)
        raise
    else:
        ctx.__exit__(None, None, None)
