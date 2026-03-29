# Discovery: Feature Implementations

## Requirements

1. **Shared infrastructure** — utils/errors.py (ErrorCategory, SidecarError response model, HTTP exception classes), utils/tracing.py (traced_agent_call decorator), dependencies.py updates (get_langfuse, updated get_request_context)
2. **8 router implementations** replacing stubs:
   - `digest.py` — POST /ai/digest/generate (202, ARQ), GET /ai/digest/latest (cache read)
   - `email.py` — POST /ai/email/draft (sync, Copilot), POST /ai/email/triage (sync, Batch)
   - `tasks.py` — POST /ai/tasks/extract (sync, Batch)
   - `crm.py` — POST /ai/crm/sync-payload (sync, Batch) [note: spec says crm.py but existing stub is not named crm.py]
   - `meetings.py` — POST /ai/meetings/prep (sync), POST /ai/meetings/transcribe (202, ARQ), POST /ai/meetings/summarize (sync), GET /ai/meetings/{id}/summary (cache read)
   - `tax.py` — POST /ai/tax/plan (sync, Analysis)
   - `portfolio.py` — POST /ai/portfolio/analyze (sync, Copilot)
   - `reports.py` — POST /ai/reports/firm-wide (202, ARQ), POST /ai/reports/narrative (sync)
   - `documents.py` — POST /ai/documents/classify (sync), POST /ai/documents/extract (sync)
3. **Chat router update** — Existing chat.py may need updates for the spec's CopilotDeps pattern
4. **18 total endpoints** across all routers

## Decisions Already Made

- Every request uses Depends(get_request_context) for tenant/actor context
- Langfuse tracing on every agent call
- Async jobs return HTTP 202
- Financial outputs include freshness metadata
- Errors classified into 4 categories
- Existing schemas.py models used for responses
- Existing agents from spec 02 wired into routers

## Constraints

- Existing chat.py has real implementation — adapt, don't rewrite
- Existing health.py and indexing.py — don't touch
- main.py already includes all routers with /ai prefix
- Spec uses Langfuse v2 API (.trace(), .generation()) — must adapt for Langfuse v4
- Spec references CopilotDeps, DigestDeps etc. — agents may not have these exact classes
- pydantic-ai: output_type not result_type, defer_model_check=True

## Open Questions

- [ ] The spec defines a `crm.py` router but the existing stubs don't include a crm.py. Should we create it as a new router, or does it map to an existing stub?
- [ ] The spec redefines response models (ChatResponse, DigestItem, etc.) in each router file. Should routers define their own response models or import from schemas.py?
