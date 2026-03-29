# Architecture: Async Jobs

## Approach A: Spec-Faithful with Adaptations (Recommended)

Implement the full spec adapting to the existing codebase: use existing schemas.py models, adapt pydantic-ai API, use redis_url instead of separate redis fields, preserve existing indexing_jobs.py and gc_jobs.py.

**Files created (8):**
1. `src/app/jobs/enqueue.py` — JobContext model + enqueue helpers
2. `src/app/jobs/errors.py` — FailureCategory, classify_error, retry policy table
3. `src/app/jobs/retry.py` — with_retry_policy decorator + dead-letter
4. `src/app/jobs/observability.py` — JobTracer + JobMetrics
5. `src/app/jobs/meeting_summary.py` — Post-transcription summary job
6. `src/app/jobs/rag_index.py` — RAG index update on content events

**Files replaced (5 stubs):**
7. `src/app/jobs/daily_digest.py` — Full implementation
8. `src/app/jobs/email_triage.py` — Full implementation
9. `src/app/jobs/transcription.py` — Full implementation
10. `src/app/jobs/firm_report.py` — Full implementation
11. `src/app/jobs/style_profile.py` — Full implementation

**Files modified (4):**
12. `src/app/jobs/worker.py` — Full rewrite with startup/shutdown/cron/retry
13. `src/app/services/platform_client.py` — Add 3 methods
14. `src/app/routers/health.py` — Add worker health endpoint
15. `src/app/dependencies.py` — Add build_worker_dependencies()

**Trade-offs:**
- (+) Complete spec coverage
- (+) Reuses existing models and patterns
- (-) 15 files touched, but most are stub replacements

## Recommendation

**Approach A** — only one viable approach since the spec is comprehensive and the user wants full implementation.

## Task Breakdown (recommended approach)

| Order | File | Action | Depends On |
|---|---|---|---|
| 1 | `jobs/enqueue.py` | Create: JobContext + enqueue helpers | — |
| 2 | `jobs/errors.py` | Create: FailureCategory + classify_error | — |
| 3 | `jobs/observability.py` | Create: JobTracer + JobMetrics | — |
| 4 | `jobs/retry.py` | Create: with_retry_policy + dead-letter | errors.py |
| 5 | `services/platform_client.py` | Add 3 methods | — |
| 6 | `jobs/transcription.py` | Replace stub | enqueue.py |
| 7 | `jobs/meeting_summary.py` | Create | enqueue.py |
| 8 | `jobs/daily_digest.py` | Replace stub | enqueue.py |
| 9 | `jobs/email_triage.py` | Replace stub | enqueue.py |
| 10 | `jobs/firm_report.py` | Replace stub | enqueue.py |
| 11 | `jobs/style_profile.py` | Replace stub | enqueue.py |
| 12 | `jobs/rag_index.py` | Create | enqueue.py |
| 13 | `jobs/worker.py` | Rewrite | all jobs, retry.py |
| 14 | `dependencies.py` | Add build_worker_dependencies | — |
| 15 | `routers/health.py` | Add worker health | — |
