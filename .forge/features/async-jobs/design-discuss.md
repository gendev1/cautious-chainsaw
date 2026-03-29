# Design Discussion: Async Jobs

## Resolved Decisions

### 1. PlatformClient — Add missing methods
- **Category**: blocking
- **Decision**: Add list_active_tenants() and list_advisors() to PlatformClient as real typed methods. Also add get_meeting_metadata().
- **Rationale**: Cron sweep jobs need these for fan-out. User wants real implementations, not stubs.
- **Constraint**: Follow the same pattern as the existing 24 methods (typed return, access scope, cache key).

### 2. Result types — Use existing schemas.py models
- **Category**: blocking
- **Decision**: Import DailyDigest, MeetingSummary, TriagedEmail, FirmWideReport, etc. from app.models.schemas. Do not redefine them in job files.
- **Rationale**: All 15 result types already exist. Duplication would create drift.
- **Constraint**: Job files import from app.models.schemas, not define their own.

### 3. Langfuse — Use real client
- **Category**: informing
- **Decision**: Use real Langfuse client. User will provide API keys. No mocking needed for tests.
- **Rationale**: User preference. Tests that exercise Langfuse will need env vars set.
- **Constraint**: For pure unit tests of job logic (chunking, error classification, retry), mock the langfuse parameter. For integration tests, use real client.

## Open Questions

None.

## Summary for Architect

- Replace 5 existing job stubs with full implementations
- Create 4 new infrastructure files: enqueue.py, errors.py (job-specific), retry.py, observability.py
- Create 2 new job files: meeting_summary.py, rag_index.py
- Rewrite worker.py with startup/shutdown, cron schedules, retry wrapper
- Add 3 new PlatformClient methods (list_active_tenants, list_advisors, get_meeting_metadata)
- Add worker health endpoint to health.py
- Add build_worker_dependencies() to dependencies.py
- Config needs: platform_base_url (for sweep HTTP calls). redis_host/port/password/job_db not needed (use existing redis_url)
- Use existing schemas.py models for all job outputs
- Adapt pydantic-ai: output_type not result_type, defer_model_check=True, no fallback_model
