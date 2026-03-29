# Architecture: Feature Implementations

## Approach A: Router-by-Router Implementation (Recommended)

Implement each router as a self-contained file with request/response models and agent wiring. Shared infrastructure (error classes, tracing utility) created first.

**Files created (3):**
1. `src/app/utils/errors.py` — HTTP exception classes for 4 error categories
2. `src/app/utils/tracing.py` — Langfuse v4 tracing utility
3. `src/app/routers/crm.py` — New CRM sync router

**Files replaced (8 stubs):**
4. `src/app/routers/digest.py`
5. `src/app/routers/email.py`
6. `src/app/routers/tasks.py`
7. `src/app/routers/meetings.py`
8. `src/app/routers/tax.py`
9. `src/app/routers/portfolio.py`
10. `src/app/routers/reports.py`
11. `src/app/routers/documents.py`

**Files modified (2):**
12. `src/app/routers/chat.py` — Update to use CopilotDeps pattern from spec
13. `src/app/dependencies.py` — Add get_langfuse dependency
14. `src/app/main.py` — Add crm router inclusion

## Recommendation

**Approach A** — straightforward, each router is independent.

## Task Breakdown (recommended approach)

| Order | File | Action | Depends On |
|---|---|---|---|
| 1 | `utils/errors.py` | Create shared error classes | — |
| 2 | `utils/tracing.py` | Create Langfuse tracing utility | — |
| 3 | `dependencies.py` | Add get_langfuse | — |
| 4 | `routers/digest.py` | Replace stub | 1-3 |
| 5 | `routers/email.py` | Replace stub | 1-3 |
| 6 | `routers/tasks.py` | Replace stub | 1-3 |
| 7 | `routers/crm.py` | Create new | 1-3 |
| 8 | `routers/meetings.py` | Replace stub | 1-3 |
| 9 | `routers/tax.py` | Replace stub | 1-3 |
| 10 | `routers/portfolio.py` | Replace stub | 1-3 |
| 11 | `routers/reports.py` | Replace stub | 1-3 |
| 12 | `routers/documents.py` | Replace stub | 1-3 |
| 13 | `routers/chat.py` | Update CopilotDeps | 1-3 |
| 14 | `main.py` | Add crm router | 7 |
