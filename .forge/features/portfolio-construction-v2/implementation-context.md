# Implementation Context: portfolio-construction-v2

## Chosen Approach

**Approach B: Layered Module with Class-Based Orchestrator and Central Agent Registration.**

The portfolio construction pipeline lives at `apps/intelligence-layer/src/app/portfolio_construction/` with an `agents/` sub-package grouping the four LLM agents. The orchestrator is a class (`PortfolioConstructionPipeline`) whose constructor receives all dependencies and whose `run()` method executes the multi-stage pipeline. Each stage is a private method, independently testable. Agents register in the central `app.agents.registry` at import time. The factor model is a standard analytics model at `app/analytics/portfolio_factor_model_v2.py` registered via `register_all_models()`. A `DataLoader` class wraps PlatformClient reads with Decimal-to-float conversion and freshness checking. Progress events use Redis Streams via a `ProgressEventEmitter` class. The ARQ job entry point is a thin wrapper that creates the pipeline and delegates to `run()`.

Key structural decisions:
- Class-based orchestrator (not flat function, not protocol-based) for testability and review loop clarity.
- Agents sub-package within `portfolio_construction/` to co-locate domain agents without polluting `app/agents/`.
- `DataLoader` owns the Decimal-to-float boundary so PlatformClient stays clean.
- Theme scorer uses `Agent[None, ...]` (batch tier, prompt-only context). Intent parser, rationale, and critic use `Agent[AgentDeps, ...]` (copilot tier).

All paths below are relative to `apps/intelligence-layer/src/` unless prefixed with `tests/` (which means `apps/intelligence-layer/tests/`).

## Implementation Order

### Step 1: Domain Models and Configuration

**What:** Create the foundational Pydantic models and configuration constants that every subsequent step depends on.

**Files to create:**
- `app/portfolio_construction/__init__.py` -- Package init, empty or re-exports.
- `app/portfolio_construction/models.py` -- All domain types: `ConstructPortfolioRequest` (message, optional account_id, target_count, weighting_strategy, include/exclude_tickers), `ConstructPortfolioResponse` (parsed intent, proposed holdings, score breakdowns, rationale, warnings, relaxations, metadata), `ParsedIntent` (themes, anti_goals, factor_preferences, intent_constraints, ambiguity_flags, theme_weight, speculative), `IntentConstraints` (excluded_tickers, excluded_sectors, min/max_market_cap, max_beta, max_single_position=0.10, max_sector_concentration=0.30, turnover_budget), `FactorPreferences` (value=0.20, quality=0.20, growth=0.20, momentum=0.15, low_volatility=0.10, size=0.15 with sum-to-1 normalization), `ThemeScoreResult` (ticker, score 0-100, confidence, anti_goal_hit, reasoning), `FactorScoreResult` (ticker, overall_score, per_factor_scores dict, reliability, sub_factor_coverage), `CompositeScoreResult` (ticker, composite_score, factor_score, theme_score, gated, gate_reason, coherence_bonus, weak_link_penalty), `ProposedHolding` (ticker, weight, composite_score, factor_score, theme_score, sector, rationale_snippet), `CriticFeedback` (status APPROVED/NEEDS_REVISION, adjustment fields, reasoning), `PortfolioRationale` (thesis_summary, holdings_rationale dict, core_holdings, supporting_holdings), `PortfolioConstructionAccepted` (job_id), job event types (JobEvent with event_type enum, payload).
- `app/portfolio_construction/config.py` -- `THEME_FACTOR_PRIORS` dict (theme keywords to factor weight adjustments), `FACTOR_DEFINITIONS` dict (6 canonical factors with default weights, core metrics, normalization notes), `DEFAULT_COMPOSITE_PARAMS` (theme_weight=0.60, factor_floor=25, theme_confidence_floor=0.50, interaction_bonus=5, min_theme_score=30, weak_link_gap=35, weak_link_penalty=5), `DEFAULT_OPTIMIZER_PARAMS` (min_weight=0.02, max_weight=0.10, default target_count=25), `RECALL_POOL_PARAMS` (N_factor=150, N_metadata=100, cap=250), `THEME_SCORE_BATCH_SIZE=15`, `THEME_SCORE_CONCURRENCY_CAP=5`.
- `tests/test_portfolio_construction_models.py` -- Test all models: construction with valid data, serialization round-trip via `model_dump()`/`model_validate()`, default values, `FactorPreferences` normalization, `IntentConstraints` optional fields, `CriticFeedback` status enum, `ConstructPortfolioRequest` with and without account_id.

**Files to modify:**
- `app/config.py` -- Add `portfolio_freshness_warn_s: int = 86400` and `portfolio_theme_cache_ttl_s: int = 21600` to `Settings`.

**Depends on:** Nothing.

**Testable outcome:** `python -m pytest tests/test_portfolio_construction_models.py` passes. All models instantiate, serialize, deserialize. Config Settings loads new env vars with defaults.

---

### Step 2: PlatformClient Extensions and Data Loader

**What:** Add typed platform read methods for security universe, bulk fundamentals, and bulk price data. Build the DataLoader class that wraps these with Decimal-to-float conversion and freshness checking.

**Files to modify:**
- `app/models/platform_models.py` -- Add `SecuritySnapshot` (ticker, name, sector, industry, market_cap: Decimal, description, tags, freshness: FreshnessMeta), `FundamentalsV2` (ticker, pe_ratio, pb_ratio, roe, roa, debt_to_equity, revenue_growth, earnings_growth, dividend_yield, rnd_intensity, free_cash_flow_yield, current_ratio, gross_margin, operating_margin, net_margin, plus all fields required by the 6 factor sub-metrics, freshness: FreshnessMeta -- all numeric fields as `Decimal | None`), `PriceDataV2` (ticker, prices as list of date/close/volume, realized_vol_1y, beta, momentum_3m, momentum_6m, momentum_12m, freshness: FreshnessMeta -- Decimal at boundary).
- `app/services/platform_client.py` -- Add `get_security_universe(access_scope) -> list[SecuritySnapshot]`, `bulk_fundamentals(tickers, access_scope) -> list[FundamentalsV2]`, `bulk_price_data(tickers, access_scope) -> list[PriceDataV2]`. Each follows the existing `_cache_key` + `_get` + `model_validate` pattern.
- `tests/mocks/mock_platform_client.py` -- Add `set_security_universe()`, `set_bulk_fundamentals()`, `set_bulk_price_data()` and corresponding async mock methods with canned data for 20+ securities.

**Files to create:**
- `app/portfolio_construction/data_loader.py` -- `DataLoader` class: constructor takes `PlatformClient`, `AccessScope`, `Settings`. Methods: `load_universe()`, `load_fundamentals(tickers)`, `load_prices(tickers)`, `load_benchmark(benchmark_id)`. Each method calls PlatformClient, checks `FreshnessMeta.staleness_seconds` against `settings.portfolio_freshness_warn_s`, collects warnings, converts Decimal fields to float for downstream math. Returns typed dataclasses/dicts with float values plus a `warnings: list[str]` accumulator.
- `app/portfolio_construction/market_data_fallback.py` -- `MarketDataFallback` class: wraps yfinance calls, field-by-field merge with platform data (platform value wins when present), provenance tagging on each field (`source: "platform" | "yfinance"`), guarded by env check (disabled in production). Used only when platform data has missing fields.
- `tests/test_portfolio_data_loader.py` -- Test DataLoader with MockPlatformClient: data loads correctly, Decimal-to-float conversion verified (e.g., Decimal("123.45") becomes 123.45), freshness warning emitted when staleness exceeds threshold, no warning when fresh, fallback merge behavior (platform field wins, yfinance fills gaps, provenance correct).

**Depends on:** Step 1 (models.py for type references).

**Testable outcome:** `python -m pytest tests/test_portfolio_data_loader.py` passes. DataLoader returns float-based data from mock platform. Freshness warnings fire correctly.

---

### Step 3: Factor Model v2

**What:** Build the deterministic factor scoring model implementing all six canonical factors with hierarchical normalization, correlation-adjusted sub-factor aggregation, reliability shrinkage, breadth-sensitive caps, and weighted geometric mean.

**Files to create:**
- `app/analytics/portfolio_factor_model_v2.py` -- `PortfolioFactorModelV2` class with `ModelMetadata(name="portfolio_factor_model_v2", version="1.0.0", category=ModelCategory.PORTFOLIO, kind=ModelKind.DETERMINISTIC, ...)`. Six factor classes (Value, Quality, Growth, Momentum, LowVolatility, SizeLiquidity) each with docstring research card (economic rationale, applicable universes, coverage expectations, directional validation, known failure modes) and `known_limitations` tuple in metadata. `score(inputs: dict) -> dict` implements: (1) build peer buckets (industry >= 15, sector >= 25, fallback universe), (2) for each factor: extract raw sub-factor values, log-transform right-skewed metrics, winsorize at 5th/95th percentile within peer bucket, compute empirical percentile rank to [-1, 1], invert lower-is-better, (3) compute pairwise correlation among sub-factors, adjust weights via `adj_weight_i = base_weight_i / (1 + mean_abs_corr_i)`, normalize, (4) aggregate sub-factors per factor, (5) compute reliability from coverage/freshness/peer-size/critical-metric-presence, shrink via `final = 50 + reliability * (raw - 50)`, (6) apply breadth caps (1 sub-factor caps at 65, supportive share < 0.50 caps at 75), (7) runtime factor activation: deactivate if coverage < 0.60 or < 3 viable sub-factors, (8) weighted geometric mean across active factors using FactorPreferences weights, (9) emit activation report and universe_stats. Input keys: `securities`, `fundamentals`, `prices`, `preferences` (FactorPreferences). Output keys: `scores` (dict[ticker, FactorScoreResult-like dict]), `universe_stats` (coverage, active factors, effective weights, deactivated factors), `metadata` (model version, factor count, universe size).

**Files to modify:**
- `app/analytics/startup.py` -- Add `from app.analytics.portfolio_factor_model_v2 import PortfolioFactorModelV2` and `registry.register(PortfolioFactorModelV2())` inside `register_all_models()`.

**Files to create (test):**
- `tests/test_portfolio_factor_model_v2.py` -- Pure unit tests (no mocks): test percentile ranking produces [-1, 1] centered scores, test winsorization clips at 5th/95th, test peer bucket selection falls back correctly (industry -> sector -> universe), test correlation-adjusted weights sum to 1.0 and redundant metrics get lower weight, test reliability shrinkage moves scores toward 50 proportionally, test breadth cap at 65 when only 1 sub-factor, test breadth cap at 75 when supportive share < 0.50, test no cap when supportive share >= 0.70, test geometric mean produces expected output for known inputs, test factor deactivation when coverage < 0.60, test factor deactivation when < 3 sub-factors, test full `score()` with synthetic universe of 25 securities with known fundamentals/prices, test activation report populated with correct fields, test lower-is-better inversion (e.g., PE ratio), test missing sub-factors omitted not zero-filled.

**Depends on:** Step 1 (FactorPreferences, FactorScoreResult models), Step 2 (float-based data shapes from DataLoader).

**Testable outcome:** `python -m pytest tests/test_portfolio_factor_model_v2.py` passes. Factor model registered in analytics registry. Scores synthetic universe producing 0-100 scores. Activation report populated.

---

### Step 4: Recall Pool Builder

**What:** Build the two-stage recall pool that selects candidates for LLM theme scoring.

**Files to create:**
- `app/portfolio_construction/recall_pool.py` -- `build_recall_pool(intent: ParsedIntent, factor_scores: dict[str, FactorScoreResult], securities: list[SecuritySnapshot-like], fundamentals: dict) -> list[str]`: (1) take top N_factor (150) tickers by factor score descending, (2) keyword-match security metadata (name, description, sector, industry, tags) against intent themes to get N_metadata (100) additional tickers, (3) add all `intent.intent_constraints.include_tickers` (explicit user includes), (4) remove all `intent.intent_constraints.excluded_tickers`, (5) deduplicate, (6) cap at 250 by trimming lowest-factor-score metadata matches, (7) return sorted ticker list.
- `tests/test_portfolio_recall_pool.py` -- Test factor-top-N selects correct tickers (provide 200 scored securities, verify top 150 selected), test metadata keyword matching (securities with matching sector/name included even if low factor score), test include_tickers always in pool regardless of score, test excluded_tickers removed even if top factor score, test cap enforced at 250, test pool smaller than cap when universe is small, test deduplication (ticker in both factor and metadata sets counted once).

**Depends on:** Step 1 (ParsedIntent, IntentConstraints), Step 3 (factor score output shape).

**Testable outcome:** `python -m pytest tests/test_portfolio_recall_pool.py` passes. Recall pool contains expected tickers for synthetic inputs.

---

### Step 5: Theme Scorer Agent and Cache

**What:** Build the LLM theme scoring agent (batch tier) and the theme score cache layer.

**Files to create:**
- `app/portfolio_construction/agents/__init__.py` -- Imports all four agent modules to trigger registration side-effects.
- `app/portfolio_construction/agents/theme_scorer.py` -- `portfolio_theme_scorer` agent using `Agent[None, list[ThemeScoreResult]]` with `settings.batch_model` (`anthropic:claude-haiku-4-5`). System prompt teaches reasoning about: actual business exposure vs. name association, revenue mix and product reality, broad vs. specific sub-theme matching, multi-theme matching (multiple themes boost score), anti-goals as hard negatives (anti_goal_hit=true, score=0), uncertainty handling (low-confidence scores conservatively toward 40-50). Batches tickers (THEME_SCORE_BATCH_SIZE=15 per call, THEME_SCORE_CONCURRENCY_CAP=5 concurrent). Registered in `app.agents.registry` with `tier="batch"`, `name="portfolio_theme_scorer"`. Includes an `async score_themes(pool, intent, redis, settings)` orchestrating function that handles batching, concurrency, cache lookups, and result assembly.
- `app/portfolio_construction/cache.py` -- `ThemeScoreCache` class: `compute_key(themes, anti_goals, tickers, scorer_model, prompt_version, universe_snapshot_id) -> str` using `sha256(canonical_json(...))`. `get(key) -> list[ThemeScoreResult] | None` checks request-scoped dict first, then Redis. `set(key, scores)` writes to both. Constructor takes `redis` and `ttl_s`. Request-scoped cache is a plain dict passed in or defaulted.
- `app/portfolio_construction/prompts.py` -- `build_theme_scorer_prompt(themes, anti_goals, tickers, security_metadata) -> str` template function. Includes structured output format instructions matching `ThemeScoreResult` schema.
- `tests/test_portfolio_theme_scorer.py` -- Prompt-contract test: construct a sample LLM JSON output string matching theme scorer expected format, verify it parses into `list[ThemeScoreResult]`. Cache key determinism: same inputs produce same key, different inputs produce different key. Cache hit/miss: verify get returns None on miss, returns cached value on hit. Anti-goal representation: verify `anti_goal_hit=True` forces `score=0`.

**Depends on:** Step 1 (ThemeScoreResult model, ParsedIntent), Step 4 (recall pool provides ticker list).

**Testable outcome:** `python -m pytest tests/test_portfolio_theme_scorer.py` passes. Theme scorer agent registered in agent registry. Cache keys deterministic. Sample outputs parse.

---

### Step 6: Composite Scorer

**What:** Build the seven-step composite scoring pipeline that combines factor scores and theme scores into a final ranked list.

**Files to create:**
- `app/portfolio_construction/composite_scorer.py` -- `score_composite(factor_scores: dict[str, FactorScoreResult], theme_scores: dict[str, ThemeScoreResult], intent: ParsedIntent, params: dict | None = None) -> list[CompositeScoreResult]`: (1) hard exclusion: if ticker in excluded_tickers, gated with score=0, (2) anti-goal gate: if anti_goal_hit, gated with score=0, (3) eligibility gates: if factor_score < factor_floor (default 25), gated; if theme_score < min_theme_score (default 30), gated, (4) uncertainty adjustment: shrink low-confidence theme scores (confidence < theme_confidence_floor 0.50) toward 50, shrink low-reliability factor scores toward 50, (5) weighted geometric mean: `composite = factor_score^(1-theme_weight) * theme_score^theme_weight` (theme_weight default 0.60), (6) coherence bonus: if both factor >= 70 and theme >= 70, add interaction_bonus (5); weak-link penalty: if abs(factor - theme) >= weak_link_gap (35), subtract weak_link_penalty (5), (7) clamp to [0, 100]. Accept overrides from intent (speculative lowers factor_floor to 10-15, raises min_theme_score).
- `tests/test_portfolio_composite_scorer.py` -- Test each gate: excluded ticker scores 0 and is gated, anti_goal_hit ticker scores 0 and is gated, below-factor-floor ticker gated, below-theme-floor ticker gated. Test uncertainty adjustment shrinks toward 50. Test geometric mean math with known values (e.g., factor=80, theme=70, weight=0.60 -> expected value). Test coherence bonus: both >= 70 gets +5. Test weak-link penalty: gap >= 35 gets -5. Test no bonus and no penalty for moderate spread. Test final clamp (score computed above 100 clamped, below 0 clamped). Test speculative intent overrides (factor_floor lowered). Test ranking order matches descending composite score.

**Depends on:** Step 1 (CompositeScoreResult, ParsedIntent), Step 3 (factor score output), Step 5 (theme score output).

**Testable outcome:** `python -m pytest tests/test_portfolio_composite_scorer.py` passes. Composite scorer produces correct scores for synthetic inputs with hand-verified expected outputs.

---

### Step 7: Optimizer and Constraint Relaxation

**What:** Build candidate selection, four weighting strategies, position limit clamping, and auto-relaxation.

**Files to create:**
- `app/portfolio_construction/optimizer.py` -- `select_candidates(composite_scores: list[CompositeScoreResult], intent: ParsedIntent, securities_metadata: dict) -> tuple[list[str], list[str]]` returns (selected_tickers, relaxation_notes): apply exclusions, rank by composite descending, honor include_tickers (force-include at appropriate rank position), enforce max_sector_concentration, enforce target_count, backfill from deferred if under target. `auto_relax(composite_scores, intent, securities_metadata) -> tuple[list[str], list[str]]`: if insufficient candidates, relax in fixed order: (a) min_theme_score -5 per step, (b) max_beta +0.1, (c) max_sector_concentration +0.05, (d) reduce target_count by 5. Emit relaxation detail for each step applied. `weight_equal(tickers) -> dict[str, float]`, `weight_conviction(tickers, composite_scores) -> dict[str, float]` (proportional to scores, normalized), `weight_risk_parity(tickers, price_data) -> dict[str, float]` (inverse realized vol, sector-median imputation for missing vol), `weight_min_variance(tickers, price_data, composite_scores) -> dict[str, float]` (Ledoit-Wolf shrunk covariance via `sklearn.covariance.LedoitWolf`, score proxy lambda=0.10, falls back to risk_parity on solver failure). `clamp_positions(weights, min_weight=0.02, max_weight=0.10) -> dict[str, float]`: iterative clamping with redistribution, raise warning if infeasible (too many positions for min_weight budget).
- `app/portfolio_construction/account_aware.py` -- `compute_account_context(current_holdings: list, proposed_holdings: list[ProposedHolding], platform: PlatformClient, access_scope: AccessScope, account_id: str) -> dict`: read current holdings via platform, compute overlap (set intersection), estimated turnover (sum of absolute weight differences), drift (deviation from target), tax-sensitive warnings (positions with large unrealized gains). Returns dict with `overlap_pct`, `estimated_turnover`, `drift_summary`, `tax_warnings: list[str]`. Recommendation-only, no trades.
- `tests/test_portfolio_optimizer.py` -- Test equal weighting: N tickers each get 1/N, sums to 1.0. Test conviction weighting: higher composite scores get higher weights, sums to 1.0. Test risk_parity: lower vol gets higher weight, sums to 1.0, sector-median imputation fills missing vol. Test min_variance: runs without error on valid covariance matrix, sums to 1.0, falls back to risk_parity when solver raises. Test position clamping: no weight below 0.02 or above 0.10 after clamping, total still sums to 1.0. Test select_candidates: include_tickers always in result, excluded_tickers never in result, sector cap enforced (no sector > max_sector_concentration). Test auto_relax: when 0 candidates pass, relaxation applied in order (min_theme_score first), relaxation notes list populated with what was relaxed and by how much. Test account_aware: overlap computed correctly, turnover estimated, tax warning emitted for large gain positions.

**Depends on:** Step 1 (ProposedHolding, IntentConstraints, CompositeScoreResult), Step 6 (composite scores as input).

**Testable outcome:** `python -m pytest tests/test_portfolio_optimizer.py` passes. All weighting strategies produce valid weights summing to 1.0. Constraint relaxation follows fixed sequence. Position limits enforced.

---

### Step 8: Intent Parser, Rationale, and Critic Agents

**What:** Build the three copilot-tier LLM agents for intent parsing, rationale generation, and portfolio critique.

**Files to create:**
- `app/portfolio_construction/agents/intent_parser.py` -- `portfolio_intent_parser` agent: `Agent[AgentDeps, ParsedIntent]` with `settings.copilot_model`. System prompt covers: refine vague themes into specific investable themes, infer factor preferences from language (e.g., "value stocks" -> raise value weight), infer risk tolerance (e.g., "conservative" -> lower max_beta, higher min_market_cap, lower max_single_position), preserve explicit tickers and exclusions verbatim, emit ambiguity_flags when underspecified, coordinate theme_weight and max_sector_concentration, apply inference rules (large cap -> min_market_cap=10B, avoid meme stocks -> exclude high-vol/low-cap, conservative -> lower beta/higher quality, pure play -> raise min_theme_score, equal weight -> weighting_strategy=equal). Registered `tier="copilot"`, `name="portfolio_intent_parser"`.
- `app/portfolio_construction/agents/rationale.py` -- `portfolio_rationale` agent: `Agent[AgentDeps, PortfolioRationale]` with `settings.copilot_model`. System prompt: explain overall thesis in 2-3 sentences, per-holding justification (1-2 sentences each, referencing factor and theme signals), key factor signals driving the portfolio, classify each holding as core (primary theme exposure) or supporting (diversification/factor quality). Registered `tier="copilot"`, `name="portfolio_rationale"`.
- `app/portfolio_construction/agents/critic.py` -- `portfolio_critic` agent: `Agent[AgentDeps, CriticFeedback]` with `settings.copilot_model`. System prompt: review theme alignment (do holdings actually match themes?), anti-goal compliance (no anti-goal names present?), diversification (sector concentration acceptable?), factor coherence (do factor scores support the thesis?), obvious core name inclusion (are well-known names for the theme present?), account-aware turnover realism (if account mode, is turnover reasonable?). Output APPROVED or NEEDS_REVISION with structured adjustment fields. Hard rules: must not override user exclusions, must not force inclusion violating hard constraints, must not change universe or access scope. Registered `tier="copilot"`, `name="portfolio_critic"`.
- `tests/test_portfolio_agent_contracts.py` -- Prompt-contract tests: construct sample JSON strings matching each agent's expected output schema, verify parsing into `ParsedIntent`, `PortfolioRationale`, `CriticFeedback`. Test CriticFeedback hard rules: a helper function that applies critic adjustments must reject any adjustment that adds a user-excluded ticker or violates max_single_position. Test ParsedIntent default factor preferences normalize to 1.0. Test PortfolioRationale core_holdings and supporting_holdings are disjoint.

**Depends on:** Step 1 (ParsedIntent, PortfolioRationale, CriticFeedback models).

**Testable outcome:** `python -m pytest tests/test_portfolio_agent_contracts.py` passes. All three agents registered in agent registry (verified by importing agents package). Sample outputs parse correctly. Critic hard rules enforced.

---

### Step 9: Progress Events (Redis Streams)

**What:** Build the event emission and reading layer for job progress tracking.

**Files to create:**
- `app/portfolio_construction/events.py` -- `ProgressEventEmitter` class: constructor takes `redis: Redis`. `async emit(job_id: str, event_type: str, payload: dict | None = None)`: writes to Redis Stream key `sidecar:portfolio:events:{job_id}` via XADD with fields `v=1`, `job_id`, `event_type`, `timestamp` (ISO 8601), `payload` (JSON-serialized). Event types: `job_enqueued`, `intent_parsed`, `data_loaded`, `recall_pool_built`, `theme_scoring_started`, `theme_scoring_completed`, `review_iteration_started`, `draft_built`, `critic_verdict`, `job_completed`, `job_failed`. `async read_events(job_id: str, last_id: str = "0-0") -> list[dict]`: reads via XREAD from stream key, returns list of event dicts. `async get_job_status(job_id: str) -> str | None`: reads latest event to determine current status.
- `tests/test_portfolio_construction_events.py` -- Test with mock Redis (AsyncMock) that records XADD calls: verify emit produces correct stream key and field schema (v, job_id, event_type, timestamp present). Test read_events returns events in order. Test get_job_status returns latest event type. Test all defined event types are valid strings.

**Depends on:** Nothing (uses only Redis and stdlib).

**Testable outcome:** `python -m pytest tests/test_portfolio_construction_events.py` passes. Events emit with correct schema, read back in order.

---

### Step 10: Orchestrator (Pipeline Class)

**What:** Wire all stages together into the `PortfolioConstructionPipeline` class with the review loop.

**Files to create:**
- `app/portfolio_construction/orchestrator.py` -- `PortfolioConstructionPipeline` class. Constructor: `platform: PlatformClient`, `redis: Redis`, `access_scope: AccessScope`, `tracer: JobTracer | None = None`, `settings: Settings | None = None`. Internal setup: `ProgressEventEmitter(redis)`, `ThemeScoreCache(redis, ttl_s)`, `DataLoader(platform, access_scope, settings)`. `async run(request: ConstructPortfolioRequest, job_id: str) -> ConstructPortfolioResponse` executes: (1) `_parse_intent(request)` -> emit `intent_parsed`, (2) `_load_data(intent)` -> emit `data_loaded`, (3) `_score_factors(data, intent.factor_preferences)` -> factor scores, (4) `_build_recall_pool(intent, factor_scores, data)` -> emit `recall_pool_built`, (5) `_score_themes(pool, intent)` -> emit `theme_scoring_started` / `theme_scoring_completed`, (6) `_review_loop(intent, data, factor_scores, theme_scores, request)` which iterates up to 3 times: compute composite scores, select candidates, optimize weights (dispatch to correct strategy), build ProposedHoldings, generate rationale, run critic -> emit `review_iteration_started`, `draft_built`, `critic_verdict`. If APPROVED, break. If still NEEDS_REVISION after 3, use best-effort result with manager warning. (7) If `account_refresh` mode (account_id present and readable), call `compute_account_context()`. (8) Assemble `ConstructPortfolioResponse` with all fields. (9) Emit `job_completed`. Theme scores reused across review iterations unless theme list or recall pool changes per critic feedback.

**Files to create (test):**
- `tests/test_portfolio_construction_orchestrator.py` -- Integration test: instantiate `PortfolioConstructionPipeline` with `MockPlatformClient` (canned data for 30 securities) and patched agents (intent_parser returns canned ParsedIntent, theme_scorer returns canned ThemeScoreResults, rationale returns canned PortfolioRationale, critic returns APPROVED on first iteration). Verify: (a) all progress events emitted in correct order (intent_parsed, data_loaded, recall_pool_built, theme_scoring_started, theme_scoring_completed, review_iteration_started, draft_built, critic_verdict, job_completed), (b) final response contains parsed_intent, proposed_holdings (non-empty, weights sum to ~1.0), score_breakdowns, rationale, warnings list, metadata. Test review loop: patch critic to return NEEDS_REVISION twice then APPROVED -- verify 3 iterations, verify theme scores reused (theme_scorer called once not three times). Test best-effort: patch critic to always return NEEDS_REVISION -- verify 3 iterations then response includes manager warning. Test account_refresh mode: provide account_id, verify account_aware fields populated. Test idea mode: no account_id, verify no account_aware fields.

**Depends on:** Steps 1-9 (all components).

**Testable outcome:** `python -m pytest tests/test_portfolio_construction_orchestrator.py` passes. Full pipeline runs end-to-end with mocked dependencies. Review loop terminates correctly. Events emitted in order.

---

### Step 11: ARQ Job and Enqueue Helper

**What:** Wire the pipeline into the ARQ job system so it can be enqueued from the router.

**Files to create:**
- `app/jobs/portfolio_construction.py` -- `run_portfolio_construction(ctx: dict, job_ctx_raw: dict | None = None, request_raw: dict | None = None) -> dict`: decorated with `@with_retry_policy`. Parse `JobContext` from `job_ctx_raw`, parse `ConstructPortfolioRequest` from `request_raw`, reconstruct `AccessScope` from `job_ctx.access_scope`. Create `JobTracer` if langfuse available. Create `PortfolioConstructionPipeline(platform=ctx["platform_client"], redis=ctx["redis"], access_scope=access_scope, tracer=tracer, settings=ctx.get("settings"))`. Call `pipeline.run(request, job_id=ctx["job_id"])`. Persist final result to Redis key `sidecar:portfolio:result:{job_id}` with TTL (1 hour). Return `result.model_dump()`. On failure: `tracer.fail(exc)`, emit `job_failed` event, re-raise.

**Files to modify:**
- `app/jobs/enqueue.py` -- Add `async def enqueue_portfolio_construction(job_ctx: JobContext, request: ConstructPortfolioRequest) -> str`: calls `pool.enqueue_job("run_portfolio_construction", job_ctx.model_dump(), request.model_dump())`, returns `job.job_id`.
- `app/jobs/worker.py` -- Import `run_portfolio_construction` from `app.jobs.portfolio_construction`. Add `func(with_retry_policy(run_portfolio_construction), name="run_portfolio_construction")` to `WorkerSettings.functions`.

**Files to create (test):**
- `tests/test_portfolio_construction_enqueue.py` -- Test `ConstructPortfolioRequest` serialization round-trip through model_dump/model_validate. Test `JobContext` carries access_scope dict correctly. Test that `run_portfolio_construction` is importable (no import errors). Test that worker.py WorkerSettings.functions includes `"run_portfolio_construction"` by name.

**Depends on:** Step 1 (request/response models), Step 10 (orchestrator).

**Testable outcome:** `python -m pytest tests/test_portfolio_construction_enqueue.py` passes. Job function importable. Worker settings include the function. Enqueue helper produces correct args.

---

### Step 12: Router Endpoints

**What:** Expose the three API endpoints for portfolio construction.

**Files to modify:**
- `app/routers/portfolio.py` -- Add three endpoints: (1) `POST /portfolio/construct` accepting `ConstructPortfolioRequest`, validating scope, enqueuing job via `enqueue_portfolio_construction()`, returning 202 with `PortfolioConstructionAccepted(job_id=...)`. (2) `GET /portfolio/jobs/{job_id}` fetching job status from `ProgressEventEmitter.get_job_status()` and optionally the final result from Redis key `sidecar:portfolio:result:{job_id}`, returning status and payload. (3) `GET /portfolio/jobs/{job_id}/events` as SSE endpoint using `ProgressEventEmitter.read_events()` with long-polling/streaming, returning `text/event-stream` content type.

**Files to create (test):**
- `tests/test_portfolio_construction_router.py` -- TestClient tests with mocked `app.state` (redis=AsyncMock, platform_client=AsyncMock, langfuse=MagicMock, settings=MagicMock): (a) POST `/ai/portfolio/construct` with valid body and headers returns 202 with `job_id` field, (b) POST without required headers returns 422, (c) GET `/ai/portfolio/jobs/{job_id}` returns job status dict, (d) GET `/ai/portfolio/jobs/{job_id}/events` returns SSE content type, (e) scope propagation: verify `JobContext` built from request headers contains correct tenant_id, actor_id.

**Depends on:** Step 1 (request/response models), Step 9 (events), Step 11 (enqueue helper).

**Testable outcome:** `python -m pytest tests/test_portfolio_construction_router.py` passes. All three endpoints respond with correct status codes. Construct returns 202.

---

### Step 13: Shadow-Mode Test Harness

**What:** Build the regression comparison infrastructure for Phase 1 rollout (v2 vs. mandate-py baseline).

**Files to create:**
- `tests/fixtures/portfolio_construction_prompts.json` -- 10-15 curated prompt fixtures, each containing: `prompt` (user message string), `expected_themes` (themes mandate-py would extract), `expected_core_holdings` (tickers mandate-py would select as core), `expected_sector_distribution` (sector -> approximate weight), `expected_holding_count_range` ([min, max]), `notes` (explanation of what the prompt tests). Prompts should cover: simple theme ("AI stocks"), multi-theme ("clean energy and healthcare innovation"), anti-goal ("tech stocks but avoid social media"), speculative ("high growth biotech"), conservative ("dividend aristocrats for retirement"), specific tickers ("build around AAPL and MSFT"), sector concentration ("focused semiconductor portfolio"), equal weight request, account-aware (with account_id), vague/ambiguous ("something interesting").
- `tests/test_portfolio_shadow_mode.py` -- `TestShadowMode` class: loads fixtures from JSON, for each fixture: instantiate `PortfolioConstructionPipeline` with `MockPlatformClient` (canned universe of 100+ securities), patch agents to return plausible canned outputs (intent parser guided by fixture expected_themes, theme scorer returns scores matching fixture intent). Execute pipeline. Compare v2 output vs. mandate-py baseline: holdings overlap percentage (how many expected_core_holdings appear in v2 result), sector concentration comparison (max sector weight within tolerance), holding count within expected range, obvious core name presence check. Produce structured comparison report as test output (printed or saved). Tests are marked with `@pytest.mark.shadow` so they can be run separately. These tests do not assert pass/fail on comparison metrics (since divergence from mandate-py is expected during development) -- they assert only that the pipeline completes without error and produces valid output for each prompt.

**Depends on:** Step 10 (orchestrator), all prior steps.

**Testable outcome:** `python -m pytest tests/test_portfolio_shadow_mode.py -m shadow` passes. Pipeline runs on all fixture prompts without error. Comparison report generated.

## External Dependencies

| Package | Version Constraint | Purpose | Already in Project? |
|---------|-------------------|---------|---------------------|
| `pydantic` | >=2.0 | Domain models, request/response schemas | Yes |
| `pydantic-ai` | >=0.1 | LLM agent framework (all 4 agents) | Yes |
| `fastapi` | >=0.100 | Router endpoints, dependency injection | Yes |
| `redis` (async) | >=5.0 | Job state, theme score cache, progress event streams (Redis Streams XADD/XREAD) | Yes |
| `arq` | >=0.25 | Async job execution for portfolio construction job | Yes |
| `numpy` | >=1.24 | Factor math: percentile ranking, correlation matrix, array operations | Yes (transitive) |
| `scikit-learn` | >=1.3 | `sklearn.covariance.LedoitWolf` for min-variance optimizer covariance shrinkage | Yes (transitive) |
| `scipy` | >=1.10 | `scipy.optimize.minimize` for min-variance portfolio optimization | Yes (transitive) |
| `yfinance` | >=0.2 | Dev-time fallback market data adapter (disabled in production) | No -- add to dev dependencies |
| `langfuse` | >=2.0 | Observability: job tracing, generation tracking, cost tracking | Yes |
| `httpx` | >=0.24 | PlatformClient HTTP calls (existing client) | Yes |

## Test Cases

### Unit Tests (no mocks, pure math/logic)

| Test File | What It Validates |
|-----------|-------------------|
| `tests/test_portfolio_construction_models.py` | All Pydantic models construct, serialize, deserialize. FactorPreferences normalizes to 1.0. IntentConstraints optional fields. CriticFeedback status enum. Request with/without account_id. |
| `tests/test_portfolio_factor_model_v2.py` | Percentile ranking [-1,1]. Winsorization at 5th/95th. Peer bucket fallback (industry->sector->universe). Correlation-adjusted weights sum to 1.0. Reliability shrinkage toward 50. Breadth caps (1 sub-factor->65, low support->75). Geometric mean correctness. Factor deactivation (<0.60 coverage, <3 sub-factors). Full score() on 25-security synthetic universe. Activation report fields. Lower-is-better inversion. Missing sub-factors omitted not zero-filled. |
| `tests/test_portfolio_recall_pool.py` | Factor-top-N selection. Metadata keyword matching. include_tickers honored. excluded_tickers removed. Cap at 250. Small universe. Deduplication. |
| `tests/test_portfolio_composite_scorer.py` | Each gate independently (exclusion, anti-goal, factor floor, theme floor). Uncertainty adjustment. Geometric mean math. Coherence bonus (both>=70 -> +5). Weak-link penalty (gap>=35 -> -5). Clamp [0,100]. Speculative overrides. Ranking order. |
| `tests/test_portfolio_optimizer.py` | Equal weighting sums to 1.0. Conviction proportional to scores. Risk parity uses inverse vol. Min variance fallback to risk parity. Position clamping (0.02-0.10, sum=1.0). Candidate selection with include/exclude. Sector cap enforcement. Auto-relax fixed sequence. Account context computation. |

### Contract Tests (schema validation, no LLM calls)

| Test File | What It Validates |
|-----------|-------------------|
| `tests/test_portfolio_theme_scorer.py` | Sample LLM output parses into `list[ThemeScoreResult]`. Cache key determinism. Cache hit/miss behavior. Anti-goal forces score=0. |
| `tests/test_portfolio_agent_contracts.py` | Sample outputs parse into `ParsedIntent`, `PortfolioRationale`, `CriticFeedback`. Critic hard rules enforced (no overriding exclusions, no constraint-violating inclusions). ParsedIntent defaults normalize. Rationale core/supporting disjoint. |

### Integration Tests (mocked dependencies)

| Test File | What It Validates |
|-----------|-------------------|
| `tests/test_portfolio_data_loader.py` | DataLoader with MockPlatformClient. Decimal-to-float conversion. Freshness warnings fire/don't fire. Fallback merge provenance. |
| `tests/test_portfolio_construction_events.py` | Event emit schema (v, job_id, event_type, timestamp). Read returns in order. get_job_status returns latest. |
| `tests/test_portfolio_construction_orchestrator.py` | Full pipeline with mocked agents and MockPlatformClient. Events emitted in order. Review loop: APPROVED on first try, NEEDS_REVISION then APPROVED, best-effort after 3 failures. Theme score reuse across iterations. Account_refresh vs. idea mode. Response completeness. |
| `tests/test_portfolio_construction_enqueue.py` | Request serialization round-trip. JobContext access_scope. Job function importable. Worker settings include function. |
| `tests/test_portfolio_construction_router.py` | POST construct returns 202. Missing headers return 422. GET status returns dict. GET events returns SSE. Scope propagation. |

### Shadow-Mode Tests (comparison, non-blocking)

| Test File | What It Validates |
|-----------|-------------------|
| `tests/test_portfolio_shadow_mode.py` | Pipeline completes without error on 10-15 curated prompts. Output is structurally valid. Comparison metrics generated (holdings overlap, sector distribution, holding count). Marked `@pytest.mark.shadow` for separate execution. |

## Scope Boundaries

### In Scope

- All code under `app/portfolio_construction/` (new package).
- `app/analytics/portfolio_factor_model_v2.py` (new file).
- `app/jobs/portfolio_construction.py` (new file).
- Modifications to `app/analytics/startup.py` (register factor model).
- Modifications to `app/services/platform_client.py` (3 new typed methods).
- Modifications to `app/models/platform_models.py` (3 new response models).
- Modifications to `app/routers/portfolio.py` (3 new endpoints).
- Modifications to `app/jobs/enqueue.py` (1 new enqueue helper).
- Modifications to `app/jobs/worker.py` (1 new function registration).
- Modifications to `app/config.py` (2 new settings).
- Modifications to `tests/mocks/mock_platform_client.py` (mock methods for new platform calls).
- All test files listed above.
- US equities only.

### Out of Scope -- Do NOT Implement

- **No order creation or trade execution.** The pipeline produces a proposed portfolio, never an executable order. No OMS writes, no workflow advancement, no rebalancing.
- **No multi-asset or international equities.** Only US equities. No ADR-specific handling, no fixed income, no alternatives.
- **No transaction-cost or market-impact modeling.** The optimizer uses simple weighting strategies, not cost-aware optimization.
- **No benchmark-relative optimization.** No tracking error minimization, no benchmark-aware objective function.
- **No lot-level tax-aware optimization.** Account-aware mode computes warnings only, not tax-optimal lot selection.
- **No modifications to existing agents.** Do not change `portfolio_analyst`, `copilot`, or any other existing agent.
- **No modifications to existing analytics models.** Do not change `drift_detection`, `concentration_risk`, or any other existing model.
- **No modifications to `app/main.py`.** The portfolio router is already mounted; no additional wiring needed.
- **No new Redis infrastructure.** Use existing Redis instance. Redis Streams (XADD/XREAD) are available in Redis 5+ which the project already requires.
- **No new middleware or auth.** Use existing `TenantContextMiddleware` and `AccessScope`. No additional auth layer.
- **No UI or frontend changes.** API-only.
- **No changes to the Go api-server.** PlatformClient calls existing api-server endpoints. If new api-server endpoints are needed, that is a separate feature.
- **No production deployment configuration.** No Dockerfile changes, no Kubernetes manifests, no CI/CD pipeline changes.
- **No feature flags implementation.** Shadow mode and rollout phases are managed via the test harness and manual configuration, not a feature flag system.
- **yfinance fallback is dev-only.** Must be disabled in production via environment check. Must never overwrite platform values -- field-by-field merge with provenance only.
