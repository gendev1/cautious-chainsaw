"""
app/jobs/portfolio_construction.py -- Portfolio construction background job.

Runs the full PortfolioConstructionPipeline, stores the result
in Redis, and emits completion events.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.jobs.enqueue import JobContext
from app.models.access_scope import AccessScope
from app.portfolio_construction.events import ProgressEventEmitter
from app.portfolio_construction.models import ConstructPortfolioRequest
from app.portfolio_construction.orchestrator import PortfolioConstructionPipeline

logger = logging.getLogger("sidecar.jobs.portfolio_construction")

RESULT_TTL_S = 86_400  # 24 hours


async def run_portfolio_construction(
    ctx: dict[str, Any],
    job_ctx_raw: dict | None = None,
    request_raw: dict | None = None,
) -> dict:
    """
    Execute the portfolio construction pipeline.

    Called by the ARQ worker.  Receives serialised JobContext and
    ConstructPortfolioRequest dicts, hydrates them, runs the pipeline,
    stores the result in Redis, and returns a summary dict.
    """
    if job_ctx_raw is None:
        raise ValueError("run_portfolio_construction requires job_ctx_raw")
    if request_raw is None:
        raise ValueError("run_portfolio_construction requires request_raw")

    job_ctx = JobContext(**job_ctx_raw)
    access_scope = AccessScope(**job_ctx.access_scope) if job_ctx.access_scope else None
    request = ConstructPortfolioRequest(**request_raw)

    platform = ctx["platform_client"]
    redis = ctx["redis"]
    settings = ctx.get("settings")

    job_id = ctx.get("job_id", f"portfolio:{job_ctx.request_id}")

    # Emit enqueued event
    emitter = ProgressEventEmitter(redis)
    await emitter.emit(job_id, "job_enqueued", {"message": request.message})

    try:
        pipeline = PortfolioConstructionPipeline(
            platform=platform,
            redis=redis,
            access_scope=access_scope,
            settings=settings,
        )

        result = await pipeline.run(request=request, job_id=job_id)

        # Store result in Redis
        result_key = f"sidecar:portfolio:result:{job_id}"
        await redis.set(result_key, result.model_dump_json(), ex=RESULT_TTL_S)

        logger.info(
            "portfolio construction completed job=%s holdings=%d",
            job_id,
            len(result.proposed_holdings),
        )

        return {
            "status": "completed",
            "job_id": job_id,
            "holdings_count": len(result.proposed_holdings),
        }

    except Exception as exc:
        await emitter.emit(job_id, "job_failed", {"error": str(exc)})
        logger.exception("portfolio construction failed job=%s", job_id)
        raise
