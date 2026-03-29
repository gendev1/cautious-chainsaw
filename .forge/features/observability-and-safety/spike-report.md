# Spike Report: Observability and Safety

## Dependencies Verified

| Dependency | Version | Status |
|---|---|---|
| langfuse | 4.0.1 | v4 OTEL API works: _start_as_current_otel_span_with_processed_media, flush, shutdown, @observe |
| prometheus_client | 0.22.1 | Already installed. Counter, Histogram, Gauge, generate_latest all work |
| structlog | 25.4.0 | contextvars, stdlib, ProcessorFormatter all available |

## Langfuse v4 API Notes

- No .trace() or .generation() methods on Langfuse client
- Use `lf._start_as_current_otel_span_with_processed_media(name=..., as_type="generation")` for spans
- Span context managers: `ctx.__enter__()` / `ctx.__exit__()`
- Both return `LangfuseGeneration` objects
- `lf.flush()` and `lf.shutdown()` exist for lifecycle
- `@observe` decorator available from `langfuse`
- `lf.create_trace_id()` available for explicit trace IDs

## Risks

None — all dependencies verified and working.
