# 05 -- Async Job System (ARQ Workers)

This document specifies the implementation of the Python sidecar's background job system. All async work -- daily digests, transcription, meeting summaries, firm-wide reports, email triage, style profile refresh, and RAG index updates -- runs through ARQ workers backed by Redis.

The job system is a separate process from the FastAPI API. It shares the same codebase, models, agents, and platform client but runs in its own event loop with its own concurrency budget.

---

## 1. ARQ Setup

### 1.1 Worker entry point

`app/jobs/worker.py` is the single entry point for the ARQ worker process. It registers all job functions, configures the Redis connection, sets concurrency limits, and attaches cron schedules.

```python
"""app/jobs/worker.py -- ARQ worker entry point."""

from __future__ import annotations

import logging
from typing import Any

from arq import cron
from arq.connections import RedisSettings

from app.config import Settings
from app.jobs.daily_digest import run_daily_digest
from app.jobs.email_triage import run_email_triage
from app.jobs.firm_report import run_firm_report
from app.jobs.meeting_summary import run_meeting_summary
from app.jobs.rag_index import run_rag_index_update
from app.jobs.style_profile import run_style_profile_refresh
from app.jobs.transcription import run_transcription

logger = logging.getLogger("sidecar.worker")


async def startup(ctx: dict[str, Any]) -> None:
    """Called once when the worker process starts.

    Initializes shared resources that persist across job executions:
    platform client, Redis connection pool, Langfuse client, and
    httpx client for external APIs.
    """
    from app.dependencies import build_worker_dependencies

    deps = await build_worker_dependencies()
    ctx["platform_client"] = deps.platform_client
    ctx["redis"] = deps.redis
    ctx["langfuse"] = deps.langfuse
    ctx["http_client"] = deps.http_client
    ctx["settings"] = deps.settings
    logger.info("Worker started, dependencies initialized")


async def shutdown(ctx: dict[str, Any]) -> None:
    """Called once when the worker process shuts down."""
    if http_client := ctx.get("http_client"):
        await http_client.aclose()
    if redis := ctx.get("redis"):
        await redis.aclose()
    logger.info("Worker shut down cleanly")


def get_redis_settings() -> RedisSettings:
    settings = Settings()
    return RedisSettings(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password,
        database=settings.redis_job_db,  # separate DB from cache/sessions
        conn_timeout=10,
        conn_retries=5,
        conn_retry_delay=1.0,
    )


class WorkerSettings:
    """ARQ worker configuration. ARQ discovers this class by name."""

    functions = [
        run_daily_digest,
        run_email_triage,
        run_transcription,
        run_meeting_summary,
        run_firm_report,
        run_style_profile_refresh,
        run_rag_index_update,
    ]

    cron_jobs = [
        # Daily digest -- every day at 05:55 UTC.
        # Individual advisor times are handled by enqueuing per-advisor
        # jobs from a single cron sweep.
        cron(
            run_daily_digest,
            name="daily-digest-sweep",
            hour=5,
            minute=55,
            unique=True,
            timeout=600,
        ),
        # Email triage -- every 15 minutes.
        cron(
            run_email_triage,
            name="email-triage-sweep",
            minute={0, 15, 30, 45},
            unique=True,
            timeout=300,
        ),
        # Style profile refresh -- every Sunday at 02:00 UTC.
        cron(
            run_style_profile_refresh,
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

    # Concurrency: max 10 jobs running at once per worker instance.
    # Scale horizontally by adding worker replicas.
    max_jobs = 10

    # Jobs that exceed their timeout are killed and rescheduled.
    job_timeout = 600  # 10 minutes default

    # How often the worker checks for new jobs.
    poll_delay = 0.5

    # Keep completed job results for 24 hours for status polling.
    keep_result = 86400

    # Retry delay on transient failures (overridden per job).
    retry_jobs = True
    max_tries = 3

    # Health check key -- set in Redis so external monitors can verify
    # the worker is alive.
    health_check_interval = 30
    health_check_key = "sidecar:worker:health"
```

### 1.2 Running the worker

```bash
# Production
arq app.jobs.worker.WorkerSettings

# Development with auto-reload
watchfiles "arq app.jobs.worker.WorkerSettings" app/
```

### 1.3 Enqueuing jobs from the API process

The API process enqueues jobs via the shared Redis connection. Each job receives a `JobContext` Pydantic model carrying tenant isolation data.

```python
"""app/jobs/enqueue.py -- Job enqueue helpers for the API process."""

from __future__ import annotations

from arq.connections import ArqRedis, create_pool
from pydantic import BaseModel

from app.config import Settings
from app.jobs.worker import get_redis_settings


class JobContext(BaseModel):
    """Tenant and actor context propagated to every background job.

    This is the single mechanism for passing identity and scope into
    the worker process. Every job function receives this as its first
    real argument (after the ARQ ctx dict).
    """

    tenant_id: str
    actor_id: str
    actor_type: str  # "advisor", "admin", "service"
    request_id: str
    access_scope: dict  # serialized AccessScope


_pool: ArqRedis | None = None


async def get_job_pool() -> ArqRedis:
    """Lazily create and cache the ARQ Redis pool."""
    global _pool
    if _pool is None:
        _pool = await create_pool(get_redis_settings())
    return _pool


async def enqueue_transcription(
    job_ctx: JobContext,
    meeting_id: str,
    audio_object_key: str,
    audio_duration_seconds: int,
) -> str:
    """Enqueue an audio transcription job. Returns the ARQ job ID."""
    pool = await get_job_pool()
    job = await pool.enqueue_job(
        "run_transcription",
        job_ctx.model_dump(),
        meeting_id,
        audio_object_key,
        audio_duration_seconds,
        _job_timeout=max(600, audio_duration_seconds * 2),
    )
    return job.job_id


async def enqueue_meeting_summary(
    job_ctx: JobContext,
    meeting_id: str,
    transcript_key: str,
) -> str:
    """Enqueue a meeting summary job. Returns the ARQ job ID."""
    pool = await get_job_pool()
    job = await pool.enqueue_job(
        "run_meeting_summary",
        job_ctx.model_dump(),
        meeting_id,
        transcript_key,
    )
    return job.job_id


async def enqueue_firm_report(
    job_ctx: JobContext,
    report_type: str,
    filters: dict | None = None,
) -> str:
    """Enqueue a firm-wide report generation job. Returns the ARQ job ID."""
    pool = await get_job_pool()
    job = await pool.enqueue_job(
        "run_firm_report",
        job_ctx.model_dump(),
        report_type,
        filters or {},
        _job_timeout=1800,  # 30 min for large firms
    )
    return job.job_id


async def enqueue_rag_index_update(
    job_ctx: JobContext,
    source_type: str,
    source_id: str,
    event_type: str,  # "created", "updated", "deleted"
) -> str:
    """Enqueue a RAG index update job. Returns the ARQ job ID."""
    pool = await get_job_pool()
    job = await pool.enqueue_job(
        "run_rag_index_update",
        job_ctx.model_dump(),
        source_type,
        source_id,
        event_type,
    )
    return job.job_id
```

---

## 2. Job Definition Pattern

Every job follows a consistent pattern: deserialize `JobContext`, reconstruct tenant-scoped dependencies, execute the work, store results, and emit telemetry.

### 2.1 Base pattern

```python
"""Canonical job pattern -- all jobs follow this structure."""

from __future__ import annotations

import time
import logging
from typing import Any

from pydantic import BaseModel

from app.jobs.enqueue import JobContext
from app.services.platform_client import PlatformClient, AccessScope

logger = logging.getLogger("sidecar.jobs")


class SomeJobResult(BaseModel):
    """Typed result for this job."""
    status: str
    items_processed: int


async def run_some_job(
    ctx: dict[str, Any],   # ARQ worker context (shared deps)
    job_ctx_raw: dict,     # serialized JobContext
    entity_id: str,        # job-specific argument
) -> dict:
    """Execute the job.

    Args:
        ctx: ARQ worker context containing shared dependencies
             (platform_client, redis, langfuse, http_client, settings).
        job_ctx_raw: Serialized JobContext with tenant/actor/scope.
        entity_id: Job-specific input.

    Returns:
        Serialized result dict (ARQ stores this in Redis).
    """
    job_ctx = JobContext(**job_ctx_raw)
    started_at = time.monotonic()
    langfuse = ctx["langfuse"]

    # Start an observability trace for this job execution.
    trace = langfuse.trace(
        name="some_job",
        metadata={
            "tenant_id": job_ctx.tenant_id,
            "actor_id": job_ctx.actor_id,
            "entity_id": entity_id,
        },
        tags=["job", "some_job"],
    )

    try:
        # Reconstruct scoped dependencies.
        platform: PlatformClient = ctx["platform_client"]
        redis = ctx["redis"]
        scope = AccessScope(**job_ctx.access_scope)

        # --- Job-specific logic here ---
        result = SomeJobResult(status="completed", items_processed=42)

        # Store result in tenant-scoped Redis key with TTL.
        cache_key = f"some_job:{job_ctx.tenant_id}:{entity_id}"
        await redis.set(cache_key, result.model_dump_json(), ex=86400)

        # Record success telemetry.
        duration = time.monotonic() - started_at
        trace.update(
            output=result.model_dump(),
            metadata={"duration_seconds": duration, "status": "success"},
        )
        logger.info(
            "Job completed",
            extra={
                "job": "some_job",
                "tenant_id": job_ctx.tenant_id,
                "entity_id": entity_id,
                "duration": duration,
            },
        )
        return result.model_dump()

    except Exception as exc:
        duration = time.monotonic() - started_at
        trace.update(
            metadata={
                "duration_seconds": duration,
                "status": "error",
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
            level="ERROR",
        )
        logger.exception(
            "Job failed",
            extra={
                "job": "some_job",
                "tenant_id": job_ctx.tenant_id,
                "entity_id": entity_id,
            },
        )
        raise
```

### 2.2 Tenant context propagation

The critical design rule: **every job receives a serialized `JobContext`** as its first real argument. The job deserializes it and uses the `tenant_id` and `access_scope` for every downstream read and cache operation.

This means:
- No global tenant state. Each job call is self-contained.
- The API process serializes the caller's identity at enqueue time.
- The worker never infers tenant from ambient state.
- All cache keys are prefixed with `tenant_id`.
- All platform client calls receive the deserialized `AccessScope`.

```python
# In the API router, when handling POST /ai/meetings/transcribe:
from app.middleware.tenant import get_request_context

@router.post("/ai/meetings/transcribe", status_code=202)
async def transcribe_meeting(request: TranscribeRequest, req_ctx=Depends(get_request_context)):
    job_ctx = JobContext(
        tenant_id=req_ctx.tenant_id,
        actor_id=req_ctx.actor_id,
        actor_type=req_ctx.actor_type,
        request_id=req_ctx.request_id,
        access_scope=req_ctx.access_scope.model_dump(),
    )
    job_id = await enqueue_transcription(
        job_ctx=job_ctx,
        meeting_id=request.meeting_id,
        audio_object_key=request.audio_object_key,
        audio_duration_seconds=request.audio_duration_seconds,
    )
    return {"job_id": job_id, "status": "accepted"}
```

---

## 3. Daily Digest Job

The daily digest job runs as a cron sweep. The cron fires once, queries all advisors in active tenants, and enqueues a per-advisor digest generation. This allows each advisor's digest to fail independently and retry individually.

### 3.1 Sweep + per-advisor dispatch

```python
"""app/jobs/daily_digest.py -- Daily digest generation."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent

from app.jobs.enqueue import JobContext
from app.services.platform_client import PlatformClient, AccessScope

logger = logging.getLogger("sidecar.jobs.digest")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

class DigestItem(BaseModel):
    type: str         # "meeting", "task", "email", "alert", "crm_update"
    title: str
    summary: str
    client_id: str | None = None
    urgency: str      # "high", "medium", "low"
    action_url: str | None = None


class DigestSection(BaseModel):
    title: str        # "Today's Meetings", "Pending Tasks", "Account Alerts"
    items: list[DigestItem]


class PriorityItem(BaseModel):
    title: str
    reason: str
    urgency: str
    related_client_id: str | None = None


class DailyDigest(BaseModel):
    advisor_id: str
    generated_at: str
    greeting: str
    sections: list[DigestSection]
    priority_items: list[PriorityItem]
    suggested_actions: list[dict]  # serialized Action objects


# ---------------------------------------------------------------------------
# Agent (Haiku-tier for cost)
# ---------------------------------------------------------------------------

digest_agent = Agent(
    model="anthropic:claude-haiku-4-5",
    result_type=DailyDigest,
    system_prompt=(
        "You are Hazel, an AI assistant for wealth advisors. "
        "Generate a concise, actionable daily digest. "
        "Prioritize items by urgency and business impact. "
        "Keep summaries to one sentence each. "
        "Group items logically: meetings, tasks, emails, alerts. "
        "Highlight anything that needs immediate attention."
    ),
)


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------

async def _gather_digest_inputs(
    platform: PlatformClient,
    http_client: Any,
    advisor_id: str,
    scope: AccessScope,
    settings: Any,
) -> dict:
    """Fetch all data sources needed for digest generation.

    Runs fetches concurrently where possible. Each fetch is
    individually error-tolerant -- a failure in one source
    degrades the digest but does not block it.
    """
    import asyncio

    async def safe_fetch(coro, label: str):
        try:
            return await coro
        except Exception as exc:
            logger.warning("Digest data fetch failed: %s -- %s", label, exc)
            return []

    meetings_coro = platform.get_advisor_calendar(
        advisor_id, scope, days_ahead=2,
    )
    tasks_coro = platform.get_advisor_tasks(
        advisor_id, scope, status="pending",
    )
    emails_coro = _fetch_priority_emails(http_client, advisor_id, scope, settings)
    alerts_coro = platform.get_account_alerts(
        advisor_id, scope,
    )
    crm_coro = platform.get_crm_activity_feed(
        advisor_id, scope, days_back=1,
    )

    meetings, tasks, emails, alerts, crm_updates = await asyncio.gather(
        safe_fetch(meetings_coro, "calendar"),
        safe_fetch(tasks_coro, "tasks"),
        safe_fetch(emails_coro, "emails"),
        safe_fetch(alerts_coro, "alerts"),
        safe_fetch(crm_coro, "crm"),
    )

    return {
        "meetings": meetings,
        "tasks": tasks,
        "priority_emails": emails,
        "account_alerts": alerts,
        "crm_updates": crm_updates,
    }


async def _fetch_priority_emails(
    http_client: Any,
    advisor_id: str,
    scope: AccessScope,
    settings: Any,
) -> list[dict]:
    """Fetch unread priority emails via the email adapter."""
    resp = await http_client.get(
        f"{settings.platform_base_url}/integrations/email/unread",
        params={"advisor_id": advisor_id, "limit": 20},
        headers={
            "X-Tenant-ID": scope.tenant_id,
            "X-Access-Scope": scope.to_header(),
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("emails", [])


# ---------------------------------------------------------------------------
# Job function
# ---------------------------------------------------------------------------

async def run_daily_digest(
    ctx: dict[str, Any],
    job_ctx_raw: dict | None = None,
    advisor_id: str | None = None,
) -> dict:
    """Generate the daily digest.

    When called by cron (no arguments), this sweeps all active advisors
    in all tenants and enqueues per-advisor digest jobs.

    When called with job_ctx_raw and advisor_id, this generates the
    digest for a single advisor.
    """
    platform: PlatformClient = ctx["platform_client"]
    redis = ctx["redis"]
    langfuse = ctx["langfuse"]
    settings = ctx["settings"]

    # ------------------------------------------------------------------
    # Cron sweep mode: discover advisors and fan out
    # ------------------------------------------------------------------
    if job_ctx_raw is None:
        logger.info("Daily digest cron sweep started")
        from arq.connections import ArqRedis

        pool: ArqRedis = ctx["redis"]  # reuse the worker's Redis

        tenants = await platform.list_active_tenants()
        total_enqueued = 0

        for tenant in tenants:
            advisors = await platform.list_advisors(
                tenant_id=tenant["tenant_id"],
            )
            for adv in advisors:
                adv_job_ctx = JobContext(
                    tenant_id=tenant["tenant_id"],
                    actor_id=adv["advisor_id"],
                    actor_type="system",
                    request_id=f"digest-{adv['advisor_id']}-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
                    access_scope={
                        "visibility_mode": "advisor_scope",
                        "advisor_ids": [adv["advisor_id"]],
                    },
                )
                await pool.enqueue_job(
                    "run_daily_digest",
                    adv_job_ctx.model_dump(),
                    adv["advisor_id"],
                    _job_id=f"digest:{adv['advisor_id']}:{datetime.now(timezone.utc).strftime('%Y%m%d')}",
                )
                total_enqueued += 1

        logger.info("Daily digest sweep enqueued %d advisor digests", total_enqueued)
        return {"mode": "sweep", "enqueued": total_enqueued}

    # ------------------------------------------------------------------
    # Per-advisor mode: generate the actual digest
    # ------------------------------------------------------------------
    job_ctx = JobContext(**job_ctx_raw)
    started_at = time.monotonic()

    trace = langfuse.trace(
        name="daily_digest",
        metadata={
            "tenant_id": job_ctx.tenant_id,
            "advisor_id": advisor_id,
        },
        tags=["job", "daily_digest"],
    )

    try:
        scope = AccessScope(**job_ctx.access_scope)

        # Gather all data sources concurrently.
        inputs = await _gather_digest_inputs(
            platform, ctx["http_client"], advisor_id, scope, settings,
        )

        # Build the prompt context from gathered data.
        prompt_context = (
            f"Generate a daily digest for advisor {advisor_id}.\n\n"
            f"Date: {datetime.now(timezone.utc).strftime('%A, %B %d, %Y')}\n\n"
            f"Today's meetings ({len(inputs['meetings'])}):\n"
            + "\n".join(
                f"- {m.get('title', 'Untitled')} at {m.get('start_time', '?')} "
                f"with {m.get('client_name', 'Unknown')}"
                for m in inputs["meetings"]
            )
            + f"\n\nPending tasks ({len(inputs['tasks'])}):\n"
            + "\n".join(
                f"- {t.get('title', 'Untitled')} (due: {t.get('due_date', 'none')}, "
                f"priority: {t.get('priority', 'normal')})"
                for t in inputs["tasks"]
            )
            + f"\n\nPriority emails ({len(inputs['priority_emails'])}):\n"
            + "\n".join(
                f"- From {e.get('from', '?')}: {e.get('subject', 'No subject')} "
                f"({e.get('preview', '')[:100]})"
                for e in inputs["priority_emails"]
            )
            + f"\n\nAccount alerts ({len(inputs['account_alerts'])}):\n"
            + "\n".join(
                f"- [{a.get('severity', 'info')}] {a.get('title', '')}: {a.get('description', '')}"
                for a in inputs["account_alerts"]
            )
            + f"\n\nCRM updates ({len(inputs['crm_updates'])}):\n"
            + "\n".join(
                f"- {c.get('type', '?')}: {c.get('summary', '')}"
                for c in inputs["crm_updates"]
            )
        )

        # Run the digest agent.
        generation = trace.generation(name="digest_agent")
        result = await digest_agent.run(prompt_context)
        digest = result.data
        generation.end(
            output=digest.model_dump(),
            usage={"total_tokens": getattr(result, "token_usage", None)},
        )

        # Store in Redis with 24-hour TTL.
        date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cache_key = f"digest:{job_ctx.tenant_id}:{advisor_id}:{date_key}"
        await redis.set(cache_key, digest.model_dump_json(), ex=86400)

        duration = time.monotonic() - started_at
        trace.update(
            output={"status": "success", "sections": len(digest.sections)},
            metadata={"duration_seconds": duration},
        )
        logger.info(
            "Digest generated for advisor %s in %.1fs",
            advisor_id,
            duration,
        )
        return digest.model_dump()

    except Exception as exc:
        trace.update(
            metadata={
                "status": "error",
                "error": str(exc),
                "duration_seconds": time.monotonic() - started_at,
            },
            level="ERROR",
        )
        raise
```

---

## 4. Transcription Job

The transcription job handles audio files of arbitrary length. It downloads audio from platform-managed object storage, chunks long recordings into segments, sends each segment to the transcription API, reassembles the transcript, stores it, and chains into the meeting summary job.

### 4.1 Implementation

```python
"""app/jobs/transcription.py -- Audio transcription job."""

from __future__ import annotations

import io
import logging
import math
import time
from typing import Any

import httpx
from pydantic import BaseModel

from app.jobs.enqueue import JobContext, enqueue_meeting_summary

logger = logging.getLogger("sidecar.jobs.transcription")

# Maximum segment duration for chunked processing (seconds).
# Whisper API accepts up to 25 MB / ~25 min. We chunk at 20 min
# for safety margin.
MAX_SEGMENT_SECONDS = 1200  # 20 minutes
OVERLAP_SECONDS = 30  # overlap between chunks for continuity


class TranscriptSegment(BaseModel):
    index: int
    start_seconds: float
    end_seconds: float
    text: str
    confidence: float | None = None
    words: list[dict] | None = None  # word-level timestamps if available


class TranscriptionResult(BaseModel):
    meeting_id: str
    audio_object_key: str
    duration_seconds: int
    segments: list[TranscriptSegment]
    full_text: str
    provider: str  # "whisper" or "deepgram"
    language: str | None = None
    diarization: list[dict] | None = None  # speaker segments if available


async def _download_audio(
    http_client: httpx.AsyncClient,
    settings: Any,
    audio_object_key: str,
    tenant_id: str,
) -> bytes:
    """Download audio from platform-managed object storage."""
    resp = await http_client.get(
        f"{settings.platform_base_url}/storage/objects/{audio_object_key}",
        headers={"X-Tenant-ID": tenant_id},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.content


async def _transcribe_segment_whisper(
    http_client: httpx.AsyncClient,
    audio_bytes: bytes,
    segment_index: int,
    settings: Any,
) -> TranscriptSegment:
    """Send a single audio segment to the OpenAI Whisper API."""
    resp = await http_client.post(
        "https://api.openai.com/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        files={"file": (f"segment_{segment_index}.wav", io.BytesIO(audio_bytes), "audio/wav")},
        data={
            "model": "whisper-1",
            "response_format": "verbose_json",
            "timestamp_granularities[]": "word",
            "language": "en",
        },
        timeout=300,
    )
    resp.raise_for_status()
    data = resp.json()

    return TranscriptSegment(
        index=segment_index,
        start_seconds=0,  # adjusted by caller with offset
        end_seconds=data.get("duration", 0),
        text=data.get("text", ""),
        confidence=None,
        words=data.get("words"),
    )


async def _transcribe_segment_deepgram(
    http_client: httpx.AsyncClient,
    audio_bytes: bytes,
    segment_index: int,
    settings: Any,
) -> TranscriptSegment:
    """Send a single audio segment to the Deepgram API."""
    resp = await http_client.post(
        "https://api.deepgram.com/v1/listen",
        headers={
            "Authorization": f"Token {settings.deepgram_api_key}",
            "Content-Type": "audio/wav",
        },
        params={
            "model": "nova-3",
            "smart_format": "true",
            "diarize": "true",
            "punctuate": "true",
            "utterances": "true",
            "language": "en",
        },
        content=audio_bytes,
        timeout=300,
    )
    resp.raise_for_status()
    data = resp.json()

    channel = data["results"]["channels"][0]
    alt = channel["alternatives"][0]

    return TranscriptSegment(
        index=segment_index,
        start_seconds=0,
        end_seconds=data.get("metadata", {}).get("duration", 0),
        text=alt.get("transcript", ""),
        confidence=alt.get("confidence"),
        words=alt.get("words"),
    )


def _chunk_audio_bytes(
    audio_bytes: bytes,
    total_duration: int,
    sample_rate: int = 16000,
    bytes_per_sample: int = 2,
) -> list[tuple[int, bytes, float, float]]:
    """Split audio bytes into overlapping segments.

    Returns list of (index, chunk_bytes, start_seconds, end_seconds).

    For production, this would use a proper audio library (pydub, ffmpeg).
    This implementation shows the chunking logic.
    """
    if total_duration <= MAX_SEGMENT_SECONDS:
        return [(0, audio_bytes, 0.0, float(total_duration))]

    bytes_per_second = sample_rate * bytes_per_sample
    segment_bytes = MAX_SEGMENT_SECONDS * bytes_per_second
    overlap_bytes = OVERLAP_SECONDS * bytes_per_second

    chunks = []
    offset = 0
    index = 0

    # Skip WAV header (44 bytes) for raw PCM chunking.
    header = audio_bytes[:44]
    pcm_data = audio_bytes[44:]

    while offset < len(pcm_data):
        end = min(offset + segment_bytes, len(pcm_data))
        chunk_pcm = pcm_data[offset:end]

        start_sec = offset / bytes_per_second
        end_sec = end / bytes_per_second

        # Reconstruct a valid WAV for each chunk.
        chunk_wav = _make_wav_header(len(chunk_pcm), sample_rate, bytes_per_sample) + chunk_pcm
        chunks.append((index, chunk_wav, start_sec, end_sec))

        # Advance with overlap.
        offset = end - overlap_bytes if end < len(pcm_data) else end
        index += 1

    return chunks


def _make_wav_header(data_size: int, sample_rate: int, bytes_per_sample: int) -> bytes:
    """Construct a minimal WAV header for a mono PCM chunk."""
    import struct

    channels = 1
    byte_rate = sample_rate * channels * bytes_per_sample
    block_align = channels * bytes_per_sample
    bits_per_sample = bytes_per_sample * 8

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )
    return header


async def run_transcription(
    ctx: dict[str, Any],
    job_ctx_raw: dict,
    meeting_id: str,
    audio_object_key: str,
    audio_duration_seconds: int,
) -> dict:
    """Transcribe a meeting audio file.

    Pipeline:
    1. Download audio from object storage.
    2. Chunk into segments if longer than MAX_SEGMENT_SECONDS.
    3. Transcribe each segment via Whisper or Deepgram.
    4. Reassemble full transcript with corrected timestamps.
    5. Store raw transcript in Redis.
    6. Trigger meeting summary job.
    """
    job_ctx = JobContext(**job_ctx_raw)
    started_at = time.monotonic()
    langfuse = ctx["langfuse"]
    redis = ctx["redis"]
    http_client: httpx.AsyncClient = ctx["http_client"]
    settings = ctx["settings"]

    trace = langfuse.trace(
        name="transcription",
        metadata={
            "tenant_id": job_ctx.tenant_id,
            "meeting_id": meeting_id,
            "audio_duration_seconds": audio_duration_seconds,
        },
        tags=["job", "transcription"],
    )

    try:
        # 1. Download audio.
        download_span = trace.span(name="download_audio")
        audio_bytes = await _download_audio(
            http_client, settings, audio_object_key, job_ctx.tenant_id,
        )
        download_span.end(metadata={"size_bytes": len(audio_bytes)})

        # 2. Chunk audio for long recordings.
        chunk_span = trace.span(name="chunk_audio")
        chunks = _chunk_audio_bytes(audio_bytes, audio_duration_seconds)
        chunk_span.end(metadata={"num_chunks": len(chunks)})
        logger.info(
            "Transcribing meeting %s: %d seconds, %d chunks",
            meeting_id,
            audio_duration_seconds,
            len(chunks),
        )

        # 3. Transcribe each chunk.
        # Choose provider based on settings.
        provider = settings.transcription_provider  # "whisper" or "deepgram"
        transcribe_fn = (
            _transcribe_segment_whisper
            if provider == "whisper"
            else _transcribe_segment_deepgram
        )

        segments: list[TranscriptSegment] = []
        for idx, chunk_bytes, start_sec, end_sec in chunks:
            seg_span = trace.span(
                name=f"transcribe_segment_{idx}",
                metadata={"start": start_sec, "end": end_sec},
            )
            segment = await transcribe_fn(http_client, chunk_bytes, idx, settings)

            # Adjust timestamps to global offset.
            segment.start_seconds = start_sec
            segment.end_seconds = end_sec
            segment.index = idx
            segments.append(segment)
            seg_span.end()

        # 4. Reassemble full transcript.
        # For overlapping segments, we take the first occurrence
        # (overlap is for continuity at chunk boundaries, not dedup).
        full_text = "\n\n".join(seg.text for seg in segments)

        result = TranscriptionResult(
            meeting_id=meeting_id,
            audio_object_key=audio_object_key,
            duration_seconds=audio_duration_seconds,
            segments=segments,
            full_text=full_text,
            provider=provider,
            language="en",
        )

        # 5. Store raw transcript in Redis (48h TTL -- platform should
        # fetch and persist authoritatively before expiry).
        transcript_key = f"transcript:{job_ctx.tenant_id}:{meeting_id}"
        await redis.set(
            transcript_key,
            result.model_dump_json(),
            ex=172800,
        )

        # 6. Chain into meeting summary job.
        await enqueue_meeting_summary(
            job_ctx=job_ctx,
            meeting_id=meeting_id,
            transcript_key=transcript_key,
        )

        duration = time.monotonic() - started_at
        trace.update(
            output={
                "status": "success",
                "segments": len(segments),
                "full_text_length": len(full_text),
            },
            metadata={"duration_seconds": duration, "provider": provider},
        )
        logger.info(
            "Transcription complete for meeting %s in %.1fs (%d segments)",
            meeting_id,
            duration,
            len(segments),
        )
        return result.model_dump()

    except Exception as exc:
        trace.update(
            metadata={
                "status": "error",
                "error": str(exc),
                "duration_seconds": time.monotonic() - started_at,
            },
            level="ERROR",
        )
        raise
```

---

## 5. Meeting Summary Job

Triggered automatically after transcription completes. Loads the raw transcript, runs the summarization agent, extracts action items and follow-up drafts, generates CRM sync payloads, and stores the result.

```python
"""app/jobs/meeting_summary.py -- Post-transcription meeting summary."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent

from app.jobs.enqueue import JobContext
from app.services.platform_client import PlatformClient, AccessScope

logger = logging.getLogger("sidecar.jobs.meeting_summary")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

class TopicSection(BaseModel):
    topic: str
    summary: str
    speaker_attribution: dict[str, str]
    decisions_made: list[str]


class ExtractedTask(BaseModel):
    title: str
    assignee: str | None = None
    due_hint: str | None = None  # "next week", "by Friday", etc.
    priority: str = "normal"
    context: str = ""


class FollowUpDraft(BaseModel):
    recipient: str
    subject: str
    body: str
    reason: str  # why this follow-up is suggested


class CRMSyncPayload(BaseModel):
    action: str  # "create_note", "create_task", "update_contact"
    entity_type: str
    entity_id: str | None = None
    data: dict


class MeetingSummary(BaseModel):
    meeting_id: str
    duration_minutes: int
    participants: list[str]
    executive_summary: str
    key_topics: list[TopicSection]
    action_items: list[ExtractedTask]
    follow_up_drafts: list[FollowUpDraft]
    client_sentiment: str | None = None  # "positive", "neutral", "concerned"
    next_steps: list[str]
    crm_sync_payloads: list[CRMSyncPayload]


# ---------------------------------------------------------------------------
# Agent (Copilot-tier: needs nuance for summaries)
# ---------------------------------------------------------------------------

summary_agent = Agent(
    model="anthropic:claude-sonnet-4-6",
    fallback_model="openai:gpt-4o",
    result_type=MeetingSummary,
    system_prompt="""\
You are Hazel, an AI assistant for wealth advisors. Summarize the meeting
transcript below. Extract:

1. An executive summary (3-5 sentences).
2. Key topics discussed with speaker attribution where identifiable.
3. Decisions made during the meeting.
4. Action items with assignee and deadline hints.
5. Suggested follow-up emails to send after the meeting.
6. Client sentiment (positive, neutral, concerned) based on tone.
7. CRM sync payloads: notes to log, tasks to create, contact updates.

Be specific. Use names and dollar amounts from the transcript.
Do not hallucinate details not present in the transcript.
""",
)


# ---------------------------------------------------------------------------
# Transcript chunking for long meetings
# ---------------------------------------------------------------------------

MAX_TRANSCRIPT_TOKENS = 80_000  # stay within model context window
CHARS_PER_TOKEN_ESTIMATE = 4


def _truncate_transcript(full_text: str) -> str:
    """Truncate transcript to fit within model context limits.

    For transcripts that exceed the context window, we take the
    first portion and the last portion, with a note about the gap.
    This preserves the meeting opening (introductions, agenda) and
    closing (decisions, next steps).
    """
    max_chars = MAX_TRANSCRIPT_TOKENS * CHARS_PER_TOKEN_ESTIMATE
    if len(full_text) <= max_chars:
        return full_text

    # Take 60% from the start, 40% from the end.
    head_chars = int(max_chars * 0.6)
    tail_chars = int(max_chars * 0.4)

    head = full_text[:head_chars]
    tail = full_text[-tail_chars:]
    skipped_chars = len(full_text) - head_chars - tail_chars

    return (
        head
        + f"\n\n[--- {skipped_chars:,} characters omitted from middle of transcript ---]\n\n"
        + tail
    )


# ---------------------------------------------------------------------------
# Job function
# ---------------------------------------------------------------------------

async def run_meeting_summary(
    ctx: dict[str, Any],
    job_ctx_raw: dict,
    meeting_id: str,
    transcript_key: str,
) -> dict:
    """Generate a meeting summary from a completed transcript.

    Pipeline:
    1. Load raw transcript from Redis.
    2. Fetch meeting metadata from platform (participants, duration).
    3. Truncate transcript if needed to fit context window.
    4. Run summarization agent.
    5. Store summary in Redis.
    6. Notify platform of completed summary (webhook or status update).
    """
    job_ctx = JobContext(**job_ctx_raw)
    started_at = time.monotonic()
    langfuse = ctx["langfuse"]
    redis = ctx["redis"]
    platform: PlatformClient = ctx["platform_client"]
    http_client = ctx["http_client"]
    settings = ctx["settings"]

    trace = langfuse.trace(
        name="meeting_summary",
        metadata={
            "tenant_id": job_ctx.tenant_id,
            "meeting_id": meeting_id,
        },
        tags=["job", "meeting_summary"],
    )

    try:
        scope = AccessScope(**job_ctx.access_scope)

        # 1. Load transcript from Redis.
        raw_transcript_json = await redis.get(transcript_key)
        if raw_transcript_json is None:
            raise ValueError(
                f"Transcript not found in Redis: {transcript_key}. "
                "It may have expired before summary job ran."
            )
        transcript_data = json.loads(raw_transcript_json)
        full_text = transcript_data["full_text"]

        # 2. Fetch meeting metadata.
        meeting_meta = await platform.get_meeting_metadata(
            meeting_id, scope,
        )

        # 3. Truncate if needed.
        processed_text = _truncate_transcript(full_text)

        # 4. Run the summarization agent.
        prompt = (
            f"Meeting ID: {meeting_id}\n"
            f"Duration: {meeting_meta.get('duration_minutes', 'unknown')} minutes\n"
            f"Participants: {', '.join(meeting_meta.get('participants', ['unknown']))}\n"
            f"Date: {meeting_meta.get('date', 'unknown')}\n\n"
            f"--- TRANSCRIPT ---\n\n{processed_text}"
        )

        generation = trace.generation(name="summary_agent")
        result = await summary_agent.run(prompt)
        summary: MeetingSummary = result.data
        generation.end(
            output=summary.model_dump(),
            usage={"total_tokens": getattr(result, "token_usage", None)},
        )

        # 5. Store summary in Redis (72h TTL).
        summary_key = f"meeting_summary:{job_ctx.tenant_id}:{meeting_id}"
        await redis.set(summary_key, summary.model_dump_json(), ex=259200)

        # 6. Notify platform that the summary is ready.
        try:
            await http_client.post(
                f"{settings.platform_base_url}/webhooks/meeting-summary-ready",
                json={
                    "meeting_id": meeting_id,
                    "tenant_id": job_ctx.tenant_id,
                    "summary_key": summary_key,
                    "action_items_count": len(summary.action_items),
                    "follow_up_drafts_count": len(summary.follow_up_drafts),
                },
                headers={"X-Tenant-ID": job_ctx.tenant_id},
                timeout=10,
            )
        except Exception as notify_exc:
            # Notification failure is non-fatal. Platform can poll.
            logger.warning(
                "Failed to notify platform of summary completion: %s",
                notify_exc,
            )

        duration = time.monotonic() - started_at
        trace.update(
            output={
                "status": "success",
                "action_items": len(summary.action_items),
                "topics": len(summary.key_topics),
            },
            metadata={"duration_seconds": duration},
        )
        logger.info(
            "Meeting summary generated for %s in %.1fs: %d topics, %d action items",
            meeting_id,
            duration,
            len(summary.key_topics),
            len(summary.action_items),
        )
        return summary.model_dump()

    except Exception as exc:
        trace.update(
            metadata={
                "status": "error",
                "error": str(exc),
                "duration_seconds": time.monotonic() - started_at,
            },
            level="ERROR",
        )
        raise
```

---

## 6. Firm-Wide Report Job

Scans all accounts in a tenant, runs per-account analysis, aggregates results into a `FirmWideReport`, and stores the artifact reference for the platform to publish.

```python
"""app/jobs/firm_report.py -- Firm-wide analytical report generation."""

from __future__ import annotations

import logging
import time
from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent

from app.jobs.enqueue import JobContext
from app.services.platform_client import PlatformClient, AccessScope

logger = logging.getLogger("sidecar.jobs.firm_report")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

class FlaggedItem(BaseModel):
    client_id: str
    client_name: str
    account_id: str | None = None
    issue: str
    severity: str   # "critical", "warning", "info"
    recommended_action: dict
    estimated_impact: float | None = None


class ReportSection(BaseModel):
    title: str
    summary: str
    items: list[FlaggedItem]
    charts_data: dict | None = None  # structured data for frontend charting


class FirmWideReport(BaseModel):
    firm_id: str
    generated_at: str
    report_type: str
    summary: str
    sections: list[ReportSection]
    flagged_items: list[FlaggedItem]
    total_opportunity: float | None = None
    accounts_scanned: int
    accounts_flagged: int


# ---------------------------------------------------------------------------
# Per-account analysis agent (Haiku-tier for throughput)
# ---------------------------------------------------------------------------

class AccountAnalysis(BaseModel):
    account_id: str
    client_id: str
    client_name: str
    findings: list[dict]
    alerts: list[dict]
    opportunity_value: float


account_analyst = Agent(
    model="anthropic:claude-haiku-4-5",
    result_type=AccountAnalysis,
    system_prompt="""\
Analyze the account data below. Identify:
- Concentration risk (single position > 10% of portfolio, sector > 30%)
- Drift from target allocation
- RMD status for retirement accounts (client age, required distributions)
- Tax-loss harvesting opportunities (unrealized losses > $1000)
- Missing or outdated beneficiary designations
- Excessive cash drag (> 5% uninvested cash)

Return structured findings with severity and estimated dollar impact.
Only flag genuine issues -- do not manufacture findings.
""",
)


# ---------------------------------------------------------------------------
# Report aggregation agent (Opus-tier for accuracy)
# ---------------------------------------------------------------------------

report_aggregator = Agent(
    model="anthropic:claude-opus-4-6",
    result_type=FirmWideReport,
    system_prompt="""\
You are generating a firm-wide analytical report. Aggregate the per-account
findings below into a cohesive report with:

1. Executive summary of firm health.
2. Sections grouped by issue type (concentration, RMD, tax-loss, etc.).
3. Flagged items sorted by severity and estimated impact.
4. Total opportunity value across the firm.

Be precise with numbers. Reference specific clients and accounts.
This report will be reviewed by the firm principal.
""",
)


# ---------------------------------------------------------------------------
# Job function
# ---------------------------------------------------------------------------

ACCOUNT_BATCH_SIZE = 20  # process accounts in batches to limit concurrency


async def run_firm_report(
    ctx: dict[str, Any],
    job_ctx_raw: dict,
    report_type: str,
    filters: dict,
) -> dict:
    """Generate a firm-wide analytical report.

    Pipeline:
    1. Fetch all accounts in the tenant (with optional filters).
    2. Run per-account analysis in batches.
    3. Aggregate per-account findings into a firm-level report.
    4. Store the report artifact reference.
    """
    import asyncio
    from datetime import datetime, timezone

    job_ctx = JobContext(**job_ctx_raw)
    started_at = time.monotonic()
    langfuse = ctx["langfuse"]
    redis = ctx["redis"]
    platform: PlatformClient = ctx["platform_client"]
    http_client = ctx["http_client"]
    settings = ctx["settings"]

    trace = langfuse.trace(
        name="firm_report",
        metadata={
            "tenant_id": job_ctx.tenant_id,
            "report_type": report_type,
        },
        tags=["job", "firm_report"],
    )

    try:
        scope = AccessScope(**job_ctx.access_scope)

        # 1. Fetch all accounts.
        accounts = await platform.get_firm_accounts(
            filters=filters,
            access_scope=scope,
        )
        logger.info(
            "Firm report scanning %d accounts for tenant %s",
            len(accounts),
            job_ctx.tenant_id,
        )

        # 2. Run per-account analysis in batches.
        all_analyses: list[AccountAnalysis] = []

        for batch_start in range(0, len(accounts), ACCOUNT_BATCH_SIZE):
            batch = accounts[batch_start : batch_start + ACCOUNT_BATCH_SIZE]

            async def analyze_one(acct: dict) -> AccountAnalysis:
                # Fetch detailed account data.
                detail = await platform.get_account_summary(
                    acct["account_id"], scope,
                )
                client = await platform.get_client_profile(
                    acct.get("client_id", ""), scope,
                )

                prompt = (
                    f"Account: {acct['account_id']}\n"
                    f"Client: {client.get('name', 'Unknown')} "
                    f"(age: {client.get('age', 'unknown')})\n"
                    f"Account type: {detail.get('registration_type', 'unknown')}\n"
                    f"Total value: ${detail.get('total_value', 0):,.2f}\n"
                    f"Holdings:\n"
                    + "\n".join(
                        f"  - {h.get('symbol', '?')}: ${h.get('value', 0):,.2f} "
                        f"({h.get('weight', 0):.1f}%) "
                        f"unrealized G/L: ${h.get('unrealized_gl', 0):,.2f}"
                        for h in detail.get("holdings", [])
                    )
                    + f"\nCash: ${detail.get('cash_balance', 0):,.2f}\n"
                    f"Beneficiary on file: {detail.get('has_beneficiary', 'unknown')}\n"
                    f"Target allocation: {detail.get('target_allocation', 'none')}\n"
                    f"Report type focus: {report_type}"
                )

                gen = trace.generation(
                    name=f"account_analysis_{acct['account_id']}",
                )
                result = await account_analyst.run(prompt)
                gen.end()
                return result.data

            batch_results = await asyncio.gather(
                *(analyze_one(acct) for acct in batch),
                return_exceptions=True,
            )

            for acct, result in zip(batch, batch_results):
                if isinstance(result, Exception):
                    logger.warning(
                        "Account analysis failed for %s: %s",
                        acct.get("account_id"),
                        result,
                    )
                    continue
                all_analyses.append(result)

        # 3. Aggregate into firm-level report.
        aggregation_prompt = (
            f"Report type: {report_type}\n"
            f"Firm/Tenant: {job_ctx.tenant_id}\n"
            f"Accounts scanned: {len(accounts)}\n"
            f"Accounts with findings: {len(all_analyses)}\n\n"
            "Per-account findings:\n\n"
            + "\n---\n".join(
                f"Account {a.account_id} (Client: {a.client_name}):\n"
                f"  Findings: {len(a.findings)}\n"
                + "\n".join(
                    f"    - [{f.get('severity', '?')}] {f.get('title', '')}: "
                    f"{f.get('description', '')}"
                    for f in a.findings
                )
                + f"\n  Opportunity value: ${a.opportunity_value:,.2f}"
                for a in all_analyses
            )
        )

        agg_gen = trace.generation(name="report_aggregation")
        agg_result = await report_aggregator.run(aggregation_prompt)
        report: FirmWideReport = agg_result.data
        agg_gen.end(
            output=report.model_dump(),
            usage={"total_tokens": getattr(agg_result, "token_usage", None)},
        )

        # 4. Store report artifact.
        report_key = (
            f"firm_report:{job_ctx.tenant_id}:{report_type}"
            f":{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        )
        await redis.set(report_key, report.model_dump_json(), ex=604800)  # 7-day TTL

        # Also store a pointer to the latest report of this type.
        latest_key = f"firm_report_latest:{job_ctx.tenant_id}:{report_type}"
        await redis.set(latest_key, report_key, ex=604800)

        duration = time.monotonic() - started_at
        trace.update(
            output={
                "status": "success",
                "accounts_scanned": len(accounts),
                "flagged_items": len(report.flagged_items),
                "total_opportunity": report.total_opportunity,
            },
            metadata={"duration_seconds": duration},
        )
        logger.info(
            "Firm report (%s) complete: %d accounts, %d flagged, %.1fs",
            report_type,
            len(accounts),
            len(report.flagged_items),
            duration,
        )
        return {
            "report_key": report_key,
            **report.model_dump(),
        }

    except Exception as exc:
        trace.update(
            metadata={
                "status": "error",
                "error": str(exc),
                "duration_seconds": time.monotonic() - started_at,
            },
            level="ERROR",
        )
        raise
```

---

## 7. Email Triage Job

Runs every 15 minutes per advisor. Fetches new emails from the platform email adapter, triages them in a batch via the triage agent, and stores results for the advisor's inbox view.

```python
"""app/jobs/email_triage.py -- Scheduled email inbox triage."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent

from app.jobs.enqueue import JobContext
from app.services.platform_client import PlatformClient, AccessScope

logger = logging.getLogger("sidecar.jobs.email_triage")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class IncomingEmail(BaseModel):
    email_id: str
    from_address: str
    subject: str
    body_preview: str
    received_at: str
    thread_id: str | None = None
    has_attachments: bool = False


class TriagedEmail(BaseModel):
    email_id: str
    priority: str       # "urgent", "high", "normal", "low", "informational"
    category: str       # "client_request", "compliance", "prospect", "vendor", "internal", "newsletter"
    client_id: str | None = None
    summary: str
    suggested_action: str  # "reply_now", "reply_today", "delegate", "archive", "review_later"
    draft_reply: dict | None = None
    reasoning: str


class TriageBatchResult(BaseModel):
    advisor_id: str
    triaged_at: str
    emails: list[TriagedEmail]
    urgent_count: int
    high_count: int


# ---------------------------------------------------------------------------
# Agent (Haiku-tier for throughput)
# ---------------------------------------------------------------------------

triage_agent = Agent(
    model="anthropic:claude-haiku-4-5",
    result_type=list[TriagedEmail],
    system_prompt="""\
You are Hazel, an AI assistant for wealth advisors. Triage the batch of
incoming emails below. For each email:

1. Assign a priority: urgent, high, normal, low, informational.
2. Categorize: client_request, compliance, prospect, vendor, internal, newsletter.
3. Match to a known client if the sender matches platform records.
4. Write a one-sentence summary.
5. Suggest an action: reply_now, reply_today, delegate, archive, review_later.
6. For urgent/high emails, draft a brief reply.
7. Explain your reasoning in one sentence.

Prioritize by business impact. Client requests and compliance matters
are typically higher priority than vendor or newsletter emails.
""",
)


# ---------------------------------------------------------------------------
# Job function
# ---------------------------------------------------------------------------

EMAIL_BATCH_SIZE = 50  # max emails per agent call (context window limit)


async def run_email_triage(
    ctx: dict[str, Any],
    job_ctx_raw: dict | None = None,
    advisor_id: str | None = None,
) -> dict:
    """Triage advisor email inbox.

    When called by cron (no arguments), sweeps all advisors and
    enqueues per-advisor triage jobs.

    When called with job_ctx_raw and advisor_id, triages that
    advisor's new emails.
    """
    platform: PlatformClient = ctx["platform_client"]
    redis = ctx["redis"]
    langfuse = ctx["langfuse"]
    http_client = ctx["http_client"]
    settings = ctx["settings"]

    # ------------------------------------------------------------------
    # Cron sweep mode
    # ------------------------------------------------------------------
    if job_ctx_raw is None:
        logger.info("Email triage cron sweep started")
        from arq.connections import ArqRedis

        pool: ArqRedis = ctx["redis"]
        tenants = await platform.list_active_tenants()
        total = 0

        for tenant in tenants:
            advisors = await platform.list_advisors(tenant_id=tenant["tenant_id"])
            for adv in advisors:
                # Only triage for advisors with email integration enabled.
                if not adv.get("email_integration_enabled", False):
                    continue
                adv_ctx = JobContext(
                    tenant_id=tenant["tenant_id"],
                    actor_id=adv["advisor_id"],
                    actor_type="system",
                    request_id=f"triage-{adv['advisor_id']}-{int(time.time())}",
                    access_scope={
                        "visibility_mode": "advisor_scope",
                        "advisor_ids": [adv["advisor_id"]],
                    },
                )
                await pool.enqueue_job(
                    "run_email_triage",
                    adv_ctx.model_dump(),
                    adv["advisor_id"],
                    _job_id=f"triage:{adv['advisor_id']}:{int(time.time())}",
                )
                total += 1

        logger.info("Email triage sweep enqueued %d advisors", total)
        return {"mode": "sweep", "enqueued": total}

    # ------------------------------------------------------------------
    # Per-advisor triage
    # ------------------------------------------------------------------
    job_ctx = JobContext(**job_ctx_raw)
    started_at = time.monotonic()

    trace = langfuse.trace(
        name="email_triage",
        metadata={
            "tenant_id": job_ctx.tenant_id,
            "advisor_id": advisor_id,
        },
        tags=["job", "email_triage"],
    )

    try:
        scope = AccessScope(**job_ctx.access_scope)

        # Fetch new emails since last triage.
        last_triage_key = f"email_triage_cursor:{job_ctx.tenant_id}:{advisor_id}"
        last_cursor = await redis.get(last_triage_key)

        resp = await http_client.get(
            f"{settings.platform_base_url}/integrations/email/new",
            params={
                "advisor_id": advisor_id,
                "since_cursor": last_cursor or "",
                "limit": 200,
            },
            headers={
                "X-Tenant-ID": job_ctx.tenant_id,
                "X-Access-Scope": scope.to_header(),
            },
            timeout=30,
        )
        resp.raise_for_status()
        email_data = resp.json()
        raw_emails = email_data.get("emails", [])
        new_cursor = email_data.get("cursor")

        if not raw_emails:
            logger.info("No new emails for advisor %s", advisor_id)
            return {"advisor_id": advisor_id, "triaged": 0}

        emails = [IncomingEmail(**e) for e in raw_emails]

        # Fetch known client contacts for matching.
        clients = await platform.get_advisor_clients(advisor_id, scope)
        client_emails = {
            c.get("email", "").lower(): c.get("client_id")
            for c in clients
            if c.get("email")
        }

        # Process in batches.
        all_triaged: list[TriagedEmail] = []

        for batch_start in range(0, len(emails), EMAIL_BATCH_SIZE):
            batch = emails[batch_start : batch_start + EMAIL_BATCH_SIZE]

            # Build context for the agent.
            email_list = "\n\n".join(
                f"Email #{i + 1}:\n"
                f"  ID: {e.email_id}\n"
                f"  From: {e.from_address}\n"
                f"  Subject: {e.subject}\n"
                f"  Preview: {e.body_preview[:300]}\n"
                f"  Received: {e.received_at}\n"
                f"  Attachments: {e.has_attachments}\n"
                f"  Known client: {client_emails.get(e.from_address.lower(), 'unknown')}"
                for i, e in enumerate(batch)
            )

            prompt = (
                f"Advisor: {advisor_id}\n"
                f"Batch of {len(batch)} emails to triage:\n\n"
                f"{email_list}"
            )

            gen = trace.generation(name=f"triage_batch_{batch_start}")
            result = await triage_agent.run(prompt)
            triaged_batch = result.data
            gen.end(
                usage={"total_tokens": getattr(result, "token_usage", None)},
            )
            all_triaged.extend(triaged_batch)

        # Store triage results.
        triage_result = TriageBatchResult(
            advisor_id=advisor_id,
            triaged_at=datetime.now(timezone.utc).isoformat(),
            emails=all_triaged,
            urgent_count=sum(1 for t in all_triaged if t.priority == "urgent"),
            high_count=sum(1 for t in all_triaged if t.priority == "high"),
        )

        result_key = f"email_triage:{job_ctx.tenant_id}:{advisor_id}:latest"
        await redis.set(result_key, triage_result.model_dump_json(), ex=3600)

        # Update cursor so next run only fetches new emails.
        if new_cursor:
            await redis.set(last_triage_key, new_cursor, ex=604800)

        duration = time.monotonic() - started_at
        trace.update(
            output={
                "status": "success",
                "triaged": len(all_triaged),
                "urgent": triage_result.urgent_count,
                "high": triage_result.high_count,
            },
            metadata={"duration_seconds": duration},
        )
        logger.info(
            "Email triage for %s: %d emails, %d urgent, %d high (%.1fs)",
            advisor_id,
            len(all_triaged),
            triage_result.urgent_count,
            triage_result.high_count,
            duration,
        )
        return triage_result.model_dump()

    except Exception as exc:
        trace.update(
            metadata={
                "status": "error",
                "error": str(exc),
                "duration_seconds": time.monotonic() - started_at,
            },
            level="ERROR",
        )
        raise
```

---

## 8. Style Profile Refresh Job

Runs weekly per advisor. Fetches the advisor's last 100 sent emails, extracts writing style features, and stores a style profile in Redis for the email drafting agent.

```python
"""app/jobs/style_profile.py -- Advisor email style profile extraction."""

from __future__ import annotations

import logging
import time
from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent

from app.jobs.enqueue import JobContext
from app.services.platform_client import PlatformClient, AccessScope

logger = logging.getLogger("sidecar.jobs.style_profile")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class StyleProfile(BaseModel):
    advisor_id: str
    refreshed_at: str
    formality_level: str          # "formal", "semi_formal", "casual"
    avg_email_length_words: int
    greeting_patterns: list[str]  # "Dear [Name],", "Hi [Name],", etc.
    signoff_patterns: list[str]   # "Best regards,", "Thanks,", etc.
    common_phrases: list[str]     # recurring phrases unique to this advisor
    vocabulary_preferences: dict  # e.g. {"markets": "market conditions", "call": "reach out"}
    tone_descriptors: list[str]   # "warm", "professional", "concise", "detailed"
    paragraph_style: str          # "short_paragraphs", "long_form", "bullet_heavy"
    sample_count: int             # how many emails were analyzed


# ---------------------------------------------------------------------------
# Agent (Haiku-tier)
# ---------------------------------------------------------------------------

style_extractor = Agent(
    model="anthropic:claude-haiku-4-5",
    result_type=StyleProfile,
    system_prompt="""\
Analyze the sample of sent emails below and extract the advisor's
writing style profile. Focus on:

1. Formality level (formal/semi_formal/casual).
2. Average email length.
3. Common greeting and sign-off patterns.
4. Recurring phrases or expressions unique to this advisor.
5. Vocabulary preferences (how they refer to common concepts).
6. Overall tone descriptors.
7. Paragraph style (short vs long, bullets vs prose).

Be specific. Quote actual patterns from the emails.
Do not invent patterns not present in the data.
""",
)


# ---------------------------------------------------------------------------
# Job function
# ---------------------------------------------------------------------------

async def run_style_profile_refresh(
    ctx: dict[str, Any],
    job_ctx_raw: dict | None = None,
    advisor_id: str | None = None,
) -> dict:
    """Refresh an advisor's email style profile.

    When called by cron (no arguments), sweeps all advisors.
    When called with job_ctx_raw and advisor_id, refreshes one advisor.
    """
    platform: PlatformClient = ctx["platform_client"]
    redis = ctx["redis"]
    langfuse = ctx["langfuse"]
    http_client = ctx["http_client"]
    settings = ctx["settings"]

    # ------------------------------------------------------------------
    # Cron sweep mode
    # ------------------------------------------------------------------
    if job_ctx_raw is None:
        logger.info("Style profile refresh sweep started")
        from arq.connections import ArqRedis

        pool: ArqRedis = ctx["redis"]
        tenants = await platform.list_active_tenants()
        total = 0

        for tenant in tenants:
            advisors = await platform.list_advisors(tenant_id=tenant["tenant_id"])
            for adv in advisors:
                if not adv.get("email_integration_enabled", False):
                    continue
                adv_ctx = JobContext(
                    tenant_id=tenant["tenant_id"],
                    actor_id=adv["advisor_id"],
                    actor_type="system",
                    request_id=f"style-{adv['advisor_id']}-{int(time.time())}",
                    access_scope={
                        "visibility_mode": "advisor_scope",
                        "advisor_ids": [adv["advisor_id"]],
                    },
                )
                await pool.enqueue_job(
                    "run_style_profile_refresh",
                    adv_ctx.model_dump(),
                    adv["advisor_id"],
                    _job_id=f"style:{adv['advisor_id']}:weekly",
                )
                total += 1

        logger.info("Style profile sweep enqueued %d advisors", total)
        return {"mode": "sweep", "enqueued": total}

    # ------------------------------------------------------------------
    # Per-advisor refresh
    # ------------------------------------------------------------------
    job_ctx = JobContext(**job_ctx_raw)
    started_at = time.monotonic()
    from datetime import datetime, timezone

    trace = langfuse.trace(
        name="style_profile_refresh",
        metadata={
            "tenant_id": job_ctx.tenant_id,
            "advisor_id": advisor_id,
        },
        tags=["job", "style_profile"],
    )

    try:
        scope = AccessScope(**job_ctx.access_scope)

        # Fetch last 100 sent emails via email adapter.
        resp = await http_client.get(
            f"{settings.platform_base_url}/integrations/email/sent",
            params={"advisor_id": advisor_id, "limit": 100},
            headers={
                "X-Tenant-ID": job_ctx.tenant_id,
                "X-Access-Scope": scope.to_header(),
            },
            timeout=30,
        )
        resp.raise_for_status()
        sent_emails = resp.json().get("emails", [])

        if len(sent_emails) < 10:
            logger.info(
                "Insufficient sent emails for %s (%d), skipping style extraction",
                advisor_id,
                len(sent_emails),
            )
            return {"advisor_id": advisor_id, "status": "skipped", "reason": "insufficient_data"}

        # Build prompt with email samples.
        email_samples = "\n\n---\n\n".join(
            f"Email #{i + 1} (to: {e.get('to', '?')}, subject: {e.get('subject', '')}):\n"
            f"{e.get('body', '')[:2000]}"
            for i, e in enumerate(sent_emails[:100])
        )

        prompt = (
            f"Advisor: {advisor_id}\n"
            f"Number of sample emails: {len(sent_emails)}\n\n"
            f"--- SENT EMAIL SAMPLES ---\n\n{email_samples}"
        )

        gen = trace.generation(name="style_extraction")
        result = await style_extractor.run(prompt)
        profile: StyleProfile = result.data
        profile.advisor_id = advisor_id
        profile.refreshed_at = datetime.now(timezone.utc).isoformat()
        profile.sample_count = len(sent_emails)
        gen.end(
            output=profile.model_dump(),
            usage={"total_tokens": getattr(result, "token_usage", None)},
        )

        # Store with 14-day TTL (refreshed weekly, 2x buffer).
        profile_key = f"style_profile:{job_ctx.tenant_id}:{advisor_id}"
        await redis.set(profile_key, profile.model_dump_json(), ex=1209600)

        duration = time.monotonic() - started_at
        trace.update(
            output={"status": "success", "formality": profile.formality_level},
            metadata={"duration_seconds": duration},
        )
        logger.info(
            "Style profile refreshed for %s: %s tone, %d emails analyzed (%.1fs)",
            advisor_id,
            profile.formality_level,
            profile.sample_count,
            duration,
        )
        return profile.model_dump()

    except Exception as exc:
        trace.update(
            metadata={
                "status": "error",
                "error": str(exc),
                "duration_seconds": time.monotonic() - started_at,
            },
            level="ERROR",
        )
        raise
```

---

## 9. RAG Index Update Job

Triggered by platform events when new documents, emails, or CRM notes arrive. Fetches the content, chunks it, generates embeddings, and upserts into the vector store with full tenant-scoped metadata.

```python
"""app/jobs/rag_index.py -- RAG index update on content events."""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

import httpx
from pydantic import BaseModel

from app.jobs.enqueue import JobContext
from app.services.platform_client import PlatformClient, AccessScope

logger = logging.getLogger("sidecar.jobs.rag_index")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class ChunkMetadata(BaseModel):
    chunk_id: str
    tenant_id: str
    source_type: str     # "document", "email", "crm_note", "transcript", "activity"
    source_id: str
    household_id: str | None = None
    client_id: str | None = None
    account_id: str | None = None
    advisor_id: str | None = None
    visibility_tags: list[str] = []
    title: str | None = None
    created_at: str | None = None
    chunk_index: int = 0


class IndexedChunk(BaseModel):
    metadata: ChunkMetadata
    text: str
    embedding: list[float]


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

CHUNK_SIZE = 1000       # characters per chunk
CHUNK_OVERLAP = 200     # overlap for context continuity


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks.

    Uses paragraph-aware splitting: tries to break at paragraph
    boundaries within the chunk_size window. Falls back to
    sentence boundaries, then character boundaries.
    """
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))

        # If we're not at the end of the text, try to find a good break point.
        if end < len(text):
            # Try paragraph break.
            para_break = text.rfind("\n\n", start + chunk_size // 2, end)
            if para_break != -1:
                end = para_break + 2
            else:
                # Try sentence break.
                for sep in (". ", ".\n", "! ", "? "):
                    sent_break = text.rfind(sep, start + chunk_size // 2, end)
                    if sent_break != -1:
                        end = sent_break + len(sep)
                        break

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        # Advance with overlap.
        start = end - overlap if end < len(text) else end

    return chunks


def make_chunk_id(tenant_id: str, source_id: str, chunk_index: int) -> str:
    """Deterministic chunk ID for idempotent upserts."""
    raw = f"{tenant_id}:{source_id}:{chunk_index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

async def generate_embeddings(
    http_client: httpx.AsyncClient,
    texts: list[str],
    settings: Any,
) -> list[list[float]]:
    """Generate embeddings via OpenAI text-embedding-3-small."""
    resp = await http_client.post(
        "https://api.openai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        json={
            "model": "text-embedding-3-small",
            "input": texts,
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return [item["embedding"] for item in data["data"]]


# ---------------------------------------------------------------------------
# Vector store upsert
# ---------------------------------------------------------------------------

async def upsert_chunks(
    http_client: httpx.AsyncClient,
    chunks: list[IndexedChunk],
    settings: Any,
) -> int:
    """Upsert chunks into the vector store.

    Supports pgvector via platform API or a dedicated vector store.
    """
    payload = [
        {
            "id": c.metadata.chunk_id,
            "values": c.embedding,
            "metadata": c.metadata.model_dump(),
            "text": c.text,
        }
        for c in chunks
    ]

    resp = await http_client.post(
        f"{settings.vector_store_url}/upsert",
        json={"vectors": payload},
        timeout=60,
    )
    resp.raise_for_status()
    return len(payload)


async def delete_source_chunks(
    http_client: httpx.AsyncClient,
    tenant_id: str,
    source_id: str,
    settings: Any,
) -> None:
    """Delete all existing chunks for a source (before re-indexing)."""
    await http_client.post(
        f"{settings.vector_store_url}/delete",
        json={
            "filter": {
                "tenant_id": tenant_id,
                "source_id": source_id,
            },
        },
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Content fetchers
# ---------------------------------------------------------------------------

async def _fetch_document_content(
    platform: PlatformClient,
    http_client: httpx.AsyncClient,
    source_id: str,
    scope: AccessScope,
    settings: Any,
) -> tuple[str, dict]:
    """Fetch document text and metadata."""
    meta = await platform.get_document_metadata(source_id, scope)

    # Download document content from object storage.
    resp = await http_client.get(
        f"{settings.platform_base_url}/storage/objects/{meta.get('object_key', '')}",
        headers={"X-Tenant-ID": scope.tenant_id},
        timeout=60,
    )
    resp.raise_for_status()

    # Parse based on content type (PDF, text, etc.).
    content_type = meta.get("content_type", "")
    if "pdf" in content_type:
        import pdfplumber

        pdf = pdfplumber.open(io.BytesIO(resp.content))
        text = "\n\n".join(page.extract_text() or "" for page in pdf.pages)
        pdf.close()
    else:
        text = resp.text

    return text, meta


async def _fetch_email_content(
    http_client: httpx.AsyncClient,
    source_id: str,
    scope: AccessScope,
    settings: Any,
) -> tuple[str, dict]:
    """Fetch email body and metadata."""
    resp = await http_client.get(
        f"{settings.platform_base_url}/integrations/email/{source_id}",
        headers={
            "X-Tenant-ID": scope.tenant_id,
            "X-Access-Scope": scope.to_header(),
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    text = f"Subject: {data.get('subject', '')}\nFrom: {data.get('from', '')}\n\n{data.get('body', '')}"
    return text, data


async def _fetch_crm_note_content(
    http_client: httpx.AsyncClient,
    source_id: str,
    scope: AccessScope,
    settings: Any,
) -> tuple[str, dict]:
    """Fetch CRM note content."""
    resp = await http_client.get(
        f"{settings.platform_base_url}/integrations/crm/notes/{source_id}",
        headers={
            "X-Tenant-ID": scope.tenant_id,
            "X-Access-Scope": scope.to_header(),
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    text = f"{data.get('title', '')}\n\n{data.get('body', '')}"
    return text, data


# ---------------------------------------------------------------------------
# Job function
# ---------------------------------------------------------------------------

import io  # needed for PDF parsing above

EMBEDDING_BATCH_SIZE = 50  # max texts per embedding API call


async def run_rag_index_update(
    ctx: dict[str, Any],
    job_ctx_raw: dict,
    source_type: str,
    source_id: str,
    event_type: str,
) -> dict:
    """Update the RAG index for a content event.

    Handles three event types:
    - created: fetch content, chunk, embed, upsert.
    - updated: delete old chunks, re-index.
    - deleted: delete all chunks for this source.
    """
    job_ctx = JobContext(**job_ctx_raw)
    started_at = time.monotonic()
    langfuse = ctx["langfuse"]
    redis = ctx["redis"]
    platform: PlatformClient = ctx["platform_client"]
    http_client: httpx.AsyncClient = ctx["http_client"]
    settings = ctx["settings"]

    trace = langfuse.trace(
        name="rag_index_update",
        metadata={
            "tenant_id": job_ctx.tenant_id,
            "source_type": source_type,
            "source_id": source_id,
            "event_type": event_type,
        },
        tags=["job", "rag_index"],
    )

    try:
        scope = AccessScope(**job_ctx.access_scope)

        # Handle deletion.
        if event_type == "deleted":
            await delete_source_chunks(
                http_client, job_ctx.tenant_id, source_id, settings,
            )
            trace.update(output={"status": "deleted"})
            return {"status": "deleted", "source_id": source_id}

        # For created/updated, delete old chunks first (idempotent).
        if event_type == "updated":
            await delete_source_chunks(
                http_client, job_ctx.tenant_id, source_id, settings,
            )

        # Fetch content based on source type.
        fetchers = {
            "document": lambda: _fetch_document_content(
                platform, http_client, source_id, scope, settings,
            ),
            "email": lambda: _fetch_email_content(
                http_client, source_id, scope, settings,
            ),
            "crm_note": lambda: _fetch_crm_note_content(
                http_client, source_id, scope, settings,
            ),
        }

        fetcher = fetchers.get(source_type)
        if fetcher is None:
            raise ValueError(f"Unsupported source type: {source_type}")

        text, meta = await fetcher()

        if not text or not text.strip():
            logger.warning("Empty content for %s:%s, skipping", source_type, source_id)
            return {"status": "skipped", "reason": "empty_content"}

        # Chunk the text.
        text_chunks = chunk_text(text)

        # Generate embeddings in batches.
        all_embeddings: list[list[float]] = []
        for batch_start in range(0, len(text_chunks), EMBEDDING_BATCH_SIZE):
            batch = text_chunks[batch_start : batch_start + EMBEDDING_BATCH_SIZE]
            embeddings = await generate_embeddings(http_client, batch, settings)
            all_embeddings.extend(embeddings)

        # Build indexed chunks with full metadata.
        indexed_chunks = []
        for i, (chunk_text_str, embedding) in enumerate(zip(text_chunks, all_embeddings)):
            chunk_meta = ChunkMetadata(
                chunk_id=make_chunk_id(job_ctx.tenant_id, source_id, i),
                tenant_id=job_ctx.tenant_id,
                source_type=source_type,
                source_id=source_id,
                household_id=meta.get("household_id"),
                client_id=meta.get("client_id"),
                account_id=meta.get("account_id"),
                advisor_id=meta.get("advisor_id"),
                visibility_tags=meta.get("visibility_tags", []),
                title=meta.get("title") or meta.get("subject"),
                created_at=meta.get("created_at"),
                chunk_index=i,
            )
            indexed_chunks.append(IndexedChunk(
                metadata=chunk_meta,
                text=chunk_text_str,
                embedding=embedding,
            ))

        # Upsert into vector store.
        upserted = await upsert_chunks(http_client, indexed_chunks, settings)

        duration = time.monotonic() - started_at
        trace.update(
            output={
                "status": "indexed",
                "chunks": upserted,
                "text_length": len(text),
            },
            metadata={"duration_seconds": duration},
        )
        logger.info(
            "RAG index updated: %s:%s -> %d chunks (%.1fs)",
            source_type,
            source_id,
            upserted,
            duration,
        )
        return {
            "status": "indexed",
            "source_type": source_type,
            "source_id": source_id,
            "chunks": upserted,
        }

    except Exception as exc:
        trace.update(
            metadata={
                "status": "error",
                "error": str(exc),
                "duration_seconds": time.monotonic() - started_at,
            },
            level="ERROR",
        )
        raise
```

---

## 10. Job Retry and Error Handling

### 10.1 Failure classification

All job failures fall into four categories. Classification determines retry behavior and alerting.

```python
"""app/jobs/errors.py -- Job failure classification and retry policy."""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

import httpx

logger = logging.getLogger("sidecar.jobs.errors")


class FailureCategory(str, Enum):
    """Failure categories that determine retry behavior."""
    PLATFORM_READ = "platform_read"      # platform API returned error
    MODEL_PROVIDER = "model_provider"    # LLM/transcription API error
    VALIDATION = "validation"            # input/output validation failed
    INTERNAL = "internal"                # unexpected bug in job code


def classify_error(exc: Exception) -> FailureCategory:
    """Classify an exception into a failure category."""
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code >= 500:
            return FailureCategory.PLATFORM_READ
        if exc.response.status_code == 429:
            return FailureCategory.MODEL_PROVIDER
        if exc.response.status_code in (401, 403):
            return FailureCategory.PLATFORM_READ
        return FailureCategory.VALIDATION

    if isinstance(exc, httpx.ConnectError | httpx.TimeoutException):
        return FailureCategory.PLATFORM_READ

    if isinstance(exc, ValueError | TypeError):
        return FailureCategory.VALIDATION

    # Model provider errors (Pydantic AI wraps these).
    error_name = type(exc).__name__
    if "model" in error_name.lower() or "provider" in error_name.lower():
        return FailureCategory.MODEL_PROVIDER
    if "rate" in str(exc).lower() or "quota" in str(exc).lower():
        return FailureCategory.MODEL_PROVIDER

    return FailureCategory.INTERNAL


# ---------------------------------------------------------------------------
# Retry policy per category
# ---------------------------------------------------------------------------

RETRY_POLICY: dict[FailureCategory, dict[str, Any]] = {
    FailureCategory.PLATFORM_READ: {
        "max_retries": 3,
        "base_delay_seconds": 5,
        "backoff_factor": 2,       # 5s, 10s, 20s
        "retry": True,
    },
    FailureCategory.MODEL_PROVIDER: {
        "max_retries": 3,
        "base_delay_seconds": 10,
        "backoff_factor": 3,       # 10s, 30s, 90s
        "retry": True,
    },
    FailureCategory.VALIDATION: {
        "max_retries": 0,
        "base_delay_seconds": 0,
        "backoff_factor": 1,
        "retry": False,            # validation errors won't self-heal
    },
    FailureCategory.INTERNAL: {
        "max_retries": 1,
        "base_delay_seconds": 30,
        "backoff_factor": 1,
        "retry": True,             # one retry for transient internal errors
    },
}


def compute_retry_delay(category: FailureCategory, attempt: int) -> float | None:
    """Compute the retry delay for a given failure category and attempt.

    Returns None if the job should not be retried.
    """
    policy = RETRY_POLICY[category]
    if not policy["retry"] or attempt >= policy["max_retries"]:
        return None
    return policy["base_delay_seconds"] * (policy["backoff_factor"] ** attempt)
```

### 10.2 Retry wrapper

ARQ provides built-in retry via `ctx["job_try"]`. The wrapper below integrates classification-based retry decisions.

```python
"""app/jobs/retry.py -- Retry-aware job wrapper."""

from __future__ import annotations

import logging
from datetime import timedelta
from functools import wraps
from typing import Any, Callable, Coroutine

from arq.jobs import Retry

from app.jobs.errors import classify_error, compute_retry_delay, FailureCategory

logger = logging.getLogger("sidecar.jobs.retry")


def with_retry_policy(
    fn: Callable[..., Coroutine[Any, Any, dict]],
) -> Callable[..., Coroutine[Any, Any, dict]]:
    """Decorator that wraps a job function with classification-based retry.

    On failure:
    1. Classify the error.
    2. Compute retry delay based on category and attempt number.
    3. If retryable, raise arq.jobs.Retry with the computed delay.
    4. If not retryable, record to dead-letter and re-raise.
    """

    @wraps(fn)
    async def wrapper(ctx: dict[str, Any], *args: Any, **kwargs: Any) -> dict:
        attempt = ctx.get("job_try", 1) - 1  # ARQ starts at 1
        job_id = ctx.get("job_id", "unknown")

        try:
            return await fn(ctx, *args, **kwargs)
        except Retry:
            raise  # explicit retry from within the job
        except Exception as exc:
            category = classify_error(exc)
            delay = compute_retry_delay(category, attempt)

            logger.warning(
                "Job %s failed (attempt %d, category=%s): %s",
                job_id,
                attempt + 1,
                category.value,
                exc,
            )

            if delay is not None:
                logger.info(
                    "Retrying job %s in %.0fs (attempt %d)",
                    job_id,
                    delay,
                    attempt + 2,
                )
                raise Retry(defer=timedelta(seconds=delay)) from exc

            # Dead letter: store failure record for investigation.
            await _dead_letter(ctx, job_id, fn.__name__, args, category, exc, attempt)
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
    """Record a permanently failed job in the dead-letter set.

    Dead-letter entries are stored in a Redis sorted set keyed by
    timestamp, with a 30-day TTL. Ops teams can inspect and manually
    retry or dismiss these.
    """
    import json
    import time

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
        # Trim to last 1000 entries.
        await redis.zremrangebyrank("sidecar:dead_letter", 0, -1001)
        logger.error(
            "Job %s (%s) moved to dead letter after %d attempts: %s",
            job_id,
            job_name,
            attempts + 1,
            exc,
        )
    except Exception as dl_exc:
        logger.error("Failed to record dead letter for %s: %s", job_id, dl_exc)
```

### 10.3 Applying the retry wrapper

The retry wrapper is applied to each job function in the worker registration:

```python
# In app/jobs/worker.py, update the functions list:

from app.jobs.retry import with_retry_policy

class WorkerSettings:
    functions = [
        with_retry_policy(run_daily_digest),
        with_retry_policy(run_email_triage),
        with_retry_policy(run_transcription),
        with_retry_policy(run_meeting_summary),
        with_retry_policy(run_firm_report),
        with_retry_policy(run_style_profile_refresh),
        with_retry_policy(run_rag_index_update),
    ]
    # ... rest of config
```

### 10.4 Retry behavior summary

| Failure Category | Max Retries | Backoff | Dead Letter |
|-----------------|-------------|---------|-------------|
| `platform_read` | 3 | 5s, 10s, 20s | Yes, after exhaustion |
| `model_provider` | 3 | 10s, 30s, 90s | Yes, after exhaustion |
| `validation` | 0 | N/A | Yes, immediately |
| `internal` | 1 | 30s | Yes, after exhaustion |

---

## 11. Job Observability

### 11.1 Langfuse integration

Every job creates a Langfuse trace at entry and updates it at exit. This gives visibility into duration, token usage, failure rate, and cost per job type, per tenant, per advisor.

```python
"""app/jobs/observability.py -- Job-level Langfuse telemetry."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from langfuse import Langfuse

logger = logging.getLogger("sidecar.jobs.observability")


@dataclass
class JobMetrics:
    """Accumulated metrics for a single job execution."""
    job_name: str
    tenant_id: str
    actor_id: str
    started_at: float = field(default_factory=time.monotonic)
    ended_at: float | None = None
    status: str = "running"
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_calls: int = 0
    platform_reads: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    error: str | None = None
    error_category: str | None = None

    @property
    def duration_seconds(self) -> float:
        end = self.ended_at or time.monotonic()
        return end - self.started_at


class JobTracer:
    """Wraps Langfuse trace management for a single job execution.

    Usage:
        tracer = JobTracer(langfuse, "daily_digest", job_ctx)
        gen = tracer.start_generation("digest_agent", model="haiku")
        # ... run agent ...
        tracer.end_generation(gen, token_usage={...})
        tracer.complete(output={...})
    """

    def __init__(
        self,
        langfuse: Langfuse,
        job_name: str,
        tenant_id: str,
        actor_id: str,
        extra_metadata: dict | None = None,
    ):
        self.metrics = JobMetrics(
            job_name=job_name,
            tenant_id=tenant_id,
            actor_id=actor_id,
        )
        self.trace = langfuse.trace(
            name=job_name,
            metadata={
                "tenant_id": tenant_id,
                "actor_id": actor_id,
                **(extra_metadata or {}),
            },
            tags=["job", job_name],
        )

    def start_generation(
        self,
        name: str,
        model: str | None = None,
        input_data: Any = None,
    ):
        """Start a tracked model generation within this job."""
        self.metrics.model_calls += 1
        return self.trace.generation(
            name=name,
            model=model,
            input=input_data,
        )

    def end_generation(
        self,
        generation: Any,
        output: Any = None,
        token_usage: dict | None = None,
    ) -> None:
        """End a tracked generation and accumulate token counts."""
        if token_usage:
            self.metrics.prompt_tokens += token_usage.get("prompt_tokens", 0)
            self.metrics.completion_tokens += token_usage.get("completion_tokens", 0)
            self.metrics.total_tokens += token_usage.get("total_tokens", 0)
        generation.end(output=output, usage=token_usage)

    def record_platform_read(self) -> None:
        self.metrics.platform_reads += 1

    def record_cache_hit(self) -> None:
        self.metrics.cache_hits += 1

    def record_cache_miss(self) -> None:
        self.metrics.cache_misses += 1

    def start_span(self, name: str, **kwargs) -> Any:
        """Start a named span within this job trace."""
        return self.trace.span(name=name, **kwargs)

    def complete(self, output: Any = None) -> None:
        """Mark the job as successfully completed."""
        self.metrics.ended_at = time.monotonic()
        self.metrics.status = "success"
        self.trace.update(
            output=output,
            metadata=self._final_metadata(),
        )

    def fail(self, error: Exception, category: str | None = None) -> None:
        """Mark the job as failed."""
        self.metrics.ended_at = time.monotonic()
        self.metrics.status = "error"
        self.metrics.error = str(error)
        self.metrics.error_category = category
        self.trace.update(
            metadata=self._final_metadata(),
            level="ERROR",
        )

    def _final_metadata(self) -> dict:
        return {
            "duration_seconds": self.metrics.duration_seconds,
            "status": self.metrics.status,
            "total_tokens": self.metrics.total_tokens,
            "prompt_tokens": self.metrics.prompt_tokens,
            "completion_tokens": self.metrics.completion_tokens,
            "model_calls": self.metrics.model_calls,
            "platform_reads": self.metrics.platform_reads,
            "cache_hits": self.metrics.cache_hits,
            "cache_misses": self.metrics.cache_misses,
            "error": self.metrics.error,
            "error_category": self.metrics.error_category,
        }
```

### 11.2 Metrics dashboard queries

With the Langfuse trace structure above, the following queries are available:

| Metric | Langfuse Query |
|--------|---------------|
| Job duration P50/P95/P99 | Filter by tag `job`, group by `name`, aggregate `duration_seconds` |
| Token usage per job type | Filter by tag `job`, group by `name`, sum `total_tokens` |
| Token usage per tenant | Filter by tag `job`, group by `metadata.tenant_id`, sum `total_tokens` |
| Failure rate per job type | Filter by tag `job`, group by `name`, ratio of `status=error` / total |
| Failure rate by category | Filter by `level=ERROR`, group by `metadata.error_category` |
| Cost per advisor per day | Filter by tag `job`, group by `metadata.actor_id` + date, sum token cost |
| Model call count per job | Filter by tag `job`, group by `name`, avg `metadata.model_calls` |
| Platform read latency | Filter spans with name pattern `platform_*`, aggregate duration |

### 11.3 Alerting thresholds

Recommended alerting based on job observability:

| Condition | Alert Level | Action |
|-----------|-------------|--------|
| Dead letter queue depth > 10 | Warning | Investigate failure pattern |
| Dead letter queue depth > 50 | Critical | Likely systemic failure |
| Job P95 duration > 2x normal | Warning | Check provider latency |
| Failure rate > 10% for any job type (1h window) | Warning | Check provider status |
| Failure rate > 30% for any job type (1h window) | Critical | Likely outage |
| Daily digest completion < 90% of advisors | Warning | Check sweep/fan-out |
| Worker health check missing | Critical | Worker process down |

### 11.4 Health check endpoint

The worker exposes its health via a Redis key that the API process can read:

```python
# In the API health router:

@router.get("/health/worker")
async def worker_health(redis=Depends(get_redis)):
    """Check if the ARQ worker is alive based on its health check key."""
    last_heartbeat = await redis.get("sidecar:worker:health")
    if last_heartbeat is None:
        return {"status": "unhealthy", "reason": "no_heartbeat"}

    import time
    age = time.time() - float(last_heartbeat)
    if age > 60:  # no heartbeat in 60s
        return {"status": "unhealthy", "reason": "stale_heartbeat", "age_seconds": age}

    return {"status": "healthy", "last_heartbeat_age_seconds": age}
```

---

## Appendix A: Job Summary Table

| Job | Module | Trigger | Schedule | Agent Tier | Timeout | Max Retries |
|-----|--------|---------|----------|------------|---------|-------------|
| Daily Digest | `daily_digest.py` | Cron + fan-out | Daily 05:55 UTC | Haiku | 600s | 3 |
| Email Triage | `email_triage.py` | Cron + fan-out | Every 15 min | Haiku | 300s | 3 |
| Transcription | `transcription.py` | Event (audio upload) | On demand | Whisper/Deepgram | 2x duration | 3 |
| Meeting Summary | `meeting_summary.py` | Chained (post-transcription) | On demand | Sonnet | 600s | 3 |
| Firm-Wide Report | `firm_report.py` | API request (202) | On demand | Haiku + Opus | 1800s | 3 |
| Style Profile | `style_profile.py` | Cron + fan-out | Weekly Sunday 02:00 UTC | Haiku | 900s | 3 |
| RAG Index Update | `rag_index.py` | Event (content change) | On event | Embedding model | 600s | 3 |

## Appendix B: Redis Key Namespace

All job-related keys follow the pattern `{purpose}:{tenant_id}:{entity}:{qualifier}`.

| Key Pattern | TTL | Purpose |
|-------------|-----|---------|
| `digest:{tenant_id}:{advisor_id}:{date}` | 24h | Cached daily digest |
| `transcript:{tenant_id}:{meeting_id}` | 48h | Raw transcription result |
| `meeting_summary:{tenant_id}:{meeting_id}` | 72h | Meeting summary |
| `email_triage:{tenant_id}:{advisor_id}:latest` | 1h | Latest triage results |
| `email_triage_cursor:{tenant_id}:{advisor_id}` | 7d | Email sync cursor |
| `style_profile:{tenant_id}:{advisor_id}` | 14d | Advisor writing style profile |
| `firm_report:{tenant_id}:{type}:{timestamp}` | 7d | Firm-wide report artifact |
| `firm_report_latest:{tenant_id}:{type}` | 7d | Pointer to latest report |
| `sidecar:dead_letter` | None | Dead-letter sorted set (trimmed to 1000) |
| `sidecar:worker:health` | Auto | Worker heartbeat timestamp |

## Appendix C: File Layout

```
app/jobs/
  worker.py              # ARQ WorkerSettings, startup/shutdown, cron registration
  enqueue.py             # JobContext model, enqueue helpers for API process
  errors.py              # FailureCategory enum, classify_error, retry policy table
  retry.py               # with_retry_policy decorator, dead-letter recording
  observability.py       # JobTracer, JobMetrics for Langfuse integration
  daily_digest.py        # Daily digest sweep + per-advisor generation
  email_triage.py        # Email triage sweep + per-advisor triage
  transcription.py       # Audio transcription with chunked processing
  meeting_summary.py     # Post-transcription summarization
  firm_report.py         # Firm-wide analytical report generation
  style_profile.py       # Advisor email style profile extraction
  rag_index.py           # RAG index update on content events
```
