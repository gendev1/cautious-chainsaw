"""
app/jobs/daily_digest.py — Daily digest generation job.

Cron sweep fans out per-advisor; per-advisor mode generates a
personalised DailyDigest via pydantic-ai Agent and caches in Redis.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from pydantic_ai import Agent

from app.jobs.enqueue import JobContext
from app.jobs.observability import JobTracer
from app.jobs.retry import with_retry_policy
from app.models.access_scope import AccessScope
from app.models.schemas import (
    DailyDigest,
)

logger = logging.getLogger("sidecar.jobs.daily_digest")

# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

digest_agent: Agent[None, DailyDigest] = Agent(
    model="anthropic:claude-haiku-4-5",
    output_type=DailyDigest,
    defer_model_check=True,
    system_prompt=(
        "You are a daily briefing generator for wealth advisors. "
        "Given the advisor's calendar, emails, tasks, alerts, and CRM activity, "
        "produce a structured DailyDigest with clear sections, priority items, "
        "and suggested actions. Keep summaries concise and actionable. "
        "Urgency levels are: high, medium, low."
    ),
    retries=2,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DIGEST_CACHE_TTL_S = 86_400  # 24 hours


async def _safe_fetch(
    coro: Any,
    label: str,
    tracer: JobTracer | None = None,
) -> Any:
    """Execute a coroutine, returning None on failure instead of raising."""
    try:
        result = await coro
        if tracer:
            tracer.record_platform_read()
        return result
    except Exception as exc:
        logger.warning("safe_fetch %s failed: %s", label, exc)
        return None


async def _gather_advisor_data(
    platform: Any,
    advisor_id: str,
    access_scope: AccessScope,
    tracer: JobTracer | None = None,
) -> dict[str, Any]:
    """Concurrently fetch all data needed for an advisor's digest."""
    calendar_task = _safe_fetch(
        platform.get_advisor_calendar(advisor_id, access_scope),
        "calendar",
        tracer,
    )
    tasks_task = _safe_fetch(
        platform.get_advisor_tasks(advisor_id, access_scope),
        "tasks",
        tracer,
    )
    emails_task = _safe_fetch(
        platform.get_advisor_priority_emails(advisor_id, access_scope),
        "priority_emails",
        tracer,
    )
    alerts_task = _safe_fetch(
        platform.get_account_alerts(advisor_id, access_scope),
        "account_alerts",
        tracer,
    )
    crm_task = _safe_fetch(
        platform.get_crm_activity_feed(advisor_id, access_scope),
        "crm_activity",
        tracer,
    )
    clients_task = _safe_fetch(
        platform.get_advisor_clients(advisor_id, access_scope),
        "clients",
        tracer,
    )

    calendar, tasks, emails, alerts, crm_activity, clients = await asyncio.gather(
        calendar_task, tasks_task, emails_task, alerts_task, crm_task, clients_task,
    )

    return {
        "calendar": calendar,
        "tasks": tasks,
        "priority_emails": emails,
        "account_alerts": alerts,
        "crm_activity": crm_activity,
        "clients": clients,
    }


def _build_prompt(advisor_id: str, data: dict[str, Any]) -> str:
    """Build the user prompt from fetched data."""
    sections: list[str] = [
        f"Generate a daily digest for advisor {advisor_id}.",
        f"Date: {datetime.now(UTC).strftime('%Y-%m-%d')}",
        "",
    ]

    if data.get("calendar"):
        sections.append("## Today's Calendar")
        for event in data["calendar"]:
            obj = event if isinstance(event, dict) else event.model_dump()
            sections.append(
                f"- {obj.get('subject', 'Meeting')} at {obj.get('start', 'TBD')} "
                f"({', '.join(obj.get('attendees', []))})"
            )
        sections.append("")

    if data.get("priority_emails"):
        sections.append("## Priority Emails")
        for email in data["priority_emails"]:
            obj = email if isinstance(email, dict) else email.model_dump()
            sections.append(
                f"- [{obj.get('priority', 'normal')}] From {obj.get('from_address', 'unknown')}: "
                f"{obj.get('subject', '(no subject)')} — {obj.get('body_preview', '')[:120]}"
            )
        sections.append("")

    if data.get("tasks"):
        sections.append("## Pending Tasks")
        for task in data["tasks"]:
            obj = task if isinstance(task, dict) else task.model_dump()
            sections.append(
                f"- [{obj.get('priority', 'normal')}] {obj.get('title', 'Task')} "
                f"(due: {obj.get('due_date', 'none')})"
            )
        sections.append("")

    if data.get("account_alerts"):
        sections.append("## Account Alerts")
        for alert in data["account_alerts"]:
            obj = alert if isinstance(alert, dict) else alert.model_dump()
            sections.append(
                f"- [{obj.get('severity', 'info')}] {obj.get('title', 'Alert')}: "
                f"{obj.get('description', '')[:120]}"
            )
        sections.append("")

    if data.get("crm_activity"):
        sections.append("## Recent CRM Activity")
        for act in (data["crm_activity"] or [])[:10]:
            obj = act if isinstance(act, dict) else act.model_dump()
            sections.append(
                f"- {obj.get('activity_type', 'activity')}: {obj.get('subject', '')}"
            )
        sections.append("")

    if data.get("clients"):
        sections.append("## Client Book Summary")
        sections.append(f"Total clients: {len(data['clients'])}")
        sections.append("")

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Job entry point
# ---------------------------------------------------------------------------

@with_retry_policy
async def run_daily_digest(
    ctx: dict[str, Any],
    job_ctx_raw: dict | None = None,
    advisor_id: str | None = None,
) -> dict:
    """
    Generate daily digest.

    - Cron sweep mode (job_ctx_raw is None): fan out per advisor.
    - Per-advisor mode: generate and cache digest for one advisor.
    """
    platform = ctx["platform_client"]
    redis = ctx["redis"]
    langfuse = ctx.get("langfuse")

    # ── Cron sweep mode ───────────────────────────────────────────────
    if job_ctx_raw is None:
        logger.info("daily_digest: starting cron sweep")
        tenants = await platform.list_active_tenants()
        enqueued = 0
        for tenant in tenants:
            tenant_id = (
                tenant if isinstance(tenant, str)
                else tenant.get("tenant_id", tenant.get("id"))
            )
            scope = AccessScope(tenant_id=tenant_id, visibility_mode="full_tenant")
            advisors = await platform.list_advisors(tenant_id, scope)
            for adv in advisors:
                adv_id = adv if isinstance(adv, str) else adv.get("advisor_id", adv.get("id"))
                job_ctx = JobContext(
                    tenant_id=tenant_id,
                    actor_id=adv_id,
                    actor_type="service",
                    request_id=f"digest-cron-{tenant_id}-{adv_id}",
                    access_scope=scope.model_dump(),
                )
                arq_redis = ctx.get("arq_redis") or redis
                await arq_redis.enqueue_job(
                    "daily_digest",
                    job_ctx.model_dump(),
                    adv_id,
                )
                enqueued += 1
        logger.info("daily_digest: cron sweep enqueued %d jobs", enqueued)
        return {"mode": "cron_sweep", "enqueued": enqueued}

    # ── Per-advisor mode ──────────────────────────────────────────────
    job_ctx = JobContext(**job_ctx_raw)
    effective_advisor_id = advisor_id or job_ctx.actor_id
    access_scope = AccessScope(**job_ctx.access_scope)

    tracer: JobTracer | None = None
    if langfuse:
        tracer = JobTracer(
            langfuse=langfuse,
            job_name="daily_digest",
            tenant_id=job_ctx.tenant_id,
            actor_id=effective_advisor_id,
        )

    try:
        # Check cache
        cache_key = f"sidecar:digest:{job_ctx.tenant_id}:{effective_advisor_id}"
        cached = await redis.get(cache_key)
        if cached:
            if tracer:
                tracer.record_cache_hit()
                tracer.complete(output={"cached": True})
            logger.info("daily_digest: cache hit for %s", effective_advisor_id)
            return {"status": "cached", "advisor_id": effective_advisor_id}

        if tracer:
            tracer.record_cache_miss()

        # Gather data concurrently
        data = await _gather_advisor_data(
            platform, effective_advisor_id, access_scope, tracer,
        )

        # Build prompt and run agent
        prompt = _build_prompt(effective_advisor_id, data)

        gen = None
        if tracer:
            gen = tracer.start_generation(
                name="digest_generation",
                model="anthropic:claude-haiku-4-5",
                input_data=prompt[:2000],
            )

        result = await digest_agent.run(prompt)
        digest = result.output

        if tracer and gen:
            tracer.end_generation(gen, output=digest.model_dump())

        # Cache result
        await redis.set(
            cache_key,
            digest.model_dump_json(),
            ex=DIGEST_CACHE_TTL_S,
        )

        if tracer:
            tracer.complete(output={"advisor_id": effective_advisor_id})

        logger.info(
            "daily_digest: generated for %s (%d sections, %d priority items)",
            effective_advisor_id,
            len(digest.sections),
            len(digest.priority_items),
        )
        return {
            "status": "generated",
            "advisor_id": effective_advisor_id,
            "sections": len(digest.sections),
            "priority_items": len(digest.priority_items),
        }

    except Exception as exc:
        if tracer:
            tracer.fail(exc, category="daily_digest_error")
        raise
