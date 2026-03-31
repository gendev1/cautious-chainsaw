# Design Discussion: portfolio-construction-v2

## Resolved Decisions

### 1. Theme Scorer Model Tier

- **Category:** blocking
- **Decision:** Use `batch_model` from config (`anthropic:claude-haiku-4-5`) for `portfolio_theme_scorer`.
- **Rationale:** Theme scoring is a high-volume classification task (up to 250 tickers per job). Haiku is cheap, fast, and already configured with a fallback chain. The task does not require deep reasoning -- it requires consistent 0-100 scoring against a defined rubric.
- **Constraint for architect:** The theme scorer agent MUST use `settings.batch_model`, not a copilot or analysis tier model. The agent definition must pass the model string dynamically from config, not hardcode it.

### 2. Copilot-Tier Agents (Intent Parser, Rationale, Critic)

- **Category:** blocking
- **Decision:** Use `copilot_model` from config (`anthropic:claude-sonnet-4-6`) for `portfolio_intent_parser`, `portfolio_rationale`, and `portfolio_critic`.
- **Rationale:** These agents require nuanced reasoning (disambiguating user intent, generating investment rationale, critiquing portfolio coherence). Sonnet is the established copilot tier across the codebase (e.g., `portfolio_analyst`).
- **Constraint for architect:** All three agents MUST use `settings.copilot_model`. Registration must specify `tier="copilot"` in the agent registry.

### 3. Theme-Factor Priors Storage

- **Category:** informing
- **Decision:** Store theme-factor priors as a Python dict in `portfolio_construction/config.py`.
- **Rationale:** Keeps priors version-controlled, reviewable in diffs, and co-located with scoring code. Avoids premature externalization to YAML/JSON config files that would require a separate deployment pipeline. Graduate to a config file only if non-engineers need to edit them.
- **Constraint for architect:** Theme-factor priors MUST live in `portfolio_construction/config.py` as a typed Python constant, not loaded from an external file or database. The scoring pipeline must import them from this single source of truth.

### 4. Factor Research Cards

- **Category:** informing
- **Decision:** Research cards are docstrings on each factor class plus `known_limitations` in `ModelMetadata`. No separate documentation files.
- **Rationale:** Matches the existing analytics model pattern (e.g., `DriftDetector.metadata.known_limitations`). Keeps research documentation co-located with the code that implements the factor, ensuring they stay in sync.
- **Constraint for architect:** Each factor class MUST have a docstring documenting: economic rationale, applicable universes, coverage and freshness expectations, monotonicity/directional validation, and known failure modes. The `ModelMetadata.known_limitations` tuple must be populated. No separate markdown research card files.

### 5. Redis Infrastructure for Progress Events and Theme Cache

- **Category:** blocking
- **Decision:** Use existing Redis instance. Redis Streams for persisted progress events. Plain Redis keys with 6-hour TTL for theme score caching.
- **Rationale:** ARQ already uses Redis for job state. Redis 5+ (project requirement) supports Streams natively. No new infrastructure is needed.
- **Constraint for architect:** Progress events MUST use Redis Streams (XADD/XREAD), not pub/sub or polling. Theme score cache keys must include `sha256(themes + anti_goals + sorted(tickers) + scorer_model + prompt_version + universe_snapshot_id)`. Request-scoped cache is required; Redis cache with 6-hour TTL is optional but recommended. Add `SIDECAR_PORTFOLIO_THEME_CACHE_TTL_S` (default 21600) to Settings.

### 6. PlatformClient Extensions

- **Category:** blocking
- **Decision:** Add typed methods: `get_security_universe()`, `bulk_fundamentals(tickers)`, `bulk_price_data(tickers)`, `get_benchmark_data(benchmark_id)`. Universe and market data come from the api-server, which proxies the external security master.
- **Rationale:** The portfolio construction pipeline requires bulk data access patterns that don't exist in the current PlatformClient. All external reads must flow through PlatformClient with AccessScope -- no direct external API calls.
- **Constraint for architect:** New PlatformClient methods MUST follow the existing pattern: accept `access_scope: AccessScope`, use `self._cache_key()` for request-scoped caching, return typed Pydantic models (`SecuritySnapshot`, `FundamentalsV2`, `PriceDataV2`). Methods must call api-server endpoints, not external services directly. `get_benchmark_data` already exists for the default benchmark; extend it to accept an arbitrary `benchmark_id`.

### 7. IntentConstraints Shape

- **Category:** blocking
- **Decision:** `IntentConstraints` fields: `excluded_tickers: list[str]`, `excluded_sectors: list[str]`, `min_market_cap: float | None`, `max_market_cap: float | None`, `max_beta: float | None`, `max_single_position: float = 0.10`, `max_sector_concentration: float = 0.30`, `turnover_budget: float | None` (account mode only).
- **Rationale:** Covers all hard constraints referenced in the optimizer and composite scoring specs. Optional fields use `None` to distinguish "not specified" from "no limit". Defaults align with the discovery document.
- **Constraint for architect:** The `IntentConstraints` Pydantic model MUST use exactly these field names and types. `turnover_budget` must only be populated in `account_refresh` mode. The intent parser agent must output this shape; the optimizer must consume it without transformation.

### 8. FactorPreferences Shape

- **Category:** blocking
- **Decision:** `FactorPreferences` fields: `value: float = 0.20`, `quality: float = 0.20`, `growth: float = 0.20`, `momentum: float = 0.15`, `low_volatility: float = 0.10`, `size: float = 0.15`. Normalized to sum 1.0 at scoring time.
- **Rationale:** Six canonical factors with default weights that sum to 1.0. The intent parser may adjust weights based on user language (e.g., "conservative" raises `low_volatility`), but normalization ensures the factor model always receives valid weights.
- **Constraint for architect:** The factor model MUST normalize `FactorPreferences` to sum 1.0 before use. Normalization is the factor model's responsibility, not the intent parser's. Field names must match the six canonical factor names exactly.

### 9. PortfolioRationale Shape

- **Category:** informing
- **Decision:** `PortfolioRationale` fields: `thesis_summary: str`, `holdings_rationale: dict[str, str]` (ticker to 1-2 sentence explanation), `core_holdings: list[str]`, `supporting_holdings: list[str]`.
- **Rationale:** Provides the advisor with a structured narrative: an overall thesis, per-holding justifications, and a core-vs-supporting classification for portfolio positioning.
- **Constraint for architect:** The rationale agent MUST output this exact shape. `holdings_rationale` keys must be ticker strings matching the proposed holdings. Every ticker in the proposed portfolio must appear in either `core_holdings` or `supporting_holdings`, but not both.

### 10. Fallback Market-Data Provider

- **Category:** blocking
- **Decision:** yfinance is the development-only fallback. Platform API (via api-server proxying the external security master) is the production data source. Fallback uses field-by-field merge with provenance tagging.
- **Rationale:** yfinance is free, requires no API keys, and provides adequate data for development. But it is unreliable, rate-limited, and its data quality is insufficient for production. The api-server is the single source of truth.
- **Constraint for architect:** The fallback adapter MUST NOT be active in production (guard with a config flag or environment check). When active, it MUST NOT overwrite platform values -- only fill missing fields. Every value sourced from the fallback MUST carry `provenance: "yfinance"` metadata. The data loader must surface a warning in the job's `warnings` list when any fallback data is used.

### 11. universe_snapshot_id Generation

- **Category:** informing
- **Decision:** `universe_snapshot_id` is `sha256(sorted(tickers) + as_of_date)`, generated at job time. Not an existing platform concept.
- **Rationale:** Theme scores must be invalidated when the universe changes. A hash of the universe composition plus date provides a stable, deterministic cache key without requiring platform support.
- **Constraint for architect:** `universe_snapshot_id` MUST be computed as `sha256("|".join(sorted(tickers)) + "|" + as_of_date)` (or equivalent deterministic serialization). It is included in theme score cache keys and in the final job metadata. It must NOT be fetched from the platform.

### 12. Maximum Universe Size

- **Category:** informing
- **Decision:** Primary universe is S&P 500 (~500 names). Engine must handle up to 3,000 names. The recall pool cap of 250 keeps the expensive LLM path bounded.
- **Rationale:** The security master exposed through the api-server determines available names. Factor scoring is O(n) and handles 3,000 names easily. The LLM bottleneck is managed by the recall pool cap.
- **Constraint for architect:** Factor scoring and normalization must be performant for up to 3,000 securities. The recall pool MUST enforce a hard cap of 250 names entering theme scoring regardless of universe size. Batch the LLM theme scoring with a concurrency cap to avoid rate limits.

### 13. Authentication for Construct Endpoint

- **Category:** informing
- **Decision:** Existing `TenantContextMiddleware` + `AccessScope` suffices. No additional auth layer.
- **Rationale:** The middleware already extracts `tenant_id` and `actor_id` from request headers. AccessScope flows through JobContext to the ARQ job, ensuring platform reads are scoped correctly.
- **Constraint for architect:** The construct endpoint MUST use the standard `Depends(get_request_context)` injection. No custom auth middleware. The `AccessScope` must be serialized into `JobContext.access_scope` and reconstructed in the job via `AccessScope(**job_ctx.access_scope)`.

### 14. Stale Data Behavior

- **Category:** blocking
- **Decision:** Warn and proceed. Emit `data_staleness_warning` in progress events and final payload warnings. Do not refuse the job. Threshold configurable via `SIDECAR_PORTFOLIO_FRESHNESS_WARN_S` (default 86400 seconds / 24 hours).
- **Rationale:** Stale data is better than no portfolio. Advisors need results even when upstream data is delayed. The warning gives them transparency to exercise judgment.
- **Constraint for architect:** The data loader MUST check `FreshnessMeta.staleness_seconds` against the configured threshold for every upstream payload. When exceeded, it MUST emit a `data_staleness_warning` progress event AND include it in the final `warnings` list. The job MUST NOT fail or refuse to proceed due to stale data. Add `SIDECAR_PORTFOLIO_FRESHNESS_WARN_S` to `config.py` Settings with default 86400.

### 15. Shadow-Mode Test Harness

- **Category:** informing
- **Decision:** Build a fixture suite of 10-15 curated prompts with expected mandate-py outputs as baseline. This is in-scope for this feature.
- **Rationale:** Shadow mode (Phase 1 rollout) requires automated comparison between v2 and mandate-py outputs on fixed prompts. The harness validates parity before user-facing exposure.
- **Constraint for architect:** Create a test fixture file containing 10-15 curated prompts with mandate-py baseline outputs. The shadow-mode test runner must execute both v2 and mandate-py on each prompt and produce a structured comparison report. This is a test artifact, not a production code path.

### 16. Covariance Shrinkage Method

- **Category:** blocking
- **Decision:** Ledoit-Wolf shrinkage via `sklearn.covariance.LedoitWolf`.
- **Rationale:** Well-understood, deterministic, requires no hyperparameters, and scikit-learn is already a transitive dependency. Suitable for the min_variance weighting strategy's covariance estimation.
- **Constraint for architect:** The min_variance optimizer MUST use `sklearn.covariance.LedoitWolf` for covariance shrinkage. Do not introduce alternative shrinkage estimators or additional dependencies. On solver failure, fall back to `risk_parity` strategy as specified.

## Open Questions

### OQ-1: Portfolio Construction Deps Shape

The pipeline requires `platform`, `access_scope`, `redis`, and `langfuse` across the orchestrator, data loader, and agents. Existing patterns offer two dep shapes: `AgentDeps` (simple: platform + scope + tenant + actor) and `CopilotDeps` (extended: adds context, redis, retriever). The orchestrator job has access to all deps via the ARQ worker context dict, but the agents themselves need a consistent deps type.

**Tension:** Should all four agents share a single `PortfolioConstructionDeps(AgentDeps)` that adds redis (for cache access in theme scorer)? Or should the theme scorer use richer deps while the other three use plain `AgentDeps`? The theme scorer needs Redis for cache reads/writes, but the intent parser, rationale, and critic do not.

**Recommendation:** Create `PortfolioConstructionDeps(AgentDeps)` with an optional `redis` field. Agents that need it use it; others ignore it. This avoids multiple dep types while keeping the interface uniform.

### OQ-2: Progress Event Schema Versioning

The discovery document specifies 10 distinct progress event types (job_enqueued through job_failed). These events are persisted in Redis Streams and consumed by SSE endpoints. If the event schema evolves (e.g., adding fields to `theme_scoring_completed`), existing consumers may break.

**Tension:** Should events carry a schema version field? Should the SSE endpoint handle backward compatibility? The existing codebase has no precedent for versioned event streams since no other feature uses Redis Streams for progress tracking.

**Recommendation:** Add a `v: int = 1` field to every event payload. The SSE endpoint should tolerate unknown fields. This is low-cost insurance that avoids a breaking migration later.

### OQ-3: Concurrency Cap for LLM Theme Scoring Batches

The recall pool sends up to 250 tickers for theme scoring. The theme scorer agent processes these in batches. The discovery doc mentions a "concurrency cap" but does not specify the batch size or max concurrent LLM calls.

**Tension:** Too few concurrent calls means slow jobs (250 tickers at 1-per-call is 250 serial LLM round-trips). Too many concurrent calls risks rate limiting from the model provider. The optimal batch size depends on whether the LLM scores one ticker per call or multiple tickers per prompt.

**Recommendation:** Score multiple tickers per prompt (batch of 10-20 tickers per LLM call) with a concurrency cap of 5-10 parallel calls. This yields ~13-25 LLM calls per job, completing in seconds rather than minutes. Make both batch size and concurrency cap configurable in `portfolio_construction/config.py`.

## Summary for Architect

### Module Structure
The portfolio construction module lives at `app/portfolio_construction/` with a new analytics model at `app/analytics/portfolio_factor_model_v2.py`. The module is organized into: config, models, orchestrator, agents (4), data loader, recall pool, composite scorer, optimizer, account-aware logic, job entry point, and event emission. Eight existing files require modification (see exploration.md Expected Edits).

### Execution Model
Default execution is ARQ-backed async jobs. Router accepts request, validates scope, enqueues job, returns 202 with `job_id`. Job emits persisted progress events to Redis Streams. Client polls status or streams via SSE. No synchronous production path.

### Agent Configuration
- **Intent parser, rationale, critic:** `settings.copilot_model` (Sonnet), registered as `tier="copilot"`.
- **Theme scorer:** `settings.batch_model` (Haiku), registered as `tier="batch"`.
- All agents registered in `app.agents.registry` at import time following the existing pattern.

### Factor Model
- Registered as `portfolio_factor_model_v2` (PORTFOLIO, DETERMINISTIC) in analytics registry via `register_all_models()`.
- Six canonical factors with default weights summing to 1.0, normalized at scoring time.
- Hierarchical peer-bucket normalization (industry >= 15, sector >= 25, then universe).
- Correlation-adjusted sub-factor aggregation, reliability shrinkage toward neutral 50, breadth-sensitive score caps.
- Weighted geometric mean across active factors.
- Factor deactivation at < 0.60 coverage or < 3 viable sub-factors.
- Research cards as docstrings + `known_limitations` tuple. No separate docs.

### Theme Scoring
- LLM-only final scores (no heuristic branch as final score).
- Recall pool: top 150 by factor + 100 metadata matches + explicit includes, capped at 250.
- Theme-factor priors in `portfolio_construction/config.py` as Python dict.
- Cache key: `sha256(themes + anti_goals + sorted(tickers) + scorer_model + prompt_version + universe_snapshot_id)`.
- Request-scoped cache required; Redis cache with 6-hour TTL optional.
- Anti-goals: `anti_goal_hit = true`, `score = 0`. No negative scores.

### Composite Scoring
- Seven-step pipeline: exclusion, anti-goal gate, eligibility gates, uncertainty adjustment, weighted geometric mean, coherence bonus/weak-link penalty, clamp [0, 100].
- Default: theme_weight 0.60, factor_floor 25, theme_confidence_floor 0.50.

### Optimizer
- Four weighting strategies: equal, conviction, risk_parity, min_variance.
- Min_variance uses Ledoit-Wolf shrinkage (`sklearn.covariance.LedoitWolf`), score proxy lambda 0.10, falls back to risk_parity on solver failure.
- Position limits: min_weight 0.02, max_weight 0.10. Iterative clamping.
- Auto-relax sequence: min_theme_score -> max_beta -> sector cap -> reduce target count. Must emit relaxation details.

### Data Access
- All reads through PlatformClient with AccessScope. New typed methods: `get_security_universe()`, `bulk_fundamentals()`, `bulk_price_data()`, `get_benchmark_data(benchmark_id)`.
- Data sourced from api-server (proxies external security master). yfinance is dev-only fallback with field-by-field merge and provenance tagging; must not be active in production.
- Decimal at API boundary, float internally for vector math. Explicit conversion layers.
- Stale data: warn and proceed. Threshold: `SIDECAR_PORTFOLIO_FRESHNESS_WARN_S` (default 86400s).

### Critic Review Loop
- Up to 3 iterations. If not approved, return best effort with manager note and warnings.
- Theme scores reused across iterations unless theme list or recall pool changes.
- Critic must not override user exclusions, force constraint-violating inclusions, or change scope.

### Account-Aware Mode
- Triggered when `account_id` is provided and readable by scope.
- Computes overlap, turnover, drift, tax-sensitive warnings. Recommendation only -- no trades, no OMS writes.
- `turnover_budget` constraint only applies in this mode.

### Observability
- Use existing Langfuse + JobTracer patterns. Track: job latency, per-stage latency, LLM token/cost, cache hit rate, fallback usage, review iteration count, auto-relax frequency, optimizer fallback rate.
- Log request scope and job_id on every stage. Record prompt version, model tier, factor model version in metadata.
- Progress events persisted in Redis Streams (10 event types from job_enqueued through job_failed).

### Auth and Scope
- Existing TenantContextMiddleware + AccessScope. No new auth layer.
- Scope serialized into JobContext, reconstructed in job.

### Testing
- Unit tests for factor math, composite scoring, optimizer, recall pool, model validation.
- Prompt-contract tests for agent output schemas.
- Orchestrator integration tests with mocked agents and PlatformClient.
- Shadow-mode fixture suite: 10-15 curated prompts with mandate-py baselines.
- Follow flat test naming: `tests/test_portfolio_*.py`.

### Rollout
- Phase 1: Shadow mode beside mandate-py, not user-facing.
- Phase 2: Internal advisor preview behind feature flag.
- Phase 3: v2 becomes default, mandate-py kept as regression reference.

### Hard Boundaries (Must Not)
- Must not place trades, mutate account records, create orders, write to OMS, advance workflows.
- Must not bypass PlatformClient / AccessScope for any external read.
- Must not use heuristic branch as final theme score.
- Must not have LLM infer theme-factor priors at scoring time.
- Must not blindly overwrite platform data with fallback data.
- US equities only. No multi-asset, international equities, or ADR handling.
- No full transaction-cost modeling, benchmark-relative optimization, or lot-level tax optimization.
