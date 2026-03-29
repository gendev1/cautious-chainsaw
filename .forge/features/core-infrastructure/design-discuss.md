# Design Discussion: Core Infrastructure

## Resolved Decisions

### DD1: Migration strategy (blocking)
- **Decision:** Replace existing `app/app.py` and `app/api/` entirely with the spec's structure.
- **Rationale:** User chose clean replacement over parallel files. The existing code is minimal enough that no backward compatibility is needed.
- **Constraint for architect:** Delete `app/app.py`, `app/api/routes.py`, `app/api/__init__.py`. Create `app/main.py` as the new entry point. Update `app/__init__.py` to export from `app.main`.

### DD2: Service stubs (blocking)
- **Decision:** Create minimal stub implementations for VectorStore, PlatformClient, and Retriever.
- **Rationale:** The core infrastructure needs these interfaces to compile and boot. Full implementations come in later specs.
- **Constraint for architect:** Stubs must implement `connect()`, `disconnect()`, `health_check()` (VectorStore), `close()` (PlatformClient), and match the signatures referenced in `dependencies.py` and `errors.py`.

### DD3: Job and router stubs (blocking)
- **Decision:** Create empty stub modules for all job functions and all domain routers.
- **Rationale:** The ARQ worker and `main.py` router registration import these modules. They must exist to avoid ImportError.
- **Constraint for architect:** Each router stub needs `router = APIRouter(tags=[...])`. Each job stub needs the async function signature that ARQ expects. No business logic.

### DD4: Dependency updates (informing)
- **Decision:** Update `pyproject.toml` with all packages required by core infrastructure.
- **Rationale:** User confirmed deps should be managed as part of this feature.
- **Constraint for architect:** The existing deps already include most packages (fastapi, pydantic-settings, pydantic-ai, httpx, redis, arq, structlog, langfuse). Verify completeness and add any missing ones.

## Open Questions

None — all questions resolved.

## Summary for Architect

The spec is highly prescriptive with production-ready code blocks. The implementation is a restructuring of the existing minimal shell into the spec's target architecture. Key constraints:

1. **Clean replacement** — delete old files, don't bridge.
2. **Stubs everywhere** — every import target must exist, even if hollow.
3. **Update deps** — pyproject.toml must have all required packages.
4. **Existing tests** must be rewritten to match the new structure (`/health` not `/healthz`).
5. **The spec's code blocks are authoritative** — follow them closely rather than inventing alternatives.
