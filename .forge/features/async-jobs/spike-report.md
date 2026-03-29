# Spike Report: Async Jobs

## Dependencies Verified

| Dependency | Version | Status |
|---|---|---|
| arq | 0.27.0 | Available — cron, RedisSettings, Retry all present |
| langfuse | installed | Available — Langfuse client importable |
| httpx | 0.28.1 | Available |
| pydantic-ai | 1.73.0 | Available — output_type, defer_model_check |

## Spike Result

No new external dependencies required. All imports resolve.

## Risks

- arq 0.27.0 uses `cron()` from `arq` directly (not `arq.cron`)
- Langfuse tests need LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY env vars
