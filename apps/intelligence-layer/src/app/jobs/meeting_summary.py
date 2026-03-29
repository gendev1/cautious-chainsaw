"""
app/jobs/meeting_summary.py — Post-transcription meeting summary job.

Loads a transcript from Redis, truncates to fit context, runs a
pydantic-ai Agent to produce a structured MeetingSummary, stores
the result, and fires a webhook notification.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from pydantic_ai import Agent

from app.jobs.enqueue import JobContext
from app.jobs.observability import JobTracer
from app.jobs.retry import with_retry_policy
from app.models.schemas import (
    MeetingSummary,
)

logger = logging.getLogger("sidecar.jobs.meeting_summary")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_TRANSCRIPT_TOKENS = 80_000
CHARS_PER_TOKEN_ESTIMATE = 4
SUMMARY_TTL_S = 604_800  # 7 days

# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

summary_agent: Agent[None, MeetingSummary] = Agent(
    model="anthropic:claude-sonnet-4-6",
    output_type=MeetingSummary,
    defer_model_check=True,
    system_prompt=(
        "You are a meeting summarizer for a wealth management firm. "
        "Given a meeting transcript, produce a structured MeetingSummary with:\n"
        "- An executive summary (3-5 sentences)\n"
        "- Key topics with speaker attribution and decisions made\n"
        "- Extracted action items with assignees and due dates when mentioned\n"
        "- Suggested follow-up email drafts\n"
        "- Client sentiment assessment (positive/neutral/concerned) if determinable\n"
        "- Next steps\n"
        "- CRM sync payloads for activities and notes\n\n"
        "Be thorough but concise. Use professional language appropriate "
        "for wealth management."
    ),
    retries=2,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate_transcript(transcript: str) -> str:
    """
    Truncate transcript to fit within MAX_TRANSCRIPT_TOKENS.

    Uses a 60/40 head/tail split to preserve both the beginning
    (introductions, agenda) and end (conclusions, action items).
    """
    max_chars = MAX_TRANSCRIPT_TOKENS * CHARS_PER_TOKEN_ESTIMATE

    if len(transcript) <= max_chars:
        return transcript

    head_chars = int(max_chars * 0.6)
    tail_chars = int(max_chars * 0.4)

    head = transcript[:head_chars]
    tail = transcript[-tail_chars:]

    skipped_chars = len(transcript) - head_chars - tail_chars

    return (
        head
        + f"\n\n[--- {skipped_chars:,} characters omitted from middle of transcript ---]\n\n"
        + tail
    )


async def _notify_webhook(
    http_client: Any,
    platform: Any,
    meeting_id: str,
    tenant_id: str,
    summary_key: str,
) -> None:
    """Notify the platform that a meeting summary is ready."""
    try:
        webhook_url = f"{platform._config.base_url}/v1/webhooks/meeting-summary-ready"
        await http_client.post(
            webhook_url,
            json={
                "meeting_id": meeting_id,
                "tenant_id": tenant_id,
                "summary_key": summary_key,
                "event": "meeting_summary.completed",
            },
            headers={
                "Authorization": f"Bearer {platform._config.service_token}",
                "Content-Type": "application/json",
            },
        )
        logger.info("meeting_summary: webhook sent for meeting %s", meeting_id)
    except Exception as exc:
        # Webhook failure should not fail the job
        logger.warning(
            "meeting_summary: webhook failed for meeting %s: %s",
            meeting_id,
            exc,
        )


# ---------------------------------------------------------------------------
# Job entry point
# ---------------------------------------------------------------------------


@with_retry_policy
async def run_meeting_summary(
    ctx: dict[str, Any],
    job_ctx_raw: dict | None = None,
    meeting_id: str | None = None,
    transcript_key: str | None = None,
) -> dict:
    """
    Generate a structured meeting summary from a transcript.

    Loads transcript from Redis, truncates if needed, runs the
    summary agent, stores the result, and sends a webhook.
    """
    if job_ctx_raw is None:
        raise ValueError("run_meeting_summary requires job_ctx_raw")
    if not meeting_id or not transcript_key:
        raise ValueError("meeting_id and transcript_key are required")

    job_ctx = JobContext(**job_ctx_raw)

    platform = ctx["platform_client"]
    redis = ctx["redis"]
    http_client = ctx.get("http_client")
    langfuse = ctx.get("langfuse")

    tracer: JobTracer | None = None
    if langfuse:
        tracer = JobTracer(
            langfuse=langfuse,
            job_name="meeting_summary",
            tenant_id=job_ctx.tenant_id,
            actor_id=job_ctx.actor_id,
            extra_metadata={"meeting_id": meeting_id},
        )

    try:
        # Load transcript from Redis
        raw_transcript = await redis.get(transcript_key)
        if not raw_transcript:
            msg = f"Transcript not found in Redis at key {transcript_key}"
            logger.error("meeting_summary: %s", msg)
            if tracer:
                tracer.fail(ValueError(msg), category="missing_transcript")
            return {"status": "error", "error": msg}

        # Parse transcript JSON
        transcript_data = json.loads(raw_transcript)
        full_text = transcript_data.get("full_text", "")
        duration_seconds = transcript_data.get("duration_seconds", 0)
        segments = transcript_data.get("segments", [])

        if not full_text:
            msg = "Transcript has no text content"
            logger.error("meeting_summary: %s", msg)
            if tracer:
                tracer.fail(ValueError(msg), category="empty_transcript")
            return {"status": "error", "error": msg}

        # Truncate transcript
        truncated = _truncate_transcript(full_text)

        # Detect participants from segments
        speakers = set()
        for seg in segments:
            speaker = seg.get("speaker")
            if speaker:
                speakers.add(speaker)

        # Build prompt
        prompt_lines = [
            f"Summarize the following meeting (ID: {meeting_id}).",
            f"Duration: {duration_seconds:.0f} seconds ({duration_seconds / 60:.1f} minutes).",
        ]
        if speakers:
            prompt_lines.append(f"Detected speakers: {', '.join(sorted(speakers))}")
        prompt_lines.extend([
            "",
            "## Transcript",
            truncated,
        ])
        prompt = "\n".join(prompt_lines)

        # Run agent
        gen = None
        if tracer:
            gen = tracer.start_generation(
                name="meeting_summary_generation",
                model="anthropic:claude-sonnet-4-6",
                input_data=f"meeting {meeting_id}, {len(truncated)} chars",
            )

        result = await summary_agent.run(prompt)
        summary = result.output

        if tracer and gen:
            tracer.end_generation(gen, output=summary.model_dump())

        # Store summary in Redis
        summary_key = f"sidecar:meeting_summary:{job_ctx.tenant_id}:{meeting_id}"
        await redis.set(
            summary_key,
            summary.model_dump_json(),
            ex=SUMMARY_TTL_S,
        )

        # Webhook notification
        if http_client:
            await _notify_webhook(
                http_client, platform, meeting_id, job_ctx.tenant_id, summary_key,
            )

        if tracer:
            tracer.complete(output={
                "meeting_id": meeting_id,
                "topics": len(summary.key_topics),
                "action_items": len(summary.action_items),
                "crm_payloads": len(summary.crm_sync_payloads),
            })

        logger.info(
            "meeting_summary: completed meeting %s — %d topics, %d action items",
            meeting_id,
            len(summary.key_topics),
            len(summary.action_items),
        )

        return {
            "status": "summarized",
            "meeting_id": meeting_id,
            "summary_key": summary_key,
            "topics": len(summary.key_topics),
            "action_items": len(summary.action_items),
            "follow_up_drafts": len(summary.follow_up_drafts),
            "crm_payloads": len(summary.crm_sync_payloads),
        }

    except Exception as exc:
        if tracer:
            tracer.fail(exc, category="meeting_summary_error")
        raise
