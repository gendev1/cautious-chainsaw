# Architecture: portfolio-construction-v2

## Approach A: Flat Module with Function-Based Orchestrator

### Structure

The `portfolio_construction/` package is flat -- all modules live at the top level with no sub-packages. The orchestrator is a single async function (`run_pipeline`) that calls sub-components sequentially. Agents are defined inline in their own module files within `portfolio_construction/` rather than under `app/agents/`.

```text
app/portfolio_construction/
  __init__.py
  config.py              # theme-factor priors, factor metadata, defaults
  models.py              # all Pydantic models (ParsedIntent, ThemeScoreResult, etc.)
  orchestrator.py        # run_pipeline() async function
  data_loader.py         # typed platform reads + freshness checks
  market_data_fallback.py # yfinance dev fallback
  recall_pool.py         # two-stage recall pool builder
  composite_scorer.py    # seven-step composite scoring
  optimizer.py           # candidate selection + four weighting strategies
  account_aware.py       # account-mode overlap/turnover/drift
  events.py              # Redis Streams event emission
  cache.py               # theme score cache helpers
  prompts.py             # prompt templates as string constants
  intent_parser.py       # Agent: portfolio_intent_parser
  theme_scorer.py        # Agent: portfolio_theme_scorer
  rationale.py           # Agent: portfolio_rationale
  critic.py              # Agent: portfolio_critic
app/analytics/portfolio_factor_model_v2.py
app/jobs/portfolio_construction.py   # ARQ job entry point (thin)
```

### Orchestrator Design

The orchestrator is a single async function that accepts a typed context object and returns the final proposal. It calls each stage directly -- no class instantiation, no dependency injection beyond what is passed in the argument.

```python
# orchestrator.py
async def run_pipeline(
    request: ConstructPortfolioRequest,
    platform: PlatformClient,
    redis: Redis,
    access_scope: AccessScope,
    tracer: JobTracer | None,
    emit_event: Callable,
) -> ConstructPortfolioResponse:
    intent = await parse_intent(request.message, ...)
    await emit_event("intent_parsed", {...})
    data = await load_data(intent, platform, access_scope)
    await emit_event("data_loaded", {...})
    factor_scores = factor_model.score({...})
    pool = build_recall_pool(intent, factor_scores, data.securities)
    await emit_event("recall_pool_built", {...})
    theme_scores = await score_themes(pool, intent, redis, ...)
    # ... review loop ...
    return response
```

### Agent Wiring

Each agent is defined in its own module file inside `portfolio_construction/`. They follow the existing agent pattern (Pydantic AI `Agent` with typed output), but registration happens at module level in each file by importing from `app.agents.registry`. The agents do not use `AgentDeps` (they run inside a job, not a request) -- instead they use `Agent[None, OutputType]` with all context passed via the prompt string, matching the `meeting_summary` pattern.

### Factor Model

Standard analytics model at `app/analytics/portfolio_factor_model_v2.py` following `ConcentrationRiskScorer` pattern. `score()` takes `dict[str, Any]`, returns `dict[str, Any]`. Registered in `startup.py` via `register_all_models()`.

### ARQ Job

Thin wrapper in `app/jobs/portfolio_construction.py` that parses `JobContext`, extracts deps from `ctx` dict, creates the event emitter closure, and delegates to `run_pipeline()`. Follows `run_meeting_summary` pattern exactly.

### PlatformClient

Three new typed methods added directly to `PlatformClient`: `get_security_universe()`, `bulk_fundamentals()`, `bulk_price_data()`. Existing `get_benchmark_data()` extended with an optional `benchmark_id` parameter. Each follows the established `_cache_key` + `_get` + `model_validate` pattern.

### Testing

Flat test files at `tests/test_portfolio_*.py`. Factor math tests are pure unit tests (no mocks). Orchestrator test mocks all agents and platform client. Router test uses `TestClient` with mocked `app.state`. Prompt-contract tests validate agent output schemas against sample LLM responses.

### Deviations from Existing Patterns

- **Agents outside `app/agents/`**: The four portfolio agents live in `portfolio_construction/` rather than `app/agents/`. This co-locates domain logic but breaks the convention that all agents live in `app/agents/`. Registration still uses the central `app.agents.registry`.
- **No AgentDeps**: Job-context agents use `Agent[None, OutputType]` with prompt-only context, matching the `meeting_summary` inline agent pattern rather than the `portfolio_analyst` tool-equipped pattern.
- **Function-based orchestrator**: No class or protocol -- just a function. This is simpler but harder to test in isolation because each stage is a direct function call with no seam for stubbing intermediate results.

### Pros

- Simplest structure, fewest files, fewest abstractions.
- Easy to read top-to-bottom -- the orchestrator function IS the pipeline documentation.
- No new patterns introduced beyond what `meeting_summary` already does.

### Cons

- The orchestrator function will grow large (~200+ lines) and become hard to test individual stages.
- No clean seam between stages for mocking or independent testing.
- Agent co-location in `portfolio_construction/` breaks the established convention. If future features need similar multi-agent pipelines, there is no reusable pattern.
- The review loop (up to 3 iterations with critic feedback application) inside a flat function gets deeply nested.

---

## Approach B: Layered Module with Class-Based Orchestrator and Central Agent Registration

### Structure

The `portfolio_construction/` package uses a single `agents/` sub-package to group the four agents. The orchestrator is a class whose constructor receives all dependencies and whose `run()` method executes the pipeline. Agents are defined in the sub-package but registered in `app/agents/registry` at import time. The factor model stays in `app/analytics/`.

```text
app/portfolio_construction/
  __init__.py
  config.py              # theme-factor priors, factor metadata, defaults
  models.py              # all Pydantic models
  orchestrator.py        # PortfolioConstructionPipeline class
  data_loader.py         # DataLoader class: typed platform reads + freshness
  market_data_fallback.py # yfinance dev fallback adapter
  recall_pool.py         # build_recall_pool() function
  composite_scorer.py    # score_composite() function
  optimizer.py           # select_candidates() + weight functions
  account_aware.py       # compute_account_context() function
  events.py              # ProgressEventEmitter class (Redis Streams)
  cache.py               # ThemeScoreCache class
  prompts.py             # prompt template functions
  agents/
    __init__.py          # imports all four agents for registration side-effect
    intent_parser.py     # portfolio_intent_parser agent
    theme_scorer.py      # portfolio_theme_scorer agent
    rationale.py         # portfolio_rationale agent
    critic.py            # portfolio_critic agent
app/analytics/portfolio_factor_model_v2.py
app/jobs/portfolio_construction.py
```

### Orchestrator Design

The orchestrator is a class that receives dependencies at construction time and exposes a single `async run()` method. Each pipeline stage is a private method on the class, making stages independently testable by calling them directly in tests.

```python
# orchestrator.py
class PortfolioConstructionPipeline:
    def __init__(
        self,
        platform: PlatformClient,
        redis: Redis,
        access_scope: AccessScope,
        tracer: JobTracer | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._platform = platform
        self._redis = redis
        self._scope = access_scope
        self._tracer = tracer
        self._settings = settings or get_settings()
        self._emitter = ProgressEventEmitter(redis)
        self._cache = ThemeScoreCache(redis, ttl_s=self._settings.portfolio_theme_cache_ttl_s)
        self._loader = DataLoader(platform, access_scope, self._settings)

    async def run(self, request: ConstructPortfolioRequest, job_id: str) -> ConstructPortfolioResponse:
        intent = await self._parse_intent(request)
        await self._emitter.emit(job_id, "intent_parsed", {...})
        data = await self._load_data(intent)
        await self._emitter.emit(job_id, "data_loaded", {...})
        # ... stages as private methods ...

    async def _parse_intent(self, request: ConstructPortfolioRequest) -> ParsedIntent:
        ...

    async def _score_factors(self, data: UniverseData, preferences: FactorPreferences) -> dict[str, FactorScoreResult]:
        ...

    async def _build_recall_pool(self, intent: ParsedIntent, factor_scores: ..., securities: ...) -> list[str]:
        ...

    async def _score_themes(self, pool: list[str], intent: ParsedIntent, ...) -> list[ThemeScoreResult]:
        ...

    async def _review_loop(self, ...) -> tuple[list[ProposedHolding], PortfolioRationale, list[str]]:
        ...
```

### Agent Wiring

Agents live in `portfolio_construction/agents/` but register in `app.agents.registry` at import time (same as `portfolio_analyst.py`). The theme scorer uses `Agent[None, list[ThemeScoreResult]]` with `settings.batch_model`. The copilot-tier agents use `Agent[AgentDeps, OutputType]` with `settings.copilot_model`. The agents module `__init__.py` imports all four to trigger registration as a side-effect.

To ensure registration happens, `app/jobs/portfolio_construction.py` imports from `app.portfolio_construction.agents` at the top level.

The intent parser, rationale, and critic use `AgentDeps` because they may benefit from platform data access via tools in future iterations. The theme scorer uses `Agent[None, ...]` because it is a pure classification task.

### Factor Model

Same as Approach A: standard `AnalyticalModel` at `app/analytics/portfolio_factor_model_v2.py`.

### ARQ Job

Thin wrapper at `app/jobs/portfolio_construction.py`. Creates a `PortfolioConstructionPipeline` instance with deps from the worker `ctx` dict, then calls `pipeline.run(request, job_id)`. Follows `run_meeting_summary` shape but delegates to the pipeline class.

```python
@with_retry_policy
async def run_portfolio_construction(
    ctx: dict[str, Any],
    job_ctx_raw: dict | None = None,
    request_raw: dict | None = None,
) -> dict:
    job_ctx = JobContext(**job_ctx_raw)
    request = ConstructPortfolioRequest(**request_raw)
    access_scope = AccessScope(**job_ctx.access_scope)

    pipeline = PortfolioConstructionPipeline(
        platform=ctx["platform_client"],
        redis=ctx["redis"],
        access_scope=access_scope,
        tracer=JobTracer(...) if ctx.get("langfuse") else None,
        settings=ctx.get("settings"),
    )
    result = await pipeline.run(request, job_id=...)
    return result.model_dump()
```

### PlatformClient

Same as Approach A. Three new typed methods added to `PlatformClient`. Additionally, a `DataLoader` class in `portfolio_construction/data_loader.py` wraps the raw platform calls with freshness checking, Decimal-to-float conversion, and fallback merging logic. This keeps the conversion layer explicit and out of `PlatformClient` itself.

### Progress Events

`ProgressEventEmitter` class wraps Redis Streams XADD. Each event carries `v: 1`, `job_id`, `event_type`, `timestamp`, and a typed payload. The `GET /portfolio/jobs/{job_id}/events` endpoint uses XREAD on the stream. The emitter is injected into the pipeline class at construction time.

### Testing

- **Factor model unit tests** (`tests/test_portfolio_factor_model_v2.py`): Pure math, no mocks. Test normalization, correlation adjustment, reliability shrinkage, breadth caps, geometric mean, factor deactivation.
- **Composite scorer tests** (`tests/test_portfolio_composite_scorer.py`): Pure math. Test each of the 7 gating steps, parameter overrides, edge cases.
- **Optimizer tests** (`tests/test_portfolio_optimizer.py`): Test each weighting strategy, position limit clamping, constraint relaxation sequence.
- **Recall pool tests** (`tests/test_portfolio_recall_pool.py`): Test factor-top-N, metadata matching, cap enforcement.
- **Model validation tests** (`tests/test_portfolio_construction_models.py`): Pydantic model construction, serialization round-trip.
- **Orchestrator integration tests** (`tests/test_portfolio_construction_orchestrator.py`): Instantiate `PortfolioConstructionPipeline` with `MockPlatformClient` and patched agents. Verify end-to-end flow and event emission. Test critic review loop with mocked critic returning NEEDS_REVISION then APPROVED.
- **Router tests** (`tests/test_portfolio_construction_router.py`): `TestClient` with mocked `app.state`. Verify 202 response from construct endpoint, job status polling, SSE event streaming.
- **Prompt-contract tests** (`tests/test_portfolio_agent_contracts.py`): Validate that sample LLM output strings parse into the expected Pydantic output types. Verifies the schema contract between prompts and agent output types without calling an LLM.

### Deviations from Existing Patterns

- **Sub-package within `portfolio_construction/`**: The `agents/` sub-directory is new. No other feature has a sub-package for agents. However, four agents in a single feature justifies the grouping.
- **Class-based orchestrator**: No existing job uses a class-based pipeline. `run_meeting_summary` is a flat function. The class is warranted by the number of stages, the review loop, and the need for shared state (cache, emitter, loader).
- **DataLoader wrapper**: Adds a thin data access layer between the orchestrator and PlatformClient. This is new -- existing jobs call PlatformClient directly. The wrapper handles Decimal-to-float conversion and freshness checking, keeping that concern out of both PlatformClient and the orchestrator.
- **ProgressEventEmitter**: No existing feature uses Redis Streams for progress events. This is net-new infrastructure within the module.

### Pros

- Clean stage isolation: each pipeline stage is a private method, independently callable in tests.
- Constructor injection makes it straightforward to substitute mocks for integration tests.
- The agents sub-package keeps four agent definitions tidy without polluting the top-level `app/agents/` directory.
- The `DataLoader` class encapsulates the Decimal-to-float boundary and freshness checking, preventing it from leaking into business logic.
- The review loop is clean: `_review_loop` is its own method with clear inputs and outputs.

### Cons

- More files and more abstraction than Approach A.
- The class-based orchestrator is a new pattern -- developers must learn it.
- Risk of the pipeline class becoming a god object if discipline is not maintained.

---

## Approach C: Layered Module with Protocol-Based Stage Abstraction

### Structure

Same directory layout as Approach B, but each pipeline stage is formalized as a Protocol (Python `typing.Protocol`), and the orchestrator is generic over stage implementations. This allows swapping stage implementations for testing, shadow-mode comparison, or gradual migration.

```text
app/portfolio_construction/
  __init__.py
  config.py
  models.py
  protocols.py           # Stage protocols: IntentParser, ThemeScorer, FactorScorer, etc.
  orchestrator.py        # PipelineOrchestrator parameterized by protocols
  stages/
    __init__.py
    intent_parser.py     # LLMIntentParser (implements IntentParser protocol)
    theme_scorer.py      # LLMThemeScorer (implements ThemeScorer protocol)
    factor_scorer.py     # RegistryFactorScorer (wraps analytics model)
    rationale.py         # LLMRationaleGenerator
    critic.py            # LLMCritic
    recall_pool.py       # DefaultRecallPoolBuilder
    composite_scorer.py  # DefaultCompositeScorer
    optimizer.py         # DefaultOptimizer
    account_aware.py     # DefaultAccountAnalyzer
  data_loader.py
  market_data_fallback.py
  events.py
  cache.py
  prompts.py
app/analytics/portfolio_factor_model_v2.py
app/jobs/portfolio_construction.py
```

### Orchestrator Design

```python
# protocols.py
class IntentParser(Protocol):
    async def parse(self, message: str, ...) -> ParsedIntent: ...

class ThemeScorer(Protocol):
    async def score(self, tickers: list[str], themes: list[str], ...) -> list[ThemeScoreResult]: ...

class CompositeScorer(Protocol):
    def score(self, factor_scores: ..., theme_scores: ..., ...) -> list[CompositeScoreResult]: ...

# orchestrator.py
class PipelineOrchestrator:
    def __init__(
        self,
        intent_parser: IntentParser,
        theme_scorer: ThemeScorer,
        factor_scorer: FactorScorer,
        composite_scorer: CompositeScorer,
        optimizer: Optimizer,
        rationale_generator: RationaleGenerator,
        critic: Critic,
        event_emitter: EventEmitter,
        data_loader: DataLoader,
    ) -> None:
        ...
```

### Agent Wiring

Same as Approach B, but the LLM agents are wrapped in protocol-implementing classes that the orchestrator consumes. This adds a translation layer: the orchestrator calls `self._intent_parser.parse(...)`, which internally calls the Pydantic AI agent's `run()` method.

### Testing

Protocol-based stages are trivially mockable -- just provide a `Mock` that satisfies the protocol. No need to patch imports. Shadow-mode testing is clean: instantiate the orchestrator with a `MandatePyFactorScorer` alongside the v2 scorer to compare.

### Deviations from Existing Patterns

- **Protocol-based composition**: No existing feature in the codebase uses protocols for stage abstraction. This is a significantly new pattern.
- **Wrapper classes around agents**: Adds an indirection layer between the orchestrator and the Pydantic AI agents. Every agent invocation goes through a protocol wrapper.
- **`stages/` sub-package**: Deeper nesting than anything in the current codebase.

### Pros

- Maximum testability and substitutability.
- Clean shadow-mode comparison: swap in mandate-py implementations of each protocol.
- Each stage is fully decoupled, enabling parallel development by different engineers.

### Cons

- Heaviest abstraction. Over-engineers a first implementation.
- Protocol wrappers around Pydantic AI agents add boilerplate with no near-term payoff.
- Deeper directory nesting conflicts with the flat style of the existing codebase.
- Shadow-mode comparison only needs a few specific stages swapped, not full protocol polymorphism.
- Risk of premature abstraction: the protocols constrain stage interfaces before the implementation has proven which interfaces are actually stable.

---

## Recommendation

**Approach B: Layered Module with Class-Based Orchestrator and Central Agent Registration.**

Rationale:

1. **Right level of abstraction for the complexity.** This feature has 10+ pipeline stages, a review loop with up to 3 iterations, 4 LLM agents, 1 deterministic model, Redis Streams event emission, and theme score caching. A flat function (Approach A) cannot absorb this without becoming unmaintainable. Full protocol abstraction (Approach C) over-engineers a first implementation with no proven need for stage substitutability.

2. **Testability without boilerplate.** The class-based orchestrator gives each stage a private method that can be called directly in tests. Constructor injection of `PlatformClient`, `Redis`, and `Settings` makes integration testing clean via `MockPlatformClient`. This avoids the monkey-patching that a flat function approach would require and the protocol wrappers that Approach C demands.

3. **Minimal deviation from existing patterns.** The ARQ job entry point follows `run_meeting_summary` exactly. The factor model follows `ConcentrationRiskScorer` exactly. The agents follow `portfolio_analyst` exactly (registered in `app.agents.registry`). The only new patterns are the pipeline class and the `agents/` sub-package -- both justified by the feature's complexity and both contained within `portfolio_construction/`.

4. **Clean data boundary.** The `DataLoader` class owns Decimal-to-float conversion and freshness checking. This keeps `PlatformClient` methods clean (they return Pydantic models with `Decimal` as per convention) while giving the orchestrator float-based data ready for numpy math.

5. **Shadow-mode friendly without over-engineering.** Shadow mode (Phase 1) requires running v2 alongside mandate-py on fixed prompts. This is a test harness concern, not an architectural concern. The pipeline class is straightforward to instantiate in a test harness that compares outputs.

---

## Task Breakdown (recommended approach)

Tasks are dependency-ordered. Each task is a vertical slice that can be tested independently before moving to the next.

### Task 1: Domain Models and Configuration

Create the foundational types that all other tasks depend on.

**Files to create:**
- `app/portfolio_construction/__init__.py`
- `app/portfolio_construction/models.py` -- `ConstructPortfolioRequest`, `ConstructPortfolioResponse`, `ParsedIntent`, `IntentConstraints`, `FactorPreferences`, `ThemeScoreResult`, `FactorScoreResult`, `CompositeScoreResult`, `ProposedHolding`, `CriticFeedback`, `PortfolioRationale`, job event types, `PortfolioConstructionAccepted`
- `app/portfolio_construction/config.py` -- Theme-factor priors dict, factor metadata constants, default composite scoring parameters, default optimizer parameters, `THEME_SCORE_BATCH_SIZE`, `THEME_SCORE_CONCURRENCY_CAP`

**Files to modify:**
- `app/config.py` -- Add `portfolio_freshness_warn_s: int = 86400`, `portfolio_theme_cache_ttl_s: int = 21600`

**Files to create (test):**
- `tests/test_portfolio_construction_models.py` -- Pydantic model construction, serialization round-trip, default values, validation constraints

**Testable outcome:** All domain models instantiate, serialize, and deserialize correctly. Config values load from env.

### Task 2: PlatformClient Extensions and Data Loader

Add the typed platform read methods and the data loading layer.

**Files to modify:**
- `app/models/platform_models.py` -- Add `SecuritySnapshot`, `FundamentalsV2`, `PriceDataV2`
- `app/services/platform_client.py` -- Add `get_security_universe()`, `bulk_fundamentals(tickers, access_scope)`, `bulk_price_data(tickers, access_scope)`. Extend `get_benchmark_data()` with optional `benchmark_id` parameter.
- `tests/mocks/mock_platform_client.py` -- Add mock methods for the three new endpoints with canned data

**Files to create:**
- `app/portfolio_construction/data_loader.py` -- `DataLoader` class wrapping platform calls with Decimal-to-float conversion, freshness checks, and warnings collection
- `app/portfolio_construction/market_data_fallback.py` -- yfinance fallback adapter with field-by-field merge and provenance tagging, guarded by `settings.environment != "production"`

**Files to create (test):**
- `tests/test_portfolio_data_loader.py` -- Test data loading with `MockPlatformClient`, freshness warning emission, Decimal-to-float conversion, fallback merge behavior

**Testable outcome:** `DataLoader` loads and converts typed data from mock platform client. Freshness warnings fire when staleness exceeds threshold.

### Task 3: Factor Model v2

Build the deterministic factor scoring model.

**Files to create:**
- `app/analytics/portfolio_factor_model_v2.py` -- `PortfolioFactorModelV2` class with `ModelMetadata`, `score()` method implementing: six canonical factors with docstring research cards, hierarchical peer-bucket normalization, winsorization, percentile ranking, correlation-adjusted sub-factor aggregation, reliability shrinkage, breadth-sensitive caps, weighted geometric mean, runtime factor activation/deactivation, activation report

**Files to modify:**
- `app/analytics/startup.py` -- Import `PortfolioFactorModelV2`, register in `register_all_models()`

**Files to create (test):**
- `tests/test_portfolio_factor_model_v2.py` -- Unit tests (no mocks) for: percentile ranking, winsorization, peer bucket selection, correlation-adjusted weights, reliability shrinkage toward 50, breadth caps (1 sub-factor caps at 65, low support caps at 75), geometric mean across factors, factor deactivation below 0.60 coverage, factor deactivation below 3 sub-factors, full score() call with synthetic data, activation report contents

**Testable outcome:** Factor model scores a synthetic universe of 20+ securities with known fundamentals and prices. Scores are 0-100, deactivation fires correctly, activation report is populated.

### Task 4: Recall Pool Builder

Build the two-stage recall pool.

**Files to create:**
- `app/portfolio_construction/recall_pool.py` -- `build_recall_pool()` function: top N_factor by factor score + metadata keyword matches (name, description, sector, industry, tags) + explicit include_tickers. Apply explicit exclusions. Cap at 250.

**Files to create (test):**
- `tests/test_portfolio_recall_pool.py` -- Test factor-top-N selection, metadata matching, include_tickers honored, excluded_tickers removed, cap enforced at 250, pool with fewer candidates than cap

**Testable outcome:** Given factor scores, security metadata, and intent, the recall pool contains the expected tickers.

### Task 5: Theme Scorer Agent and Cache

Build the LLM theme scoring agent with caching.

**Files to create:**
- `app/portfolio_construction/agents/__init__.py`
- `app/portfolio_construction/agents/theme_scorer.py` -- `portfolio_theme_scorer` agent using `settings.batch_model`, output type `list[ThemeScoreResult]`, prompt teaching reasoning about business exposure, revenue mix, broad vs. specific themes, anti-goals as hard negatives. Batch scoring (10-20 tickers per LLM call). Registered in `app.agents.registry` with `tier="batch"`.
- `app/portfolio_construction/cache.py` -- `ThemeScoreCache` class: compute cache key as `sha256(themes + anti_goals + sorted(tickers) + scorer_model + prompt_version + universe_snapshot_id)`, request-scoped dict cache, optional Redis cache with configurable TTL
- `app/portfolio_construction/prompts.py` -- Theme scorer prompt template

**Files to create (test):**
- `tests/test_portfolio_theme_scorer.py` -- Prompt-contract test: validate that a sample LLM output string parses into `list[ThemeScoreResult]`. Cache key determinism test. Cache hit/miss behavior test.

**Testable outcome:** Theme scorer agent is registered. Cache produces deterministic keys. Sample outputs parse correctly.

### Task 6: Composite Scorer

Build the seven-step composite scoring pipeline.

**Files to create:**
- `app/portfolio_construction/composite_scorer.py` -- `score_composite()` function implementing the seven steps: hard exclusion, anti-goal gate, eligibility gates (factor floor, theme floor), uncertainty adjustment (shrink low-confidence themes and low-reliability factors toward 50), weighted geometric mean, coherence bonus / weak-link penalty, clamp [0, 100]. Accepts config overrides for all parameters.

**Files to create (test):**
- `tests/test_portfolio_composite_scorer.py` -- Test each gate independently: explicit exclusion gates to 0, anti-goal gates to 0, below-factor-floor gates to 0, below-theme-floor gates to 0. Test uncertainty adjustment with low-confidence theme. Test geometric mean produces expected ranking. Test coherence bonus triggers when both scores >= 70. Test weak-link penalty triggers when gap >= 35. Test final clamp to [0, 100]. Test speculative intent overrides (lower factor_floor).

**Testable outcome:** Composite scorer produces correct scores and gates for a suite of synthetic inputs with known expected outputs.

### Task 7: Optimizer and Constraint Relaxation

Build candidate selection, weighting strategies, and position limits.

**Files to create:**
- `app/portfolio_construction/optimizer.py` -- `select_candidates()`: apply exclusions, rank by composite, honor include_tickers, enforce sector cap and position count, backfill from deferred. `auto_relax()`: fixed sequence (min_theme_score, max_beta, sector cap, reduce target count), emit relaxation details. `weight_equal()`, `weight_conviction()`, `weight_risk_parity()` (inverse realized vol, sector-median imputation), `weight_min_variance()` (Ledoit-Wolf shrinkage via `sklearn.covariance.LedoitWolf`, score proxy lambda 0.10, fallback to risk_parity). `clamp_positions()`: iterative clamping with min_weight 0.02, max_weight 0.10, feasibility relaxation.
- `app/portfolio_construction/account_aware.py` -- `compute_account_context()`: read holdings, compute overlap, estimated turnover, drift, tax-sensitive warnings.

**Files to create (test):**
- `tests/test_portfolio_optimizer.py` -- Test equal weighting sums to 1.0. Test conviction weighting proportional to scores. Test risk_parity uses inverse vol. Test min_variance falls back to risk_parity on solver failure. Test position clamping redistributes excess. Test auto-relax sequence: verify min_theme_score loosened first, then max_beta, etc. Test include_tickers preserved through selection. Test sector cap enforcement.

**Testable outcome:** Each weighting strategy produces valid weights summing to 1.0. Constraint relaxation follows the fixed sequence. Position limits are enforced.

### Task 8: Intent Parser, Rationale, and Critic Agents

Build the three copilot-tier agents.

**Files to create:**
- `app/portfolio_construction/agents/intent_parser.py` -- `portfolio_intent_parser` agent, `settings.copilot_model`, output `ParsedIntent`, registered `tier="copilot"`. Prompt covers: theme refinement, factor preference inference, risk/concentration tolerance, explicit ticker/exclusion preservation, ambiguity flags, theme_weight/max_sector_concentration coordination.
- `app/portfolio_construction/agents/rationale.py` -- `portfolio_rationale` agent, `settings.copilot_model`, output `PortfolioRationale`, registered `tier="copilot"`. Prompt covers: overall thesis, per-holding justification, key factor signals, core vs. supporting classification.
- `app/portfolio_construction/agents/critic.py` -- `portfolio_critic` agent, `settings.copilot_model`, output `CriticFeedback`, registered `tier="copilot"`. Prompt covers: theme alignment, anti-goal compliance, diversification, factor coherence, obvious core name inclusion, account-aware turnover realism.

**Files to create (test):**
- `tests/test_portfolio_agent_contracts.py` -- Prompt-contract tests: validate that sample LLM output strings parse into `ParsedIntent`, `PortfolioRationale`, and `CriticFeedback` respectively. Test that critic feedback application respects hard rules (no overriding user exclusions, no forcing constraint-violating inclusions).

**Testable outcome:** All three agents registered in registry. Sample outputs parse correctly. Critic feedback rules enforce constraints.

### Task 9: Progress Events (Redis Streams)

Build the event emission and reading layer.

**Files to create:**
- `app/portfolio_construction/events.py` -- `ProgressEventEmitter` class: `emit(job_id, event_type, payload)` writes to Redis Stream via XADD. Event payload includes `v: 1`, `job_id`, `event_type`, `timestamp`, typed data. `read_events(job_id, last_id)` reads via XREAD for SSE endpoint. Stream key: `sidecar:portfolio:events:{job_id}`.

**Files to create (test):**
- `tests/test_portfolio_construction_events.py` -- Test event emission and reading with a mock Redis that records XADD calls. Test event schema includes `v`, `job_id`, `event_type`, `timestamp`.

**Testable outcome:** Events emit with correct schema. Reading returns events in order.

### Task 10: Orchestrator (Pipeline Class)

Wire all stages together in the review loop.

**Files to create:**
- `app/portfolio_construction/orchestrator.py` -- `PortfolioConstructionPipeline` class. Constructor receives `PlatformClient`, `Redis`, `AccessScope`, `JobTracer | None`, `Settings`. `run(request, job_id)` method executes: parse intent, load data, build recall pool, score themes, then review loop (up to 3 iterations: factor score, composite score, select candidates, optimize weights, generate rationale, run critic, apply feedback). Emit progress events at each stage. Handle two modes: `idea` and `account_refresh`. On completion, assemble `ConstructPortfolioResponse` with parsed intent, proposed holdings, score breakdowns, rationale, warnings, applied relaxations, model/agent metadata.

**Files to create (test):**
- `tests/test_portfolio_construction_orchestrator.py` -- Integration test with `MockPlatformClient` and patched agents (return canned outputs). Verify: all progress events emitted in correct order, review loop terminates on APPROVED, review loop returns best effort after 3 NEEDS_REVISION, theme scores reused across iterations, account_refresh mode triggers account-aware analysis, final response contains all required fields.

**Testable outcome:** Pipeline runs end-to-end with mocked dependencies. Review loop behaves correctly. All event types emitted.

### Task 11: ARQ Job and Enqueue Helper

Wire the pipeline into the ARQ job system.

**Files to create:**
- `app/jobs/portfolio_construction.py` -- `run_portfolio_construction()` job function following `run_meeting_summary` pattern. Parse `JobContext`, extract deps, create `PortfolioConstructionPipeline`, call `run()`, persist result to Redis, return summary dict.

**Files to modify:**
- `app/jobs/enqueue.py` -- Add `enqueue_portfolio_construction(job_ctx, request)` helper
- `app/jobs/worker.py` -- Import `run_portfolio_construction`, add to `WorkerSettings.functions`

**Files to create (test):**
- `tests/test_portfolio_construction_enqueue.py` -- Test `ConstructPortfolioRequest` serialization round-trip through `enqueue_portfolio_construction`. Test `JobContext` carries access_scope correctly.

**Testable outcome:** Job function can be imported. Enqueue helper produces correct job args. Worker settings include the new function.

### Task 12: Router Endpoints

Expose the API surface.

**Files to modify:**
- `app/routers/portfolio.py` -- Add `POST /portfolio/construct` (accepts `ConstructPortfolioRequest`, returns 202 with `PortfolioConstructionAccepted`), `GET /portfolio/jobs/{job_id}` (fetch job status from Redis and optionally the final payload), `GET /portfolio/jobs/{job_id}/events` (SSE endpoint using `ProgressEventEmitter.read_events()`).

**Files to create (test):**
- `tests/test_portfolio_construction_router.py` -- TestClient tests: POST /ai/portfolio/construct returns 202 with job_id, GET /ai/portfolio/jobs/{job_id} returns status, events endpoint returns SSE stream. Test missing required headers return 422. Test scope propagation into JobContext.

**Testable outcome:** All three endpoints respond correctly. Construct returns 202. Status returns job state.

### Task 13: Shadow-Mode Test Harness

Build the regression comparison infrastructure for Phase 1 rollout.

**Files to create:**
- `tests/fixtures/portfolio_construction_prompts.json` -- 10-15 curated prompts with mandate-py baseline outputs (holdings, weights, sector distribution)
- `tests/test_portfolio_shadow_mode.py` -- Test runner that executes v2 pipeline on each fixture prompt with mocked platform data and compares: holdings overlap, sector concentration, factor quality, presence of obvious core names. Produces structured comparison report.

**Testable outcome:** Shadow-mode runner executes all fixture prompts and produces comparison metrics.
