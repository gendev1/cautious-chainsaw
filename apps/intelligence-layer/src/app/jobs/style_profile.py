"""
app/jobs/style_profile.py — Advisor writing style profile refresh.

Fetches an advisor's recent sent emails, extracts a StyleProfile via
pydantic-ai Agent, and caches the result in Redis with a 14-day TTL.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from app.jobs.enqueue import JobContext
from app.jobs.observability import JobTracer
from app.jobs.retry import with_retry_policy
from app.models.access_scope import AccessScope

logger = logging.getLogger("sidecar.jobs.style_profile")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STYLE_PROFILE_TTL_S = 1_209_600  # 14 days
MIN_EMAILS_FOR_PROFILE = 5
MAX_EMAILS_FOR_ANALYSIS = 50

# ---------------------------------------------------------------------------
# Local models
# ---------------------------------------------------------------------------


class StyleProfile(BaseModel):
    """Extracted writing style profile for an advisor."""
    advisor_id: str
    tone: str = Field(
        description="Overall tone: formal, professional, friendly, casual"
    )
    greeting_style: str = Field(
        description="Typical greeting pattern, e.g. 'Dear [Name],' or 'Hi [Name],'"
    )
    closing_style: str = Field(
        description="Typical closing, e.g. 'Best regards,' or 'Thanks,'"
    )
    avg_sentence_length: str = Field(
        description="Short/medium/long — typical sentence length preference"
    )
    vocabulary_level: str = Field(
        description="One of: simple, moderate, sophisticated"
    )
    formality_score: float = Field(
        ge=0.0, le=1.0,
        description="0.0 = very casual, 1.0 = very formal",
    )
    common_phrases: list[str] = Field(
        default_factory=list,
        description="Phrases the advisor frequently uses",
    )
    style_notes: str = Field(
        default="",
        description="Additional style observations",
    )
    sample_count: int = Field(
        default=0,
        description="Number of emails analysed to build this profile",
    )


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

style_extractor: Agent[None, StyleProfile] = Agent(
    model="anthropic:claude-haiku-4-5",
    output_type=StyleProfile,
    defer_model_check=True,
    system_prompt=(
        "You are a writing style analyst. Given a collection of emails written "
        "by a wealth advisor, extract their writing style profile. "
        "Analyse tone, greeting patterns, closings, sentence structure, "
        "vocabulary level, formality, and recurring phrases. "
        "Be specific and provide actionable style descriptors that could be "
        "used to replicate their writing style in AI-drafted emails."
    ),
    retries=2,
)


# ---------------------------------------------------------------------------
# Job entry point
# ---------------------------------------------------------------------------


@with_retry_policy
async def run_style_profile_refresh(
    ctx: dict[str, Any],
    job_ctx_raw: dict | None = None,
    advisor_id: str | None = None,
) -> dict:
    """
    Refresh an advisor's writing style profile.

    - Cron sweep mode (job_ctx_raw is None): fan out per advisor.
    - Per-advisor mode: fetch sent emails, extract style, cache.
    """
    platform = ctx["platform_client"]
    redis = ctx["redis"]
    langfuse = ctx.get("langfuse")

    # ── Cron sweep mode ───────────────────────────────────────────────
    if job_ctx_raw is None:
        logger.info("style_profile: starting cron sweep")
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

                # Check if profile is still fresh
                cache_key = f"sidecar:style_profile:{tenant_id}:{adv_id}"
                ttl = await redis.ttl(cache_key)
                if ttl > STYLE_PROFILE_TTL_S // 2:
                    # Still more than half the TTL remaining, skip
                    continue

                job_ctx = JobContext(
                    tenant_id=tenant_id,
                    actor_id=adv_id,
                    actor_type="service",
                    request_id=f"style-profile-cron-{tenant_id}-{adv_id}",
                    access_scope=scope.model_dump(),
                )
                arq_redis = ctx.get("arq_redis") or redis
                await arq_redis.enqueue_job(
                    "style_profile_refresh",
                    job_ctx.model_dump(),
                    adv_id,
                )
                enqueued += 1
        logger.info("style_profile: cron sweep enqueued %d jobs", enqueued)
        return {"mode": "cron_sweep", "enqueued": enqueued}

    # ── Per-advisor mode ──────────────────────────────────────────────
    job_ctx = JobContext(**job_ctx_raw)
    effective_advisor_id = advisor_id or job_ctx.actor_id
    access_scope = AccessScope(**job_ctx.access_scope)

    tracer: JobTracer | None = None
    if langfuse:
        tracer = JobTracer(
            langfuse=langfuse,
            job_name="style_profile",
            tenant_id=job_ctx.tenant_id,
            actor_id=effective_advisor_id,
        )

    try:
        # Check cache freshness
        cache_key = f"sidecar:style_profile:{job_ctx.tenant_id}:{effective_advisor_id}"
        cached = await redis.get(cache_key)
        if cached:
            ttl = await redis.ttl(cache_key)
            if ttl > STYLE_PROFILE_TTL_S // 2:
                if tracer:
                    tracer.record_cache_hit()
                    tracer.complete(output={"cached": True})
                logger.info("style_profile: cache still fresh for %s", effective_advisor_id)
                return {"status": "cached", "advisor_id": effective_advisor_id}

        if tracer:
            tracer.record_cache_miss()

        # Fetch sent emails
        try:
            sent_emails = await platform.get_advisor_sent_emails(
                effective_advisor_id, access_scope,
            )
        except AttributeError:
            # Fallback: use priority emails as a proxy (advisor's email activity)
            sent_emails = await platform.get_advisor_priority_emails(
                effective_advisor_id, access_scope,
            )
        if tracer:
            tracer.record_platform_read()

        if not sent_emails or len(sent_emails) < MIN_EMAILS_FOR_PROFILE:
            logger.info(
                "style_profile: insufficient emails (%d) for %s",
                len(sent_emails) if sent_emails else 0,
                effective_advisor_id,
            )
            if tracer:
                tracer.complete(output={"status": "insufficient_data"})
            return {
                "status": "insufficient_data",
                "advisor_id": effective_advisor_id,
                "emails_found": len(sent_emails) if sent_emails else 0,
            }

        # Limit to most recent emails
        emails_to_analyse = sent_emails[:MAX_EMAILS_FOR_ANALYSIS]

        # Build prompt
        prompt_lines = [
            f"Analyse the writing style of advisor {effective_advisor_id} "
            f"based on {len(emails_to_analyse)} sent emails.",
            "",
        ]
        for i, email in enumerate(emails_to_analyse, 1):
            obj = email if isinstance(email, dict) else email.model_dump()
            prompt_lines.append(f"### Email {i}")
            prompt_lines.append(f"Subject: {obj.get('subject', '(no subject)')}")
            body = obj.get("body", obj.get("body_preview", ""))
            if body:
                prompt_lines.append(f"Body:\n{body[:500]}")
            prompt_lines.append("")

        prompt = "\n".join(prompt_lines)

        # Run agent
        gen = None
        if tracer:
            gen = tracer.start_generation(
                name="style_extraction",
                model="anthropic:claude-haiku-4-5",
                input_data=f"{len(emails_to_analyse)} emails for {effective_advisor_id}",
            )

        result = await style_extractor.run(prompt)
        profile = result.output

        if tracer and gen:
            tracer.end_generation(gen, output=profile.model_dump())

        # Ensure advisor_id and sample_count are set
        profile.advisor_id = effective_advisor_id
        profile.sample_count = len(emails_to_analyse)

        # Cache with TTL
        await redis.set(
            cache_key,
            profile.model_dump_json(),
            ex=STYLE_PROFILE_TTL_S,
        )

        if tracer:
            tracer.complete(output={
                "advisor_id": effective_advisor_id,
                "formality_score": profile.formality_score,
                "tone": profile.tone,
                "sample_count": profile.sample_count,
            })

        logger.info(
            "style_profile: extracted for %s — tone=%s, formality=%.2f, "
            "%d common phrases, %d emails analysed",
            effective_advisor_id,
            profile.tone,
            profile.formality_score,
            len(profile.common_phrases),
            profile.sample_count,
        )

        return {
            "status": "extracted",
            "advisor_id": effective_advisor_id,
            "tone": profile.tone,
            "formality_score": profile.formality_score,
            "sample_count": profile.sample_count,
        }

    except Exception as exc:
        if tracer:
            tracer.fail(exc, category="style_profile_error")
        raise
