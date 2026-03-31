"""
app/jobs/email_triage.py — Email triage job.

Cron sweep fans out per-advisor; per-advisor mode classifies emails
via pydantic-ai Agent into TriagedEmail results with cursor-based sync.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from pydantic_ai import Agent

from app.jobs.enqueue import JobContext
from app.services.llm_client import get_model
from app.jobs.observability import JobTracer
from app.jobs.retry import with_retry_policy
from app.models.access_scope import AccessScope
from app.models.schemas import TriagedEmail

logger = logging.getLogger("sidecar.jobs.email_triage")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BATCH_SIZE = 50
CURSOR_TTL_S = 86_400 * 7  # 7 days

# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

triage_agent: Agent[None, list[TriagedEmail]] = Agent(
    model=get_model("batch"),
    output_type=list[TriagedEmail],
    defer_model_check=True,
    system_prompt=(
        "You are an email triage assistant for wealth advisors. "
        "Classify each email by urgency (high/medium/low), category "
        "(client_request, meeting_followup, compliance, marketing, internal, other), "
        "and suggest a brief action. Be concise. "
        "If an email is clearly from a client, try to identify the client_id from context."
    ),
    retries=2,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_email_batch(emails: list[Any]) -> str:
    """Format a batch of emails into a prompt for the triage agent."""
    lines: list[str] = [
        f"Triage the following {len(emails)} emails. "
        "For each, provide email_id, subject, sender, urgency, category, summary, "
        "suggested_action, and client_id (if identifiable).",
        "",
    ]
    for i, email in enumerate(emails, 1):
        obj = email if isinstance(email, dict) else email.model_dump()
        lines.append(f"### Email {i}")
        lines.append(f"- email_id: {obj.get('email_id', obj.get('id', f'email_{i}'))}")
        lines.append(f"- from: {obj.get('from_address', obj.get('sender', 'unknown'))}")
        lines.append(f"- subject: {obj.get('subject', '(no subject)')}")
        lines.append(f"- received: {obj.get('received_at', 'unknown')}")
        lines.append(f"- preview: {obj.get('body_preview', '')[:300]}")
        if obj.get("client_id"):
            lines.append(f"- known_client_id: {obj['client_id']}")
        lines.append("")
    return "\n".join(lines)


async def _get_sync_cursor(redis: Any, advisor_id: str, tenant_id: str) -> str | None:
    """Retrieve the last-synced cursor for this advisor."""
    key = f"sidecar:email_triage:cursor:{tenant_id}:{advisor_id}"
    return await redis.get(key)


async def _set_sync_cursor(
    redis: Any, advisor_id: str, tenant_id: str, cursor: str,
) -> None:
    """Store the sync cursor for next run."""
    key = f"sidecar:email_triage:cursor:{tenant_id}:{advisor_id}"
    await redis.set(key, cursor, ex=CURSOR_TTL_S)


# ---------------------------------------------------------------------------
# Job entry point
# ---------------------------------------------------------------------------


@with_retry_policy
async def run_email_triage(
    ctx: dict[str, Any],
    job_ctx_raw: dict | None = None,
    advisor_id: str | None = None,
) -> dict:
    """
    Triage and classify incoming emails.

    - Cron sweep mode (job_ctx_raw is None): fan out per advisor.
    - Per-advisor mode: fetch new emails since cursor, classify in batches.
    """
    platform = ctx["platform_client"]
    redis = ctx["redis"]
    langfuse = ctx.get("langfuse")

    # ── Cron sweep mode ───────────────────────────────────────────────
    if job_ctx_raw is None:
        logger.info("email_triage: starting cron sweep")
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
                    request_id=f"email-triage-cron-{tenant_id}-{adv_id}",
                    access_scope=scope.model_dump(),
                )
                arq_redis = ctx.get("arq_redis") or redis
                await arq_redis.enqueue_job(
                    "email_triage",
                    job_ctx.model_dump(),
                    adv_id,
                )
                enqueued += 1
        logger.info("email_triage: cron sweep enqueued %d jobs", enqueued)
        return {"mode": "cron_sweep", "enqueued": enqueued}

    # ── Per-advisor mode ──────────────────────────────────────────────
    job_ctx = JobContext(**job_ctx_raw)
    effective_advisor_id = advisor_id or job_ctx.actor_id
    access_scope = AccessScope(**job_ctx.access_scope)

    tracer: JobTracer | None = None
    if langfuse:
        tracer = JobTracer(
            langfuse=langfuse,
            job_name="email_triage",
            tenant_id=job_ctx.tenant_id,
            actor_id=effective_advisor_id,
        )

    try:
        # Get sync cursor
        cursor = await _get_sync_cursor(redis, effective_advisor_id, job_ctx.tenant_id)

        # Fetch emails from platform
        fetch_params: dict[str, Any] = {}
        if cursor:
            fetch_params["since_cursor"] = cursor

        emails = await platform.get_advisor_priority_emails(
            effective_advisor_id, access_scope,
        )
        if tracer:
            tracer.record_platform_read()

        if not emails:
            if tracer:
                tracer.complete(output={"triaged": 0})
            logger.info("email_triage: no new emails for %s", effective_advisor_id)
            return {"status": "no_new_emails", "advisor_id": effective_advisor_id, "triaged": 0}

        # Process in batches
        all_triaged: list[TriagedEmail] = []
        total_emails = len(emails)
        latest_cursor: str | None = None

        for batch_start in range(0, total_emails, BATCH_SIZE):
            batch = emails[batch_start : batch_start + BATCH_SIZE]
            prompt = _format_email_batch(batch)

            gen = None
            if tracer:
                gen = tracer.start_generation(
                    name=f"triage_batch_{batch_start}",
                    model="anthropic:claude-haiku-4-5",
                    input_data=f"batch of {len(batch)} emails",
                )

            result = await triage_agent.run(prompt)
            triaged_batch = result.output

            if tracer and gen:
                tracer.end_generation(
                    gen,
                    output=f"{len(triaged_batch)} emails triaged",
                )

            all_triaged.extend(triaged_batch)

            # Track cursor from last email in batch
            last_email = batch[-1]
            obj = last_email if isinstance(last_email, dict) else last_email.model_dump()
            latest_cursor = obj.get("email_id", obj.get("id"))

        # Store results in Redis
        results_key = f"sidecar:email_triage:results:{job_ctx.tenant_id}:{effective_advisor_id}"
        results_json = json.dumps([t.model_dump() for t in all_triaged])
        await redis.set(results_key, results_json, ex=CURSOR_TTL_S)

        # Update sync cursor
        if latest_cursor:
            await _set_sync_cursor(redis, effective_advisor_id, job_ctx.tenant_id, latest_cursor)

        if tracer:
            tracer.complete(output={
                "advisor_id": effective_advisor_id,
                "triaged": len(all_triaged),
            })

        # Count by urgency
        urgency_counts: dict[str, int] = {}
        for t in all_triaged:
            urgency_counts[t.urgency] = urgency_counts.get(t.urgency, 0) + 1

        logger.info(
            "email_triage: triaged %d emails for %s (urgency: %s)",
            len(all_triaged),
            effective_advisor_id,
            urgency_counts,
        )
        return {
            "status": "triaged",
            "advisor_id": effective_advisor_id,
            "triaged": len(all_triaged),
            "urgency_counts": urgency_counts,
        }

    except Exception as exc:
        if tracer:
            tracer.fail(exc, category="email_triage_error")
        raise
