# Design Discussion: Feature Implementations

## Resolved Decisions

### 1. CRM router — Create new file
- **Category**: blocking
- **Decision**: Create src/app/routers/crm.py and register it in main.py.
- **Constraint**: Follow existing router pattern, /ai/crm prefix.

### 2. Response models — Import from schemas.py, define request/response wrappers in routers
- **Category**: blocking
- **Decision**: Use existing schemas.py models (DailyDigest, TriagedEmail, ExtractedTask, etc.) for the core data. Define thin request/response wrappers (with as_of, metadata) in router files since they're endpoint-specific.
- **Rationale**: Core models exist, but endpoint-specific wrappers (DigestJobAccepted, EmailTriageResponse, etc.) are router concerns.

### 3. Langfuse v4 — Use observe pattern
- **Category**: informing
- **Decision**: Adapt spec's Langfuse v2 API (.trace/.generation) to v4 OTEL spans. Use langfuse._start_as_current_otel_span_with_processed_media for tracing, consistent with what we built in spec 05 observability.

## Open Questions

None.

## Summary for Architect

- Create crm.py router (new)
- Replace 8 stub routers with full endpoint implementations
- Create utils/errors.py and utils/tracing.py for shared infrastructure
- Update dependencies.py with get_langfuse
- Update chat.py for CopilotDeps pattern
- Each router: request model → platform reads → agent invocation → response assembly
- Async endpoints (digest/generate, meetings/transcribe, reports/firm-wide) enqueue ARQ jobs, return 202
- Sync endpoints invoke agents directly, return 200
- All endpoints use get_request_context for tenant isolation
