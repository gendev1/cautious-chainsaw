# Design Discussion: Agents and Tools

## Resolved Decisions

### DD1: All 12 agents fully implemented (blocking)
- **Decision:** Implement all 12 agents with full system prompts and tool assignments, not just copilot and digest.
- **Rationale:** User confirmed Option B — extrapolate system prompts and tool lists for the 10 agents the spec doesn't fully detail, using the domain context and patterns from the 2 detailed examples.
- **Constraint for architect:** Every agent module must have: deps class, Agent instantiation with correct tier model, system_prompt function, tool list, and registry.register() call.

### DD2: Keep deps.py, add base_deps.py (blocking)
- **Decision:** Keep existing `app/agents/deps.py` as the DI bridge (FastAPI → agents with redis, retriever, context). Add `app/agents/base_deps.py` as the simpler base that tools type against (platform, access_scope, tenant_id, actor_id). Agent-specific deps (CopilotDeps, etc.) extend base_deps.py.
- **Rationale:** Spec 01 and spec 02 define different shapes for different purposes. Both are correct — deps.py bridges FastAPI DI, base_deps.py is what tools see. Aligns with platform chassis principle of narrow read-only agent interface.
- **Constraint for architect:** Tools use `RunContext[AgentDeps]` where AgentDeps is from base_deps.py. Route handlers construct the richer deps from FastAPI DI and narrow it down.

### DD3: All result type models created (blocking)
- **Decision:** Create every Pydantic result type model in schemas.py — HazelCopilot, DailyDigest, TaxPlan, MeetingSummary, EmailDraft, TriagedEmail, ExtractedTask, MeetingPrep, PortfolioAnalysis, FirmWideReport, DocClassification, DocExtraction, and all supporting models.
- **Rationale:** Models define the API contract. Even if an agent's implementation is later refined, the result type is stable.
- **Constraint for architect:** All models must have field descriptions matching the spec for LLM-visible schema generation.

### DD4: Add fakeredis to dev deps (informing)
- **Decision:** Add fakeredis to dev dependencies for conversation memory tests.
- **Constraint for architect:** Add `fakeredis` to `[dependency-groups] dev` in pyproject.toml.

## Open Questions

None — all questions resolved.

## Summary for Architect

This feature builds the Pydantic AI agent layer on top of core infrastructure. Key constraints:

1. **All 12 agents fully implemented** — extrapolate where spec is silent.
2. **Dual deps pattern** — keep deps.py for DI bridge, add base_deps.py for tool typing.
3. **All models created** — complete schemas.py with every result type.
4. **Tools are read-only** — enforce via PlatformClient interface and CI safety tests.
5. **Conversation memory** — Redis-backed with message codec preserving tool traces.
6. **Build order matters** — models → base_deps → tools → agents → services → router expansion.
