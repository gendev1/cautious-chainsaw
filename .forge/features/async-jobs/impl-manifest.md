# Implementation Manifest: Async Jobs

## Files Created

| File | Purpose |
|---|---|
| `src/app/jobs/enqueue.py` | JobContext model, lazy ArqRedis pool, 4 enqueue helpers |
| `src/app/jobs/errors.py` | FailureCategory enum, classify_error(), RETRY_POLICY, compute_retry_delay() |
| `src/app/jobs/observability.py` | JobMetrics dataclass, JobTracer wrapping Langfuse v4 OTEL spans |
| `src/app/jobs/retry.py` | with_retry_policy decorator, dead-letter recording |
| `src/app/jobs/meeting_summary.py` | Post-transcription summary with _truncate_transcript, MeetingSummary agent |
| `src/app/jobs/rag_index.py` | RAG index update: chunk_text, make_chunk_id, content fetchers, vector upsert |

## Files Modified

| File | Change |
|---|---|
| `src/app/jobs/daily_digest.py` | Replaced stub with full cron sweep + per-advisor digest generation |
| `src/app/jobs/email_triage.py` | Replaced stub with full cron sweep + per-advisor triage |
| `src/app/jobs/transcription.py` | Replaced stub with full audio chunking, Whisper/Deepgram transcription, chained summary |
| `src/app/jobs/firm_report.py` | Replaced stub with per-account analysis + aggregation pipeline |
| `src/app/jobs/style_profile.py` | Replaced stub with sent email analysis + style extraction |
| `src/app/jobs/worker.py` | Full rewrite: startup/shutdown, cron schedules, retry wrapper, health check |
| `src/app/services/platform_client.py` | Added list_active_tenants(), list_advisors(), get_meeting_metadata() |
| `src/app/dependencies.py` | Added build_worker_dependencies() |
| `src/app/routers/health.py` | Added GET /health/worker endpoint |
| `src/app/config.py` | Added langfuse_base_url field |

## Patterns Followed

- All jobs follow: deserialize JobContext → reconstruct deps → execute → store → observe
- Cron sweep + fan-out for per-advisor jobs (digest, triage, style profile)
- Classification-based retry via with_retry_policy decorator
- Dead-letter recording for permanently failed jobs
- Langfuse v4 OTEL spans for observability
- pydantic-ai Agent with output_type= and defer_model_check=True
- Existing schemas.py models reused for job outputs

## Test Results

```
139 passed in 2.00s
Ruff: All checks passed!
```
