# Portfolio Construction Engine v2

Specification for replacing `mandate-py`'s portfolio construction flow with a first-class module inside the intelligence layer.

v2 produces a proposed target portfolio, rationale, and diagnostics. It does not place trades, mutate account records, or become a system of record. The sidecar remains read-oriented and recommendation-oriented.

## Repo Alignment

This spec is intentionally aligned to the current repository structure:

- `apps/intelligence-layer/src/app/analytics/registry.py`
- `apps/intelligence-layer/src/app/analytics/startup.py`
- `apps/intelligence-layer/src/app/agents/registry.py`
- `apps/intelligence-layer/src/app/agents/base_deps.py`
- `apps/intelligence-layer/src/app/services/platform_client.py`
- `apps/intelligence-layer/src/app/models/platform_models.py`
- `apps/intelligence-layer/src/app/routers/portfolio.py`
- `apps/intelligence-layer/src/app/jobs/worker.py`

The prior draft referred to `src/intelligence_layer/...` paths and a synchronous SSE-first execution model. That does not match the current app layout or the sidecar's existing ARQ-based runtime guidance. This version corrects those mismatches.

---

## 1. Goals and Scope

### Goals

- Turn a natural-language request into a reviewable US equity portfolio proposal.
- Replace the brittle theme heuristics in `mandate-py` with typed Pydantic AI agents.
- Move deterministic factor scoring into the existing analytics registry.
- Reuse the existing read-only `PlatformClient` boundary and request access scope.
- Produce structured explanations, warnings, and per-holding rationale.
- Support both idea generation and account-aware construction.

### Out of Scope

- Order creation, rebalancing, or execution.
- Custodial writes, OMS writes, or workflow advancement.
- Multi-asset construction beyond US equities.
- Full transaction-cost or market-impact modeling.
- Benchmark-relative optimizer sophistication beyond simple score and risk controls.

---

## 2. Why `mandate-py` Is Not Enough

### 2.1 Factor model weaknesses

The current factor model is directionally useful but structurally weak:

- It uses a narrow metric set and misses several standard equity signals.
- It averages correlated sub-factors too naively.
- It treats missing data as simple neutrality, which can create false confidence.
- It does not track data coverage or score reliability.
- It has no clear governance boundary inside the sidecar's analytics registry.

### 2.2 Theme scoring weaknesses

The heuristic scorer hardcodes a few favored themes and degrades badly outside them. That should not survive the migration.

The correct v2 rule is:

- final theme scores are produced by an LLM agent
- deterministic logic is allowed only for coarse recall, hard exclusions, and caching
- no heuristic branch is allowed to act as a final theme score

### 2.3 Composite scoring weaknesses

The current `theme_weight * theme + (1 - theme_weight) * factor` blend is too permissive:

- a strong theme score can rescue an obviously broken company
- anti-goals are not modeled as hard exclusions
- there is no explicit distinction between "off-theme" and "anti-goal"
- there is no penalty for low data coverage

### 2.4 Construction weaknesses

The current constructor is good enough for a prototype, but it lacks:

- account-aware turnover controls
- covariance-aware weighting in the advanced path
- persisted construction diagnostics
- explicit constraint auto-relaxation policy
- a clean async execution story inside the sidecar

### 2.5 Runtime mismatch

The old draft recommended a synchronous long-running request with SSE as the default. That conflicts with the sidecar architecture, which explicitly avoids heavy batch work on the request thread and already uses ARQ for similar workflows. Portfolio construction belongs in the async job path by default.

---

## 3. Target Architecture

### 3.1 Module layout

```text
apps/intelligence-layer/src/app/
├── analytics/
│   ├── registry.py
│   ├── startup.py
│   └── portfolio_factor_model_v2.py       # new deterministic model
├── portfolio_construction/
│   ├── __init__.py
│   ├── models.py                          # request/result domain models
│   ├── adapters.py                        # platform-first read adapters
│   ├── factor_inputs.py                   # decimal -> float conversion and validation
│   ├── theme_recall.py                    # deterministic broad candidate recall
│   ├── theme_scorer.py                    # Pydantic AI theme scorer
│   ├── composite.py                       # gating and blend logic
│   ├── constraints.py                     # hard filters + auto-relax policy
│   ├── optimizer.py                       # weighting and position limits
│   ├── intent_parser.py                   # Pydantic AI intent parser
│   ├── rationale.py                       # Pydantic AI rationale generator
│   ├── critic.py                          # Pydantic AI review agent
│   ├── orchestrator.py                    # end-to-end job pipeline
│   ├── cache.py                           # request/job cache helpers
│   └── prompts.py
├── routers/
│   └── portfolio.py                       # extend existing router with construct endpoints
└── jobs/
    └── portfolio_construction.py          # ARQ job entry point
```

### 3.2 Registration model

- `portfolio_factor_model_v2` is registered in `app.analytics.startup.register_all_models()`.
- `portfolio_intent_parser`, `portfolio_theme_scorer`, `portfolio_rationale`, and `portfolio_critic` are registered in `app.agents.registry`.
- The orchestrator is a normal Python service, not a registry entry.

### 3.3 Read boundary and access scope

All external reads flow through `app.services.platform_client.PlatformClient` and must carry the request `AccessScope`.

The construction pipeline may read:

- security universe metadata
- fundamentals and price history
- benchmark data
- account summary and holdings when `account_id` is supplied

It may not:

- write model assignments
- create orders
- mutate workflows
- bypass platform access controls with generic fetches

### 3.4 Execution model

Default execution path:

1. API receives construction request.
2. API validates scope and enqueues an ARQ job.
3. Job emits persisted progress events keyed by `job_id`.
4. Client polls status or streams job events via SSE.
5. Final artifact is a structured portfolio proposal payload.

Synchronous execution may exist for local testing or tiny universes, but it is not the default production path.

---

## 4. Core Contracts

### 4.1 Construction modes

The engine supports two modes:

- `idea`
  Build a portfolio from market data only.
- `account_refresh`
  Build a portfolio with awareness of an existing account's holdings, drift, and turnover.

`account_refresh` is selected only when `account_id` is provided and readable by scope.

### 4.2 Key models

```python
class ParsedIntent(BaseModel):
    themes: list[str]
    anti_goals: list[str]
    include_tickers: list[str] = []
    target_count: int = 15
    weighting_strategy: Literal[
        "equal", "conviction", "risk_parity", "min_variance"
    ] = "conviction"
    theme_weight: float = 0.60
    constraints: IntentConstraints
    preferences: FactorPreferences
    ambiguity_flags: list[str] = []


class ThemeScoreResult(BaseModel):
    ticker: str
    score: float                         # 0-100
    confidence: float                    # 0-1
    matched_themes: list[str]
    anti_goal_hit: bool = False
    rationale: str


class FactorScoreResult(BaseModel):
    ticker: str
    score: float                         # 0-100
    factor_breakdown: dict[str, float]
    data_coverage: float                 # 0-1
    warnings: list[str] = []


class CompositeScoreResult(BaseModel):
    ticker: str
    theme_score: float
    factor_score: float
    composite_score: float
    gated: bool = False
    gate_reason: str | None = None
    warnings: list[str] = []
```

### 4.3 Numeric conventions

- Platform read models use `Decimal` at the API boundary.
- Portfolio construction may convert to `float` internally for vector math.
- Every conversion layer must be explicit and loss-tolerant.
- Final externally visible monetary values must convert back to `Decimal` where applicable.

---

## 5. Factor Model v2

### 5.1 Registry metadata

```python
ModelMetadata(
    name="portfolio_factor_model_v2",
    version="2.0.0",
    owner="portfolio-analytics",
    category=ModelCategory.PORTFOLIO,
    kind=ModelKind.DETERMINISTIC,
    description="Cross-sectional equity factor scoring for portfolio construction.",
    use_case="Rank a US equity universe for construction and portfolio diagnostics.",
    input_freshness_seconds=86_400,
    known_limitations=(
        "US equities only.",
        "Forward-estimate coverage may be partial in v2.",
        "Very small industry buckets may require fallback to sector or universe normalization.",
    ),
)
```

### 5.2 Factor identification is a first-class problem

The current spec still assumes factor identification is mostly solved once we list six canonical factors. That is too loose.

v2 must treat factor identification and factor scoring as separate concerns:

- factor identification answers: which factors are economically relevant, measurable, and trustworthy for this request and this universe
- factor scoring answers: given the active factors, how strong is each stock on each factor

The engine should not blindly score every request with the same full factor stack.

Each factor in the canonical library must declare metadata beyond its sub-metrics:

- economic thesis
- expected direction of payoff
- required raw fields
- minimum universe coverage
- minimum cross-sectional dispersion
- preferred normalization bucket
- redundancy group
- regime notes

No factor should be active in production unless it has an explicit research card documenting:

- why it exists economically
- which universes it works in
- coverage and freshness expectations
- monotonicity or directional validation from offline testing
- known failure modes

### 5.3 Runtime factor activation

For each construction request:

1. Start from the canonical factor library.
2. Compute universe-level viability for each factor.
3. Deactivate a factor if any of these are true:
   - coverage is below `0.60`
   - fewer than `3` viable sub-factors remain
   - cross-sectional dispersion is too low to rank meaningfully
4. Apply intent priors from the parsed request.
   Example: a value-oriented request increases `value`, lowers `growth`.
5. Apply deterministic theme-factor priors from a config map.
   Example: AI infrastructure should usually favor growth, quality, and momentum more than deep value.
6. Normalize the resulting active-factor weights.
7. Emit an activation report explaining:
   - which factors were active
   - which factors were down-weighted
   - which factors were deactivated and why

Important rule:

- theme-factor priors must be config-driven and reviewable
- they must not be inferred ad hoc by the LLM inside the scoring path

### 5.4 Factor set

Six factors remain a good balance for v2:

| Factor | Default Weight | Core Metrics |
|---|---:|---|
| Value | 0.20 | earnings yield, book yield, FCF yield, EV/EBITDA, dividend yield |
| Quality | 0.20 | ROIC, gross margin stability, accruals ratio, interest coverage, operating CF / net income |
| Growth | 0.20 | revenue growth, earnings growth, revenue acceleration, R&D intensity, earnings revision |
| Momentum | 0.15 | 12-1 return, 6-month return, risk-adjusted momentum, sector-relative strength |
| Low Volatility | 0.10 | realized volatility, beta, max drawdown |
| Size / Liquidity | 0.15 | market cap, average daily dollar volume |

### 5.5 Robust normalization

The current scoring feels weak mainly because plain z-scores over the whole universe are noisy, sector-biased, and too sensitive to outliers. v2 should replace that with robust, sector-aware ranking.

Rules:

1. Invalid raw values become missing.
   Example: negative earnings should not produce a bogus P/E-derived earnings yield.
2. Right-skewed metrics use log transforms before ranking where appropriate.
3. Metrics are normalized by hierarchical peer bucket, not by whole-universe z-score.
   Preferred order: industry bucket, then sector bucket, then full universe.
4. A bucket is usable only if it has enough names.
   Suggested minimums: `industry >= 15`, `sector >= 25`.
5. Raw values are winsorized inside the selected bucket before ranking.
   Default clip: 5th to 95th percentile.
6. Each metric is converted to an empirical percentile rank `p` and then centered:
   `metric_score = 2 * p - 1`, producing a stable `[-1, 1]` score.
7. Lower-is-better metrics are inverted after ranking.
8. Missing sub-factors are omitted from aggregation, not filled with neutral zeros.

This is intentionally rank-based. Equity fundamentals are heavy-tailed and regime-sensitive, and percentile scoring is materially more stable than raw z-scores for a retail/advisor construction engine.

### 5.6 Correlation-aware sub-factor aggregation

The old weighted-average approach still double-counts redundant signals. v2 should reduce that explicitly.

Within each factor:

1. Compute pairwise absolute correlation between available sub-factors across the live universe.
2. Penalize redundant metrics before aggregation:

```text
adj_weight_i = base_weight_i / (1 + mean_abs_corr_i)
normalized_weight_i = adj_weight_i / sum(adj_weights)
```

3. Aggregate using the normalized correlation-adjusted weights.
4. Publish the effective weights in `universe_stats` for inspectability.

This is not full orthogonalization, but it is cheap, explainable, and much better than pretending correlated metrics are independent.

### 5.7 Reliability shrinkage and neutral anchoring

Low-quality data should not push a stock toward an extreme score. It should pull the score back toward neutral.

For each factor, compute a `reliability` score in `[0, 1]` from:

- sub-factor coverage
- data freshness
- peer-bucket size
- presence or absence of critical metrics

Then shrink the raw factor score toward neutral:

```text
final_factor_score = 50 + reliability * (raw_factor_score - 50)
```

This is better than a flat multiplicative penalty because uncertain data becomes less opinionated rather than just smaller.

### 5.8 Breadth-sensitive factor scoring

Another weakness in the original design is that a stock can look excellent on a factor because of one heroic sub-metric while everything else is neutral, missing, or bad.

v2 should enforce breadth inside each factor:

1. Compute `supportive_weight_share`:

```text
supportive_weight_share =
  sum(weight_i for subfactor_i with score_i > 0) / sum(available_weights)
```

2. Apply caps before finalizing the factor score:

- if only `1` sub-factor is available, cap the factor score at `65`
- if `supportive_weight_share < 0.50`, cap the factor score at `75`
- if `supportive_weight_share >= 0.70`, no breadth cap applies

This prevents one-metric winners from surfacing as top-ranked names.

### 5.9 Context-sensitive metrics

Two metrics remain context-sensitive:

- `rnd_intensity`
  Higher is favorable for innovation-heavy themes, neutral otherwise.
- `market_cap`
  Smaller is favorable only when the intent clearly requests small-cap exposure.

These should not be encoded as ambiguous math inside the factor model. The factor model emits the raw normalized metrics, and the composite layer applies the intent-specific directional preference.

### 5.10 Overall factor score aggregation

The overall factor score should not be a plain weighted average across active factors. That still lets one dominant factor wash out obvious weakness elsewhere.

Use a weighted geometric mean across active factor scores:

```text
q_f = clamp(factor_score_f / 100, 0.01, 0.99)
overall_factor_score =
  100 * exp(sum(active_weight_f * ln(q_f)))
```

Why this is better:

- it rewards names that are consistently strong across the factors that actually matter
- it penalizes weak links more than an arithmetic average
- it still allows concentrated intent because irrelevant factors can be deactivated earlier in the activation step

### 5.11 Scoring algorithm

```text
for each stock:
  1. validate raw inputs and null invalid metrics
  2. determine the active factor set and active factor weights
  3. choose peer bucket: industry -> sector -> universe
  4. winsorize and percentile-rank each metric inside that bucket
  5. convert percentile ranks to centered sub-factor scores in [-1, 1]
  6. apply directional inversion where lower is better
  7. aggregate sub-factors with correlation-adjusted weights
  8. apply reliability shrinkage toward neutral 50
  9. apply breadth caps inside each factor
  10. rescale each factor to 0-100
  11. combine active factors with a weighted geometric mean
```

### 5.12 Input and output contract

```python
def score(self, inputs: dict[str, Any]) -> dict[str, Any]:
    """
    inputs:
      securities: dict[str, SecuritySnapshot]
      fundamentals: dict[str, FundamentalsV2]
      prices: dict[str, PriceDataV2]
      preferences: FactorPreferences | None

    returns:
      scores: dict[str, FactorScoreResult]
      universe_stats: dict[str, Any]
      metadata: dict[str, Any]
    """
```

`universe_stats` should include:

- active factor set
- deactivated factors and reasons
- bucket choice statistics
- effective sub-factor weights
- factor coverage and dispersion summaries

---

## 6. Theme Scoring

### 6.1 Design rule

v2 removes heuristic final scoring but keeps deterministic recall.

That means:

- the LLM decides the final 0-100 theme score
- deterministic code may expand the candidate set, enforce explicit excludes, and cache results
- deterministic code may not override the final theme score except for hard anti-goal exclusion

### 6.2 Two-stage recall

The old draft proposed scoring only the top `N` names by factor score. That is too aggressive for theme-heavy portfolios because it can exclude early-stage or lower-quality names that are still legitimate pure plays.

Use a recall pool built as:

- top `N_factor` names by factor score
- all user-specified `include_tickers`
- a broad metadata match set from security name, description, sector, industry, and tags

Suggested defaults:

- `N_factor = 150`
- `N_metadata = 100`
- recall pool cap `= 250`

### 6.3 Agent definition

```text
Agent: portfolio_theme_scorer
Registry tier: batch
Model tier: cheap classification model
Output: list[ThemeScoreResult]
Tools: none
```

### 6.4 Score semantics

Theme scores stay on a strict `0-100` scale:

| Score | Meaning |
|---|---|
| 85-100 | core play on the theme |
| 65-84 | strong alignment |
| 45-64 | partial or indirect alignment |
| 25-44 | weak or tangential |
| 0-24 | off-theme or anti-goal-adjacent |

Important correction:

- do not use negative theme scores
- represent anti-goals as `anti_goal_hit = true` plus `score = 0`

That keeps score math clean and makes exclusion logic explicit.

### 6.5 Prompt requirements

The prompt must explicitly teach the model to reason about:

1. Actual business exposure, not just GICS labels.
2. Revenue mix and product reality, not marketing language.
3. Broad themes versus specific sub-themes.
4. Multi-theme portfolios where one stock may match more than one theme.
5. Anti-goals as hard negatives.
6. Uncertainty handling: low-confidence names should score conservatively.

### 6.6 Caching

Theme scores are stable across review-loop iterations as long as these inputs do not change:

- normalized themes
- anti-goals
- recall pool
- scorer model version
- prompt version

Recommended cache key:

```text
sha256(
  themes + anti_goals + sorted(tickers)
  + scorer_model + prompt_version + universe_snapshot_id
)
```

Cache lifetime:

- request-scoped cache inside a single job
- optional Redis cache for cross-job reuse with TTL 6 hours

### 6.7 Safety rails

- Explicit `excluded_tickers` and `excluded_sectors` are applied before LLM scoring.
- `anti_goal_hit` forces exclusion later in composite scoring.
- Low-confidence theme results should not be silently rounded up.

---

## 7. Composite Scoring and Gating

### 7.1 Corrected scoring flow

The previous draft still leaned too hard on a plain linear blend. That is one of the main reasons the scoring feels weak. v2 should separate eligibility from ranking and use a non-linear composite for final ordering.

Use this order:

```text
Step 1: hard exclusion
  if ticker is explicitly excluded:
    composite = 0
    gated = true
    gate_reason = "explicit_exclusion"

Step 2: anti-goal gate
  if theme_result.anti_goal_hit:
    composite = 0
    gated = true
    gate_reason = "anti_goal"

Step 3: eligibility gates
  if factor_score < factor_floor:
    composite = 0
    gated = true
    gate_reason = "below_factor_floor"
  if thematic_request and theme_score < min_theme_score:
    composite = 0
    gated = true
    gate_reason = "below_theme_floor"

Step 4: uncertainty adjustment
  if theme_confidence < theme_confidence_floor:
    theme_confidence = theme_confidence * 0.5
  theme_adj = 50 + theme_confidence * (theme_score - 50)
  factor_adj = 50 + factor_reliability * (factor_score - 50)

Step 5: ranking score
  t = clamp(theme_adj / 100, 0.01, 0.99)
  f = clamp(factor_adj / 100, 0.01, 0.99)
  composite = 100 * exp(theme_weight * ln(t) + (1 - theme_weight) * ln(f))

Step 6: coherence bonus / weak-link penalty
  if theme_adj >= 70 and factor_adj >= 70:
    composite += interaction_bonus
  if abs(theme_adj - factor_adj) >= weak_link_gap:
    composite -= weak_link_penalty

Step 7: clamp to [0, 100]
```

The weighted geometric mean is deliberate. It still rewards stocks that are strong on both theme and fundamentals, but it punishes one-sided names much more than a linear average.

### 7.2 Parameter defaults

| Parameter | Default | Notes |
|---|---:|---|
| `theme_weight` | 0.60 | set by intent parser |
| `factor_floor` | 25 | lowered only for explicitly speculative requests |
| `theme_confidence_floor` | 0.50 | below this, theme scores shrink materially toward neutral |
| `interaction_bonus` | 5 | modest nudge, never a rescue path |
| `min_theme_score` | 30 | only applied when the user asked for explicit themes |
| `weak_link_gap` | 35 | gap between adjusted theme and factor scores |
| `weak_link_penalty` | 5 | penalty for lopsided names |

### 7.3 Speculative intent handling

Do not globally assume all users want a hard factor floor. When the user explicitly asks for speculative, frontier, or pure-play exposure, the intent parser may lower:

- `factor_floor` to `10-15`
- `min_market_cap`
- `max_sector_concentration`
- `min_theme_score` upward rather than downward when pure thematic exposure is the point

That override must be explicit and reviewable in the parsed intent.

### 7.4 Theme-weight guidance

| User signal | Suggested `theme_weight` |
|---|---:|
| "build me an AI infrastructure portfolio" | 0.70-0.85 |
| "growth portfolio with some AI exposure" | 0.40-0.55 |
| "value stocks in tech" | 0.25-0.40 |
| "best risk-adjusted names" | 0.10-0.20 |

When `theme_weight >= 0.70`, a concentrated sector footprint is expected unless the user explicitly asks for diversification.

---

## 8. Optimizer and Constraints

### 8.1 Hard constraints

Supported hard constraints:

- `excluded_tickers`
- `excluded_sectors`
- `include_tickers`
- `min_market_cap`
- `max_market_cap`
- `max_beta`
- `max_single_position`
- `max_sector_concentration`
- `min_factor_score`
- `min_theme_score`
- `turnover_budget` when `account_id` is present

### 8.2 Candidate selection flow

```text
1. apply explicit exclusions and hard filters
2. rank by composite score descending
3. honor include_tickers if they pass hard exclusions
4. enforce sector cap and position-count target
5. backfill from deferred names
6. if still short, auto-relax eligible constraints in a fixed order
```

Eligible auto-relax sequence:

1. loosen `min_theme_score`
2. loosen `max_beta`
3. loosen sector cap
4. expand target count downward as a last resort

The job must emit which constraints were relaxed and by how much.

### 8.3 Weighting strategies

#### Equal

`weight_i = 1 / N`

#### Conviction

`weight_i = composite_score_i / sum(composite_scores)`

#### Risk parity

Use inverse realized volatility with sector-median imputation for missing vols.

#### Minimum variance

The old draft called this mean-variance optimization but did not define expected returns. That is incomplete.

Use a minimum-variance objective with optional score proxy:

```text
minimize:    w^T Σ w - λ * μ^T w
where:
  Σ = shrunk covariance matrix from trailing daily returns
  μ = normalized composite score proxy
  λ = small return-seeking coefficient, default 0.10
```

Constraints:

- weights sum to `1.0`
- bounds `[min_weight, max_weight]`
- sector weights under cap
- turnover under budget in `account_refresh` mode

If the solver fails, fall back to `risk_parity`.

### 8.4 Position limits

Retain iterative clamping with redistribution, but add explicit feasibility handling:

- if `target_count * max_weight < 1.0`, relax `max_weight`
- if `target_count * min_weight > 1.0`, relax `min_weight`
- emit warnings whenever a limit is relaxed

Default bounds:

- `min_weight = 0.02`
- `max_weight = 0.10`

### 8.5 Account-aware mode

When `account_id` is present, construction should read the current holdings and compute:

- current overlap with proposed names
- estimated turnover
- drift versus current allocation
- tax-sensitive warnings if lot metadata is available

This mode remains recommendation-only. It does not generate executable trades.

---

## 9. Intent Parser

### 9.1 Agent definition

```text
Agent: portfolio_intent_parser
Registry tier: copilot
Output: ParsedIntent
Tools: none
```

### 9.2 Required behaviors

1. Refine vague themes into specific investable themes.
2. Infer factor preferences from the language used.
3. Infer risk and concentration tolerance.
4. Preserve explicit tickers and explicit exclusions.
5. Emit `ambiguity_flags` instead of silently guessing when intent is underspecified.
6. Coordinate `theme_weight` and `max_sector_concentration`.

### 9.3 Important inference rules

- "large cap" implies `min_market_cap`.
- "avoid meme stocks" implies stronger quality and size preferences.
- "conservative" implies lower beta and more low-volatility weight.
- "pure play" implies higher `theme_weight` and looser sector cap.
- "equal weight" overrides weighting strategy directly.

### 9.4 Output correction

The old draft said ambiguity flags were returned "alongside" the intent while the schema omitted them. In v2, `ambiguity_flags` is part of `ParsedIntent` so it survives the job pipeline cleanly.

---

## 10. Rationale and Critic Loop

### 10.1 Rationale agent

```text
Agent: portfolio_rationale
Registry tier: copilot
Output: PortfolioRationale
```

The rationale must explain:

- overall thesis
- why each holding belongs
- which factor signals matter most
- whether the name is a core play or supporting exposure

### 10.2 Critic agent

```text
Agent: portfolio_critic
Registry tier: copilot
Output: CriticFeedback
```

Review criteria:

1. theme alignment
2. anti-goal compliance
3. diversification relative to the stated intent
4. factor coherence
5. inclusion of obvious core names where appropriate
6. account-aware turnover realism when `account_id` is provided

### 10.3 Feedback contract

```python
class CriticFeedback(BaseModel):
    status: Literal["APPROVED", "NEEDS_REVISION"]
    message: str
    exclude_tickers: list[str] = []
    include_tickers: list[str] = []
    exclude_sectors: list[str] = []
    boost_factors: list[str] = []
    reduce_factors: list[str] = []
    adjust_theme_weight: float | None = None
    reduce_sector_cap: float | None = None
```

### 10.4 Hard rules for applying feedback

- Critic feedback may not override explicit user exclusions.
- Critic feedback may not force inclusion of names that violate hard constraints.
- Critic feedback may not change the universe or access scope.
- Review loop max iterations: `3`.

### 10.5 Review loop

```text
1. parse intent
2. load data and build recall pool
3. theme score recall pool
4. loop up to 3 times:
   a. factor score
   b. composite score
   c. select candidates
   d. optimize weights
   e. build draft
   f. generate rationale
   g. run critic
   h. apply deterministic feedback rules
5. if still not approved, return best effort with manager note and warnings
```

Theme scores should usually be reused across loop iterations. Re-score themes only if the theme list or recall pool changes.

---

## 11. Data Access and Adapters

### 11.1 Platform-first read contract

The construction engine needs typed platform reads for:

- equity universe listing
- security metadata snapshots
- fundamentals bulk read
- price-history bulk read
- benchmark data
- account holdings and allocation snapshot

These may require extending `PlatformClient`. The implementation should add explicit typed methods, not generic fetches.

### 11.2 Fallback provider

A fallback market-data adapter is acceptable for missing fields during development, but it must obey these rules:

- platform data remains the primary source
- fallback fields are merged field-by-field, never blindly overwrite platform values
- each merged result carries source provenance
- fallback use is surfaced in warnings and observability

### 11.3 Required typed models

```python
class SecuritySnapshot(BaseModel):
    ticker: str
    name: str
    sector: str
    industry: str | None = None
    market_cap: Decimal | None = None
    avg_daily_dollar_volume: Decimal | None = None
    description: str | None = None
    tags: list[str] = []


class FundamentalsV2(BaseModel):
    ticker: str
    pe_ratio: Decimal | None = None
    pb_ratio: Decimal | None = None
    ev_to_ebitda: Decimal | None = None
    fcf_yield: Decimal | None = None
    dividend_yield: Decimal | None = None
    roic: Decimal | None = None
    gross_margin_stability: Decimal | None = None
    accruals_ratio: Decimal | None = None
    interest_coverage: Decimal | None = None
    operating_cf_to_ni: Decimal | None = None
    revenue_growth: Decimal | None = None
    earnings_growth: Decimal | None = None
    revenue_acceleration: Decimal | None = None
    rnd_intensity: Decimal | None = None
    earnings_revision: Decimal | None = None


class PriceDataV2(BaseModel):
    ticker: str
    return_6m: Decimal | None = None
    return_12_1m: Decimal | None = None
    volatility_1y: Decimal | None = None
    beta: Decimal | None = None
    max_drawdown_1y: Decimal | None = None
    sector_median_return_6m: Decimal | None = None
```

### 11.4 Freshness

Every upstream payload should carry freshness metadata or be wrapped with it. The job must warn when critical inputs exceed declared freshness thresholds.

---

## 12. Orchestration and Progress Events

### 12.1 Pipeline

```text
1. validate request and scope
2. enqueue job
3. parse intent
4. load securities, fundamentals, prices, and optional account context
5. build recall pool
6. score themes
7. review loop:
   a. factor score
   b. composite score
   c. select candidates
   d. optimize weights
   e. generate rationale
   f. critic review
8. finalize proposal payload
9. persist result summary and completion metadata
```

### 12.2 Progress events

Use persisted job events, not ephemeral Redis pub/sub only.

Recommended event types:

- `job_enqueued`
- `intent_parsed`
- `data_loaded`
- `recall_pool_built`
- `theme_scoring_started`
- `theme_scoring_completed`
- `review_iteration_started`
- `draft_built`
- `critic_verdict`
- `job_completed`
- `job_failed`

Persist enough event history for reconnecting SSE clients.

### 12.3 Event transport

Recommended implementation:

- ARQ for job execution
- Redis-backed job state
- Redis Streams or equivalent persisted event log for SSE fan-out

Plain pub/sub is insufficient because reconnecting clients lose history.

---

## 13. API Surface

### 13.1 Internal sidecar endpoints

Extend `app/routers/portfolio.py` with:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/portfolio/construct` | enqueue a portfolio construction job |
| `GET` | `/portfolio/jobs/{job_id}` | fetch job status and final payload |
| `GET` | `/portfolio/jobs/{job_id}/events` | stream persisted progress events |

If the platform exposes these under `/ai/...`, that mapping belongs at the platform/API-gateway layer, not inside the sidecar router contract.

### 13.2 Request schema

```python
class ConstructPortfolioRequest(BaseModel):
    message: str
    account_id: str | None = None
    target_count: int | None = None
    weighting_strategy: Literal[
        "equal", "conviction", "risk_parity", "min_variance"
    ] | None = None
    include_tickers: list[str] = []
    exclude_tickers: list[str] = []
```

### 13.3 Response shape

`POST /portfolio/construct` should return `202 Accepted`:

```python
class PortfolioConstructionAccepted(BaseModel):
    job_id: str
    status: Literal["queued", "running"]
```

Final job payload should include:

- parsed intent
- proposed holdings and weights
- score breakdowns
- rationale
- warnings
- applied relaxations
- model and agent metadata

---

## 14. Observability, Safety, and Testing

### 14.1 Observability

Track at minimum:

- job latency
- per-stage latency
- LLM token and cost usage
- cache hit rate
- fallback-provider usage
- number of review iterations
- auto-relax frequency
- optimizer fallback rate

Use existing Langfuse and normal service metrics patterns already defined in the sidecar specs.

### 14.2 Safety

- Log the request scope and `job_id` on every stage.
- Keep all outputs reviewable and typed.
- Record prompt version, model tier, and factor model version in final metadata.
- Emit warnings for stale data, low coverage, solver fallback, and constraint relaxation.

### 14.3 Testing

Required test layers:

- unit tests for factor math and gating logic
- prompt-contract tests for agent output schemas
- orchestrator tests with mocked agents and mocked platform client
- regression fixtures comparing v2 outputs against curated example prompts
- shadow-mode comparison tests against `mandate-py` for a fixed seed universe

---

## 15. Rollout Plan

### Phase 1: Shadow mode

- Run v2 beside `mandate-py` on a fixed suite of prompts.
- Compare holdings overlap, sector concentration, factor quality, and obvious misses.
- Do not expose v2 to end users yet.

### Phase 2: Internal advisor preview

- Expose v2 behind a feature flag.
- Return richer diagnostics and review warnings.
- Capture manual feedback on relevance and explanation quality.

### Phase 3: Default path

- Make v2 the default constructor.
- Keep `mandate-py` only as a regression reference until confidence is high.

---

## 16. Explicit Non-Goals for v2

These remain deferred:

- multi-asset class construction
- full benchmark-relative optimization
- lot-level tax-aware optimization as a core objective
- full transaction-cost and market-impact modeling
- direct order or rebalance proposal generation
- international equities and ADR-specific handling

---

## 17. Summary of Material Changes From the Prior Draft

- Corrected the module layout to match `apps/intelligence-layer/src/app/...`.
- Moved the default execution model from synchronous SSE to ARQ-backed async jobs.
- Fixed the theme-score contract so anti-goals are explicit booleans, not negative scores.
- Replaced factor-only prefiltering with a broader recall strategy so thematic pure plays are not dropped too early.
- Fixed the registry metadata shape to match the actual analytics registry.
- Added access-scope, typed platform-read, and persisted-event requirements.
- Clarified account-aware construction without turning the sidecar into an order engine.
- Added observability, testing, and rollout guidance so the spec is implementable, not just conceptual.
