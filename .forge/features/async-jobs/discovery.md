# Discovery: Async Jobs

## Requirements

1. **Worker entry point** (`worker.py`) — Replace existing stub with full ARQ WorkerSettings: startup/shutdown lifecycle, cron schedules (daily digest 05:55 UTC, email triage every 15min, style profile weekly Sunday 02:00), health check key, concurrency config.
2. **Job enqueue helpers** (`enqueue.py`) — JobContext Pydantic model for tenant/actor/scope propagation. Enqueue functions: enqueue_transcription, enqueue_meeting_summary, enqueue_firm_report, enqueue_rag_index_update. Lazy ArqRedis pool.
3. **Job error classification** (`errors.py`) — FailureCategory enum (platform_read, model_provider, validation, internal). classify_error() function. Retry policy table with per-category max_retries, backoff.
4. **Retry wrapper** (`retry.py`) — with_retry_policy decorator using ARQ's Retry mechanism. Dead-letter recording to Redis sorted set.
5. **Observability** (`observability.py`) — JobTracer wrapping Langfuse traces. JobMetrics dataclass tracking tokens, duration, platform reads, cache hits.
6. **Daily digest job** (`daily_digest.py`) — Cron sweep mode (fan-out per advisor) + per-advisor generation using digest agent (Haiku tier). Concurrent data gathering with error tolerance.
7. **Email triage job** (`email_triage.py`) — Cron sweep + per-advisor triage using triage agent (Haiku). Batch processing, cursor-based sync.
8. **Transcription job** (`transcription.py`) — Audio download, chunked processing (20min segments with 30s overlap), Whisper/Deepgram provider selection, WAV header construction, chained meeting summary trigger.
9. **Meeting summary job** (`meeting_summary.py`) — Load transcript from Redis, truncate for context window (80K tokens, 60/40 head/tail split), run summary agent (Sonnet tier), store + webhook notify.
10. **Firm report job** (`firm_report.py`) — Fetch all accounts, per-account analysis (Haiku), aggregate into firm report (Opus), batch processing.
11. **Style profile job** (`style_profile.py`) — Fetch sent emails, extract writing style via agent (Haiku), store with 14d TTL.
12. **RAG index update job** (`rag_index.py`) — Content event handler (created/updated/deleted), source-type fetchers, character-based chunking, embedding generation, vector store upsert.
13. **Worker health endpoint** — Add GET /health/worker to health router checking Redis heartbeat key.
14. **Worker dependencies** — build_worker_dependencies() in dependencies.py for worker startup.

## Decisions Already Made

- ARQ as job queue backed by Redis
- Separate worker process from FastAPI API
- JobContext model for tenant isolation in every job
- Langfuse for job observability
- Dead-letter set in Redis for permanently failed jobs
- Classification-based retry: platform_read (3x, 5s backoff), model_provider (3x, 10s backoff), validation (0x), internal (1x, 30s)
- Cron sweep + fan-out pattern for per-advisor jobs

## Constraints

- Existing worker.py uses redis_url (not redis_host/port/password/db separately) — spec's get_redis_settings() must adapt
- Existing worker.py already registers 5 stub functions + 2 real jobs (indexing_jobs, gc_jobs) — must preserve real jobs
- Spec uses `result_type` (old pydantic-ai API) — must use `output_type` instead
- Spec uses `fallback_model` on Agent() — doesn't exist in pydantic-ai v1.73, must use `defer_model_check=True`
- Spec references PlatformClient methods that don't exist: list_active_tenants(), list_advisors(), get_meeting_metadata()
- Spec references AccessScope.to_header() which doesn't exist
- Existing indexing_jobs.py and gc_jobs.py are real implementations from spec 03 — must not be overwritten
- Config doesn't have redis_host/port/password/job_db or platform_base_url fields

## Open Questions

- [ ] The spec references PlatformClient.list_active_tenants() and PlatformClient.list_advisors() which don't exist and weren't in the 04-platform-client spec. The cron sweep jobs need these to fan out per-advisor. Should we add stub methods for these, or should the sweep jobs receive explicit tenant/advisor lists as arguments?
- [ ] The spec defines its own result types (DailyDigest, MeetingSummary, etc.) in each job file, but schemas.py already has similar models from spec 02. Should job files use the existing schemas.py models or define their own job-specific types?
- [ ] The spec uses Langfuse directly (`from langfuse import Langfuse`). Should we use real Langfuse (requires API keys in tests) or create a mock/no-op Langfuse client for testing?
