"""app/observability/tool_audit.py — Tool call audit."""
from __future__ import annotations

import functools
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("sidecar.tool_audit")

MAX_RESULT_SUMMARY_LEN = 500


def _summarize(result: Any) -> str:
    text = str(result)
    if len(text) > MAX_RESULT_SUMMARY_LEN:
        return text[:MAX_RESULT_SUMMARY_LEN] + "...[truncated]"
    return text


def audited_tool(
    fn: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        tool_name = fn.__name__
        start = time.monotonic()
        error: str | None = None
        result = None
        try:
            result = await fn(*args, **kwargs)
            return result
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            latency_ms = (time.monotonic() - start) * 1000
            if error:
                logger.warning(
                    "tool_call_failed tool=%s latency_ms=%.2f"
                    " error=%s",
                    tool_name,
                    latency_ms,
                    error,
                )
            else:
                logger.info(
                    "tool_call_complete tool=%s latency_ms=%.2f"
                    " result=%s",
                    tool_name,
                    latency_ms,
                    _summarize(result),
                )

    return wrapper
