"""
app/jobs/retry.py — Retry-aware job wrapper with dead-letter.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Coroutine
from datetime import timedelta
from functools import wraps
from typing import Any

from arq import Retry

from app.jobs.errors import (
    FailureCategory,
    classify_error,
    compute_retry_delay,
)

logger = logging.getLogger("sidecar.jobs.retry")


def with_retry_policy(
    fn: Callable[..., Coroutine[Any, Any, dict]],
) -> Callable[..., Coroutine[Any, Any, dict]]:
    """Decorator that wraps a job function with
    classification-based retry.
    """

    @wraps(fn)
    async def wrapper(
        ctx: dict[str, Any], *args: Any, **kwargs: Any
    ) -> dict:
        attempt = ctx.get("job_try", 1) - 1
        job_id = ctx.get("job_id", "unknown")

        try:
            return await fn(ctx, *args, **kwargs)
        except Retry:
            raise
        except Exception as exc:
            category = classify_error(exc)
            delay = compute_retry_delay(
                category, attempt
            )

            logger.warning(
                "Job %s failed (attempt %d, "
                "category=%s): %s",
                job_id,
                attempt + 1,
                category.value,
                exc,
            )

            if delay is not None:
                logger.info(
                    "Retrying job %s in %.0fs "
                    "(attempt %d)",
                    job_id,
                    delay,
                    attempt + 2,
                )
                raise Retry(
                    defer=timedelta(seconds=delay)
                ) from exc

            await _dead_letter(
                ctx,
                job_id,
                fn.__name__,
                args,
                category,
                exc,
                attempt,
            )
            raise

    return wrapper


async def _dead_letter(
    ctx: dict[str, Any],
    job_id: str,
    job_name: str,
    args: tuple,
    category: FailureCategory,
    exc: Exception,
    attempts: int,
) -> None:
    """Record a permanently failed job in the
    dead-letter sorted set.
    """
    redis = ctx.get("redis")
    if redis is None:
        return

    entry = {
        "job_id": job_id,
        "job_name": job_name,
        "category": category.value,
        "error": str(exc),
        "error_type": type(exc).__name__,
        "attempts": attempts + 1,
        "failed_at": time.time(),
    }

    try:
        await redis.zadd(
            "sidecar:dead_letter",
            {json.dumps(entry): time.time()},
        )
        await redis.zremrangebyrank(
            "sidecar:dead_letter", 0, -1001
        )
        logger.error(
            "Job %s (%s) moved to dead letter "
            "after %d attempts: %s",
            job_id,
            job_name,
            attempts + 1,
            exc,
        )
    except Exception as dl_exc:
        logger.error(
            "Failed to record dead letter for %s: %s",
            job_id,
            dl_exc,
        )
