# Discovery: portfolio-construction-v2

## Requirements

### Core Engine
- Replace `mandate-py` portfolio construction with a first-class module inside the intelligence layer at `apps/intelligence-layer/src/app/portfolio_construction/`.
- Produce a proposed target portfolio, rationale, and diagnostics. The engine must not place trades, mutate account records, or act as a system of record.
- Support two construction modes: `idea` (market data only) and `account_refresh` (account-aware, selected when `account_id` is provided and readable by scope).
- All external reads must flow through `PlatformClient` and carry the request `AccessScope`. The pipeline may read security universe metadata, fundamentals, price history, benchmark data, and account holdings/allocation. It may not write model assignments, create orders, mutate workflows, or bypass platform access controls.

### Factor Model v2
- Register `portfolio_factor_model_v2` in the analytics registry via `register_all_models()` as a deterministic model.
- Treat factor identification and factor scoring as separate concerns. Each factor in the canonical library must declare metadata: economic thesis, expected direction of payoff, required raw fields, minimum universe coverage, minimum cross-sectional dispersion, preferred normalization bucket, redundancy group, and regime notes.
- No factor active in production without an explicit research card documenting: economic rationale, applicable universes, coverage and freshness expectations, monotonicity/directional validation, and known failure modes.
- Implement runtime factor activation per request: compute universe-level viability, deactivate factors below 0.60 coverage or fewer than 3 viable sub-factors or insufficient dispersion, apply intent priors and config-driven theme-factor priors, normalize active-factor weights, and emit an activation report.
- Six canonical factors: Value (0.20), Quality (0.20), Growth (0.20), Momentum (0.15), Low Volatility (0.10), Size/Liquidity (0.15), each with specified core metrics.
- Implement robust normalization: invalid values become missing, log transforms for right-skewed metrics, hierarchical peer-bucket normalization (industry >= 15, sector >= 25, then universe), winsorization at 5th-95th percentile, empirical percentile rank converted to centered [-1, 1] scores, lower-is-better inversion, missing sub-factors omitted not zero-filled.
- Implement correlation-aware sub-factor aggregation: penalize redundant metrics via `adj_weight_i = base_weight_i / (1 + mean_abs_corr_i)`, publish effective weights in `universe_stats`.
- Implement reliability shrinkage: compute reliability from sub-factor coverage, data freshness, peer-bucket size, and critical metric presence; shrink toward neutral via `final = 50 + reliability * (raw - 50)`.
- Implement breadth-sensitive scoring: cap factor score at 65 if only 1 sub-factor available, cap at 75 if supportive weight share < 0.50, no cap if >= 0.70.
- Context-sensitive metrics (`rnd_intensity`, `market_cap`) handled at composite layer, not inside factor model.
- Overall factor score uses weighted geometric mean across active factors, not arithmetic average.
- Factor model input: `securities`, `fundamentals`, `prices`, `preferences`. Output: `scores`, `universe_stats`, `metadata`.

### Theme Scoring
- Final theme scores produced by LLM agent (`portfolio_theme_scorer`, batch tier, cheap classification model). No heuristic branch allowed to act as a final theme score.
- Deterministic code allowed only for coarse recall, hard exclusions, and caching.
- Two-stage recall pool: top N_factor (150) by factor score + all user include_tickers + broad metadata match set (N_metadata = 100), capped at 250.
- Theme scores on strict 0-100 scale. Anti-goals represented as `anti_goal_hit = true` plus `score = 0`, not negative scores.
- Theme score prompt must teach reasoning about: actual business exposure, revenue mix/product reality, broad vs. specific sub-themes, multi-theme matching, anti-goals as hard negatives, uncertainty handling (low-confidence scores conservatively).
- Cache theme scores keyed by `sha256(themes + anti_goals + sorted(tickers) + scorer_model + prompt_version + universe_snapshot_id)`. Request-scoped cache required; optional Redis cache with 6-hour TTL for cross-job reuse.
- Explicit exclusions and anti-goal hits applied before and after LLM scoring respectively.

### Composite Scoring and Gating
- Seven-step composite scoring: hard exclusion, anti-goal gate, eligibility gates (factor floor, theme floor), uncertainty adjustment (shrink low-confidence themes and low-reliability factors toward neutral), weighted geometric mean ranking, coherence bonus / weak-link penalty, clamp to [0, 100].
- Default parameters: theme_weight 0.60, factor_floor 25, theme_confidence_floor 0.50, interaction_bonus 5, min_theme_score 30, weak_link_gap 35, weak_link_penalty 5.
- Speculative intent may lower factor_floor to 10-15, lower min_market_cap, loosen max_sector_concentration, raise min_theme_score. Overrides must be explicit and reviewable in parsed intent.

### Optimizer and Constraints
- Hard constraints: excluded_tickers, excluded_sectors, include_tickers, min/max_market_cap, max_beta, max_single_position, max_sector_concentration, min_factor_score, min_theme_score, turnover_budget (account mode).
- Candidate selection: apply exclusions, rank by composite descending, honor include_tickers, enforce sector cap and position count, backfill from deferred names, auto-relax in fixed order (min_theme_score, max_beta, sector cap, reduce target count).
- Job must emit which constraints were relaxed and by how much.
- Four weighting strategies: equal, conviction, risk_parity (inverse realized vol, sector-median imputation), min_variance (shrunk covariance, score proxy, lambda 0.10, falls back to risk_parity on solver failure).
- Position limits: min_weight 0.02, max_weight 0.10. Iterative clamping with redistribution and explicit feasibility handling.
- Account-aware mode: read current holdings, compute overlap, estimated turnover, drift, tax-sensitive warnings. Recommendation-only, no executable trades.

### Intent Parser
- `portfolio_intent_parser` agent (copilot tier). Output: `ParsedIntent`.
- Must refine vague themes, infer factor preferences, infer risk/concentration tolerance, preserve explicit tickers and exclusions, emit `ambiguity_flags` when underspecified, coordinate `theme_weight` and `max_sector_concentration`.
- Specified inference rules for "large cap", "avoid meme stocks", "conservative", "pure play", "equal weight".

### Rationale and Critic
- `portfolio_rationale` agent (copilot tier) explains overall thesis, per-holding justification, key factor signals, core vs. supporting classification.
- `portfolio_critic` agent (copilot tier) reviews: theme alignment, anti-goal compliance, diversification, factor coherence, obvious core name inclusion, account-aware turnover realism.
- Critic output: `CriticFeedback` with status (APPROVED/NEEDS_REVISION), structured adjustment fields.
- Critic feedback must not override user exclusions, force inclusion violating hard constraints, or change universe/access scope.
- Review loop: up to 3 iterations. If still not approved, return best effort with manager note and warnings.
- Theme scores reused across iterations unless theme list or recall pool changes.

### Data Access and Adapters
- Typed platform reads for: equity universe listing, security metadata, fundamentals bulk, price-history bulk, benchmark data, account holdings/allocation.
- May require extending `PlatformClient` with explicit typed methods, not generic fetches.
- Fallback market-data adapter allowed during development: platform data primary, field-by-field merge with provenance, surfaced in warnings.
- Required typed models: `SecuritySnapshot`, `FundamentalsV2`, `PriceDataV2` with specified fields (all use `Decimal` at API boundary).
- Every upstream payload must carry freshness metadata. Job must warn when inputs exceed freshness thresholds.

### Execution Model
- Default path: API receives request, validates scope, enqueues ARQ job. Job emits persisted progress events keyed by `job_id`. Client polls status or streams via SSE. Final artifact is structured portfolio proposal.
- Synchronous execution may exist for local testing or tiny universes but is not the default production path.
- Persisted job events (not ephemeral pub/sub only): job_enqueued, intent_parsed, data_loaded, recall_pool_built, theme_scoring_started/completed, review_iteration_started, draft_built, critic_verdict, job_completed, job_failed.
- Recommended transport: ARQ for execution, Redis-backed job state, Redis Streams or equivalent for SSE fan-out.

### API Surface
- Extend `app/routers/portfolio.py` with: POST `/portfolio/construct` (returns 202 with job_id), GET `/portfolio/jobs/{job_id}` (status and final payload), GET `/portfolio/jobs/{job_id}/events` (stream progress events).
- Request schema: `ConstructPortfolioRequest` with message, optional account_id, target_count, weighting_strategy, include/exclude_tickers.
- Final payload includes: parsed intent, proposed holdings and weights, score breakdowns, rationale, warnings, applied relaxations, model/agent metadata.

### Observability, Safety, and Testing
- Track: job latency, per-stage latency, LLM token/cost usage, cache hit rate, fallback-provider usage, review iteration count, auto-relax frequency, optimizer fallback rate. Use existing Langfuse and service metrics patterns.
- Log request scope and job_id on every stage. Record prompt version, model tier, factor model version in final metadata. Emit warnings for stale data, low coverage, solver fallback, constraint relaxation.
- Required tests: unit tests for factor math and gating, prompt-contract tests for agent output schemas, orchestrator tests with mocked agents and platform client, regression fixtures against curated example prompts, shadow-mode comparison tests against `mandate-py`.

### Rollout
- Phase 1 (shadow mode): run v2 beside `mandate-py` on fixed prompts, compare outputs, not user-facing.
- Phase 2 (internal advisor preview): expose behind feature flag, richer diagnostics, capture manual feedback.
- Phase 3 (default path): v2 becomes default, `mandate-py` kept as regression reference.

## Decisions Already Made

- Module lives at `apps/intelligence-layer/src/app/portfolio_construction/` with a new analytics model at `apps/intelligence-layer/src/app/analytics/portfolio_factor_model_v2.py`.
- Default execution is ARQ-backed async jobs, not synchronous SSE.
- Anti-goals are represented as `anti_goal_hit = true` with `score = 0`, not negative theme scores.
- Theme-factor priors are config-driven and reviewable; they must not be inferred ad hoc by the LLM inside the scoring path.
- Final theme scores are always produced by an LLM agent; no heuristic branch may act as a final theme score.
- Recall pool uses a broad strategy (factor top-N + metadata matches + explicit includes), not factor-only prefiltering.
- Composite scoring uses weighted geometric mean, not linear blend.
- Factor scoring uses weighted geometric mean across active factors, not arithmetic average.
- Normalization is rank-based (percentile) with hierarchical peer buckets, not whole-universe z-scores.
- Sub-factor aggregation uses correlation-adjusted weights.
- Reliability shrinkage pulls uncertain scores toward neutral 50, not multiplicative penalty.
- Minimum variance optimizer uses shrunk covariance with a small score proxy (lambda 0.10) and falls back to risk_parity on solver failure.
- Auto-relax follows a fixed sequence: min_theme_score, max_beta, sector cap, reduce target count.
- Critic review loop capped at 3 iterations; best effort returned if not approved.
- `ambiguity_flags` is part of `ParsedIntent`, not a separate sidecar field.
- The sidecar remains read-oriented and recommendation-oriented; no order creation, OMS writes, or workflow mutation.
- Access scope enforcement through existing `PlatformClient` boundary.
- Progress events must be persisted (Redis Streams or equivalent), not ephemeral pub/sub.
- Agents registered in `app.agents.registry`: intent_parser, theme_scorer, rationale, critic. Factor model registered in `app.analytics.startup.register_all_models()`.
- Numeric conventions: `Decimal` at API boundary, `float` internally for vector math, explicit conversion layers.
- Position limit defaults: min_weight 0.02, max_weight 0.10.
- Recall pool defaults: N_factor = 150, N_metadata = 100, cap = 250.

## Constraints

- US equities only. Multi-asset, international equities, and ADR-specific handling are out of scope.
- No order creation, rebalancing, execution, custodial writes, OMS writes, or workflow advancement.
- No full transaction-cost or market-impact modeling.
- No benchmark-relative optimizer sophistication beyond simple score and risk controls.
- No lot-level tax-aware optimization as a core objective.
- All external reads must flow through `PlatformClient` with `AccessScope`; no generic fetches bypassing platform access controls.
- Must align to existing repo structure under `apps/intelligence-layer/src/app/`.
- Must use existing ARQ-based runtime for async job execution.
- Must use existing Langfuse and service metrics patterns for observability.
- Must use existing agent and analytics registry patterns for registration.
- Fallback data provider must not blindly overwrite platform values; field-by-field merge with provenance required.
- Theme-factor priors must be config-driven, not LLM-inferred at scoring time.
- Critic feedback must not override user exclusions, force constraint-violating inclusions, or change universe/access scope.
- Platform read models use `Decimal`; external monetary values must convert back to `Decimal`.
- Peer bucket minimums: industry >= 15 names, sector >= 25 names.
- Factor deactivation thresholds: coverage below 0.60, fewer than 3 viable sub-factors.
- Every factor in production must have a documented research card.

## Open Questions

All questions resolved:

- [x] **Theme scorer model**: Use `batch_model` from config (`anthropic:claude-haiku-4-5`). It's cheap, fast, and already configured with a fallback chain.
- [x] **Copilot-tier agents**: Use `copilot_model` from config (`anthropic:claude-sonnet-4-6`). Consistent with existing agents like `portfolio_analyst`.
- [x] **Theme-factor priors**: Python dict in code inside `portfolio_construction/config.py`. Keeps them version-controlled, reviewable in diffs, and close to the scoring code. Graduate to a config file only if non-engineers need to edit them.
- [x] **Factor research cards**: Docstrings on each factor class plus a `known_limitations` tuple in `ModelMetadata`. No separate docs — the research card IS the code-level documentation on the factor class. Matches the existing analytics model pattern.
- [x] **Redis infrastructure**: Existing Redis is sufficient. ARQ already uses it for job state. Use Redis Streams (available in Redis 5+, which the project requires) for persisted progress events. Theme score caching uses plain Redis keys with TTL.
- [x] **PlatformClient gaps**: New typed methods need to be added: `get_security_universe()`, `bulk_fundamentals(tickers)`, `bulk_price_data(tickers)`, `get_benchmark_data(benchmark_id)`. This is in-scope for this feature. Universe and market data come from the api-server, which proxies the external security master.
- [x] **IntentConstraints shape**: `excluded_tickers: list[str]`, `excluded_sectors: list[str]`, `min_market_cap: float | None`, `max_market_cap: float | None`, `max_beta: float | None`, `max_single_position: float = 0.10`, `max_sector_concentration: float = 0.30`, `turnover_budget: float | None` (account mode only).
- [x] **FactorPreferences shape**: `value: float = 0.20`, `quality: float = 0.20`, `growth: float = 0.20`, `momentum: float = 0.15`, `low_volatility: float = 0.10`, `size: float = 0.15`. Normalized to sum 1.0 at scoring time.
- [x] **PortfolioRationale shape**: `thesis_summary: str`, `holdings_rationale: dict[str, str]` (ticker → 1-2 sentence explanation), `core_holdings: list[str]` (tickers classified as core plays), `supporting_holdings: list[str]` (tickers classified as supporting exposure).
- [x] **Fallback market-data provider**: yfinance for development only. Platform API (via api-server connecting to the external security master) is the production data source. yfinance is a dev-time fallback with field-by-field merge and provenance tagging.
- [x] **universe_snapshot_id**: A hash of `sorted(tickers) + as_of_date`. Not an existing platform concept — generated at job time to key cache entries. Ensures theme scores are invalidated when the universe changes.
- [x] **Max universe size**: Determined by the security master exposed through the api-server. Expect S&P 500 (~500 names) as the primary universe. Engine should handle up to 3,000 names within acceptable latency (factor scoring is O(n), LLM theme scoring is batched with concurrency cap). The recall pool cap of 250 keeps the expensive LLM path bounded regardless.
- [x] **Auth for construct endpoint**: Existing `TenantContextMiddleware` + `AccessScope` suffices. No additional auth layer needed. The middleware already extracts tenant_id and actor_id from the request.
- [x] **Stale data behavior**: Warn and proceed. Emit a `data_staleness_warning` in the job's progress events and include it in the final payload's `warnings` list. Do not refuse the job — stale data is better than no portfolio. Make the threshold configurable via `SIDECAR_PORTFOLIO_FRESHNESS_WARN_S` (default 86400).
- [x] **Shadow-mode test harness**: Needs to be created. Build a fixture suite of 10-15 curated prompts with expected mandate-py outputs as baseline. This is part of the testing requirement.
- [x] **Covariance shrinkage**: Ledoit-Wolf (sklearn.covariance.LedoitWolf). Well-understood, deterministic, no hyperparameters, available in scikit-learn which is already a transitive dependency.
