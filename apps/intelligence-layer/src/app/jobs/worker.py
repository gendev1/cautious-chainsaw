"""
app/jobs/worker.py — ARQ worker entry point.

Registers all job functions, configures Redis connection,
sets concurrency limits, and attaches cron schedules.
"""
from __future__ import annotations

import logging
from typing import Any

from arq import cron, func
from arq.connections import RedisSettings

from app.config import get_settings
from app.jobs.daily_digest import run_daily_digest
from app.jobs.email_triage import run_email_triage
from app.jobs.firm_report import run_firm_report
from app.jobs.meeting_summary import run_meeting_summary
from app.jobs.rag_index import run_rag_index_update
from app.jobs.retry import with_retry_policy
from app.jobs.style_profile import run_style_profile_refresh
from app.jobs.transcription import run_transcription

logger = logging.getLogger("sidecar.worker")


async def startup(ctx: dict[str, Any]) -> None:
    """Called once when the worker process starts."""
    from app.dependencies import build_worker_dependencies

    deps = await build_worker_dependencies()
    ctx["platform_client"] = deps["platform_client"]
    ctx["redis"] = deps["redis"]
    ctx["langfuse"] = deps["langfuse"]
    ctx["http_client"] = deps["http_client"]
    ctx["settings"] = deps["settings"]
    logger.info("Worker started, dependencies initialized")


async def shutdown(ctx: dict[str, Any]) -> None:
    """Called once when the worker process shuts down."""
    if http_client := ctx.get("http_client"):
        await http_client.aclose()
    if platform_client := ctx.get("platform_client"):
        await platform_client.close()
    logger.info("Worker shut down cleanly")


def get_redis_settings() -> RedisSettings:
    settings = get_settings()
    return RedisSettings.from_dsn(settings.redis_url)


class WorkerSettings:
    """ARQ worker configuration. ARQ discovers this class
    by name.
    """

    functions = [
        func(
            with_retry_policy(run_daily_digest),
            name="run_daily_digest",
        ),
        func(
            with_retry_policy(run_email_triage),
            name="run_email_triage",
        ),
        func(
            with_retry_policy(run_transcription),
            name="run_transcription",
        ),
        func(
            with_retry_policy(run_meeting_summary),
            name="run_meeting_summary",
        ),
        func(
            with_retry_policy(run_firm_report),
            name="run_firm_report",
        ),
        func(
            with_retry_policy(run_style_profile_refresh),
            name="run_style_profile_refresh",
        ),
        func(
            with_retry_policy(run_rag_index_update),
            name="run_rag_index_update",
        ),
    ]

    cron_jobs = [
        cron(
            with_retry_policy(run_daily_digest),
            name="daily-digest-sweep",
            hour=5,
            minute=55,
            unique=True,
            timeout=600,
        ),
        cron(
            with_retry_policy(run_email_triage),
            name="email-triage-sweep",
            minute={0, 15, 30, 45},
            unique=True,
            timeout=300,
        ),
        cron(
            with_retry_policy(run_style_profile_refresh),
            name="style-profile-weekly",
            weekday=6,
            hour=2,
            minute=0,
            unique=True,
            timeout=900,
        ),
    ]

    on_startup = startup
    on_shutdown = shutdown
    redis_settings = get_redis_settings()
    max_jobs = 10
    job_timeout = 600
    poll_delay = 0.5
    keep_result = 86400
    retry_jobs = True
    max_tries = 3
    health_check_interval = 30
    health_check_key = "sidecar:worker:health"
