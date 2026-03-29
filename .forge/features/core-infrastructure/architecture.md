# Architecture: Core Infrastructure

## Approach A: Spec-Faithful Implementation (Bottom-Up)

Build layer by layer from the foundation, following the spec's code blocks almost verbatim. Start with models and utilities (no dependencies), then middleware, then DI, then main.py wiring.

**Implementation order:**
1. Config (`app/config.py`) — expand existing settings
2. Models (`app/models/access_scope.py`) — pure Pydantic, no deps
3. Context (`app/context.py`) — frozen dataclass, depends on AccessScope
4. Errors (`app/errors.py`) — error hierarchy, no deps
5. Utils (`app/utils/cache.py`) — pure function
6. Service stubs (`app/services/vector_store.py`, `app/services/platform_client.py`) — minimal interfaces
7. RAG stub (`app/rag/retriever.py`) — depends on vector store stub
8. Agent deps (`app/agents/deps.py`) — depends on context, platform client, retriever
9. Middleware (`app/middleware/request_id.py`, `tenant.py`, `logging.py`) — depends on context, access scope
10. Dependencies (`app/dependencies.py`) — DI wiring, depends on services and context
11. Health router (`app/routers/health.py`) — depends on DI
12. Domain router stubs (`app/routers/chat.py`, etc.) — empty APIRouters
13. Job stubs (`app/jobs/*.py`) — async function signatures
14. Main (`app/main.py`) — wires everything, delete old files
15. Update `app/__init__.py`
16. Update `pyproject.toml`
17. Rewrite tests

**Trade-offs:**
- (+) Directly matches the spec — minimal interpretation needed
- (+) Each step is independently testable
- (+) Dependency order means no forward references
- (-) Many small files created in sequence

**Deviation from existing patterns:** Replaces the single `app/api/routes.py` router with per-domain routers under `app/routers/`. This is deliberate per the spec.

## Approach B: Top-Down Shell Replacement

Start with `main.py` and work inward — create the app shell first with imports stubbed, then fill in each imported module.

**Implementation order:**
1. Create all `__init__.py` files and empty modules first
2. Write `app/main.py` with all imports and wiring
3. Fill in each imported module (config, middleware, dependencies, routers, etc.)
4. Delete old files
5. Update deps and tests

**Trade-offs:**
- (+) The final structure is visible immediately
- (-) Broken imports until every module is filled in — harder to test incrementally
- (-) Risk of circular imports during the filling phase
- (-) Can't validate each layer independently

## Recommendation

**Approach A: Spec-Faithful Bottom-Up** is the clear choice.

The spec provides production-ready code blocks for every file. The bottom-up order means each file can be created and validated before the next depends on it. The existing codebase is small enough that the "many small files" downside is negligible. The dependency graph is clean and acyclic when built in this order.

Approach B's only advantage (seeing the structure early) is irrelevant since the spec already defines the exact structure.

## Task Breakdown (recommended approach)

| Step | Files | Depends on |
|---|---|---|
| 1. Expand config | `app/config.py` | — |
| 2. Create models | `app/models/__init__.py`, `app/models/access_scope.py` | — |
| 3. Create context | `app/context.py` | step 2 |
| 4. Create errors | `app/errors.py` | — |
| 5. Create cache util | `app/utils/__init__.py`, `app/utils/cache.py` | — |
| 6. Create service stubs | `app/services/__init__.py`, `app/services/vector_store.py`, `app/services/platform_client.py` | — |
| 7. Create RAG stub | `app/rag/__init__.py`, `app/rag/retriever.py` | step 6 |
| 8. Create agent deps | `app/agents/__init__.py`, `app/agents/deps.py` | steps 3, 6, 7 |
| 9. Create middleware | `app/middleware/__init__.py`, `app/middleware/request_id.py`, `app/middleware/tenant.py`, `app/middleware/logging.py` | steps 2, 3 |
| 10. Create dependencies | `app/dependencies.py` | steps 1, 3, 6 |
| 11. Create health router | `app/routers/__init__.py`, `app/routers/health.py` | step 10 |
| 12. Create domain router stubs | `app/routers/chat.py`, `app/routers/digest.py`, `app/routers/email.py`, `app/routers/tasks.py`, `app/routers/meetings.py`, `app/routers/tax.py`, `app/routers/portfolio.py`, `app/routers/reports.py`, `app/routers/documents.py` | — |
| 13. Create job stubs | `app/jobs/__init__.py`, `app/jobs/worker.py`, `app/jobs/daily_digest.py`, `app/jobs/email_triage.py`, `app/jobs/firm_report.py`, `app/jobs/style_profile.py`, `app/jobs/transcription.py` | step 1 |
| 14. Create main.py and clean up | `app/main.py`, delete `app/app.py`, `app/api/` | all above |
| 15. Update init and deps | `app/__init__.py`, `pyproject.toml` | step 14 |
| 16. Rewrite tests | `tests/test_health.py` + new test files | step 14 |
