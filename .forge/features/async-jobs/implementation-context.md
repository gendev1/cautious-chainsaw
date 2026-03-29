# Implementation Context: Async Jobs

## Chosen Approach

Approach A — Full spec implementation adapted to existing codebase.

## Implementation Order

### Step 1: Job Infrastructure (enqueue, errors, observability, retry)
- `jobs/enqueue.py` — JobContext model, lazy ArqRedis pool, enqueue_* helpers
- `jobs/errors.py` — FailureCategory enum, classify_error(), RETRY_POLICY table, compute_retry_delay()
- `jobs/observability.py` — JobMetrics dataclass, JobTracer class wrapping Langfuse
- `jobs/retry.py` — with_retry_policy decorator, _dead_letter() helper

### Step 2: PlatformClient additions
- Add list_active_tenants(), list_advisors(), get_meeting_metadata() to platform_client.py

### Step 3: Job implementations (replace stubs + new files)
- `jobs/transcription.py` — Full audio transcription with chunking
- `jobs/meeting_summary.py` — Post-transcription summarization
- `jobs/daily_digest.py` — Cron sweep + per-advisor digest
- `jobs/email_triage.py` — Cron sweep + per-advisor triage
- `jobs/firm_report.py` — Firm-wide report with per-account analysis
- `jobs/style_profile.py` — Style profile extraction
- `jobs/rag_index.py` — RAG index content event handler

### Step 4: Worker + dependencies + health
- `jobs/worker.py` — Full rewrite with startup/shutdown, cron jobs, retry wrapper
- `dependencies.py` — Add build_worker_dependencies()
- `routers/health.py` — Add GET /health/worker endpoint

## External Dependencies

| Dependency | Already Installed | Notes |
|---|---|---|
| arq | Yes | Already used in worker.py |
| langfuse | Yes | Already in pyproject.toml |
| httpx | Yes | Already used everywhere |
| pydantic-ai | Yes | For agent definitions in jobs |

No new dependencies needed.

## Test Cases

### Job errors (test_job_errors.py)
- classify_error maps httpx.HTTPStatusError 500 to PLATFORM_READ
- classify_error maps httpx.TimeoutException to PLATFORM_READ
- classify_error maps ValueError to VALIDATION
- compute_retry_delay returns None for validation category
- compute_retry_delay computes exponential backoff for platform_read

### Retry wrapper (test_job_retry.py)
- with_retry_policy passes through on success
- with_retry_policy raises Retry on retryable error
- with_retry_policy records dead letter on non-retryable error

### Observability (test_observability.py)
- JobMetrics tracks duration
- JobTracer accumulates token counts

### Enqueue (test_enqueue.py)
- JobContext serializes/deserializes
- enqueue helpers call pool.enqueue_job with correct args

### Transcription (test_transcription.py)
- _chunk_audio_bytes returns single chunk for short audio
- _chunk_audio_bytes returns multiple chunks for long audio
- _make_wav_header produces valid 44-byte header
- _truncate_transcript (from meeting_summary) preserves short text
- _truncate_transcript truncates long text with head/tail split

### RAG index (test_rag_index.py)
- chunk_text returns single chunk for short text
- chunk_text splits at paragraph boundaries
- make_chunk_id is deterministic

## Scope Boundaries

### In scope
- `src/app/jobs/enqueue.py` (new)
- `src/app/jobs/errors.py` (new)
- `src/app/jobs/observability.py` (new)
- `src/app/jobs/retry.py` (new)
- `src/app/jobs/daily_digest.py` (replace)
- `src/app/jobs/email_triage.py` (replace)
- `src/app/jobs/transcription.py` (replace)
- `src/app/jobs/firm_report.py` (replace)
- `src/app/jobs/style_profile.py` (replace)
- `src/app/jobs/meeting_summary.py` (new)
- `src/app/jobs/rag_index.py` (new)
- `src/app/jobs/worker.py` (rewrite)
- `src/app/services/platform_client.py` (modify — add 3 methods)
- `src/app/dependencies.py` (modify — add build_worker_dependencies)
- `src/app/routers/health.py` (modify — add worker health)

### Out of scope
- `src/app/jobs/indexing_jobs.py` — existing RAG pipeline job, not modified
- `src/app/jobs/gc_jobs.py` — existing GC job, not modified
- Agent implementations — not modified
- Existing schemas.py — not modified (reuse existing models)
