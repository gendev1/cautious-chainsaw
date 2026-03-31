# Exploration: portfolio-construction-v2

## Most Similar Feature

The closest existing implementation is the **meeting summary pipeline**, which combines:

1. **A deterministic analytics model** (like `DriftDetector` or `ConcentrationRiskScorer`) registered in the analytics registry -- analogous to the `portfolio_factor_model_v2` deterministic model.
2. **An ARQ job** (`run_meeting_summary` in `app/jobs/meeting_summary.py`) that orchestrates data loading, agent invocation, result persistence, and webhook notification -- analogous to the portfolio construction orchestrator job.
3. **A Pydantic AI agent** (`summary_agent`) with a structured output type (`MeetingSummary`) -- analogous to the intent parser, theme scorer, rationale, and critic agents.
4. **Router endpoints** that accept a request, enqueue the job, and return a job ID -- analogous to `POST /portfolio/construct`.

Secondary references:
- **`portfolio_analyst` agent** (`app/agents/portfolio_analyst.py`) -- closest agent definition for the portfolio domain, uses `AgentDeps`, registers in the agent registry, defines tools and structured output.
- **`copilot` agent** (`app/agents/copilot.py`) -- richest agent example: extended deps (`CopilotDeps`), inline tool definition via `@agent.tool`, system prompt via decorator, full tool list.
- **`daily_digest` job** (`app/jobs/daily_digest.py`) -- shows cron sweep + per-entity fan-out, concurrent data gathering, Redis caching, Langfuse tracing.
- **`concentration_risk` model** (`app/analytics/concentration_risk.py`) -- closest analytics model to the factor model: multi-metric scoring, flag generation, severity classification.

The portfolio construction feature is unique in that it combines **all** of these patterns into a single multi-stage pipeline with iterative review loops. No existing feature has this level of orchestration complexity.

---

## Architecture Map

### Router Layer (`app/routers/`)

Existing file: `app/routers/portfolio.py`
- Currently has one endpoint: `POST /portfolio/analyze` returning `PortfolioAnalysis`.
- Mounted in `main.py` as `app.include_router(portfolio.router, prefix="/ai")`, so full path is `/ai/portfolio/analyze`.
- New endpoints will extend this router: `POST /portfolio/construct`, `GET /portfolio/jobs/{job_id}`, `GET /portfolio/jobs/{job_id}/events`.

All routers in `app/routers/`:
| File | Prefix | Purpose |
|------|--------|---------|
| `portfolio.py` | `/portfolio` | Portfolio analysis (to be extended) |
| `chat.py` | `/chat` | Copilot chat |
| `digest.py` | `/digest` | Daily digest |
| `email.py` | `/email` | Email draft/triage |
| `meetings.py` | `/meetings` | Transcription, summary |
| `tax.py` | `/tax` | Tax planning |
| `reports.py` | `/reports` | Firm reports |
| `documents.py` | `/documents` | Document classify/extract |
| `tasks.py` | `/tasks` | Task extraction |
| `crm.py` | `/crm` | CRM sync |
| `admin.py` | `/admin` | Admin endpoints |
| `health.py` | (none) | Health check |
| `indexing.py` | (none) | RAG indexing |

### Agent Layer (`app/agents/`)

| File | Agent Name | Tier | Output Type |
|------|-----------|------|-------------|
| `copilot.py` | `copilot` | copilot | `HazelCopilot` |
| `portfolio_analyst.py` | `portfolio_analyst` | copilot | `PortfolioAnalysis` |
| `digest.py` | `digest` | batch | `DailyDigest` |
| `email_drafter.py` | `email_drafter` | copilot | `EmailDraft` |
| `email_triager.py` | `email_triager` | batch | (triaged emails) |
| `tax_planner.py` | `tax_planner` | copilot | `TaxPlan` |
| `meeting_prep.py` | `meeting_prep` | copilot | `MeetingPrep` |
| `meeting_summarizer.py` | `meeting_summarizer` | copilot | `MeetingSummary` |
| `firm_reporter.py` | `firm_reporter` | copilot | `FirmWideReport` |
| `doc_classifier.py` | `doc_classifier` | extraction | `DocClassification` |
| `doc_extractor.py` | `doc_extractor` | extraction | `DocExtraction` |
| `task_extractor.py` | `task_extractor` | extraction | `ExtractedTask` |
| `safety.py` | (safety checks) | - | - |
| `disclaimers.py` | (disclaimer injection) | - | - |
| `fallback.py` | (fallback agent) | - | - |

Supporting files:
- `registry.py` -- `AgentRegistry` singleton, agents register via `registry.register(name, agent, tier=..., description=...)`.
- `base_deps.py` -- `AgentDeps` dataclass: `platform`, `access_scope`, `tenant_id`, `actor_id`.
- `deps.py` -- Richer `AgentDeps` with `context: RequestContext`, `redis`, `retriever`.
- `runner.py` -- `run_agent_safe()` with retry and fallback logic.

New agents needed: `portfolio_intent_parser`, `portfolio_theme_scorer`, `portfolio_rationale`, `portfolio_critic`.

### Analytics Model Layer (`app/analytics/`)

| File | Model Name | Category | Kind |
|------|-----------|----------|------|
| `drift_detection.py` | `drift_detection` | PORTFOLIO | DETERMINISTIC |
| `concentration_risk.py` | `concentration_risk` | PORTFOLIO | DETERMINISTIC |
| `tax_loss_harvesting.py` | `tax_loss_harvesting` | TAX | DETERMINISTIC |
| `cash_drag.py` | `cash_drag` | PORTFOLIO | DETERMINISTIC |
| `rmd_calculator.py` | `rmd_calculator` | TAX | DETERMINISTIC |
| `tax_scenario_engine.py` | `tax_scenario_engine` | TAX | DETERMINISTIC |
| `firm_ranker.py` | `firm_ranker` | FIRM_ANALYTICS | DETERMINISTIC |
| `beneficiary_audit.py` | `beneficiary_audit` | COMPLIANCE | DETERMINISTIC |
| `style_profile.py` | `style_profile` | PERSONALIZATION | DETERMINISTIC |

Supporting files:
- `registry.py` -- `ModelRegistry` singleton with `register()`, `get()`, `invoke()`, `list_models()`.
- `startup.py` -- `register_all_models()` called from FastAPI lifespan.

New model needed: `portfolio_factor_model_v2` (PORTFOLIO, DETERMINISTIC).

### Service Layer (`app/services/`)

| File | Purpose |
|------|---------|
| `platform_client.py` | Typed read-only HTTP client for platform API |
| `circuit_breaker.py` | Circuit breaker for external calls |
| `request_cache.py` | Request-scoped in-memory cache |
| `llm_client.py` | LLM provider abstraction |
| `conversation_memory.py` | Redis-backed conversation history |
| `vector_store.py` | Vector store client |
| `retry.py` | Generic retry utilities |
| `degradation.py` | Graceful degradation logic |
| `message_codec.py` | Message serialization |

New PlatformClient methods needed: `get_security_universe()`, `bulk_fundamentals()`, `bulk_price_data()`, `get_benchmark_data()` (already exists for default benchmark).

### Job Layer (`app/jobs/`)

| File | Job Name | Type |
|------|---------|------|
| `daily_digest.py` | `run_daily_digest` | Cron + per-entity |
| `email_triage.py` | `run_email_triage` | Cron + per-entity |
| `meeting_summary.py` | `run_meeting_summary` | On-demand |
| `firm_report.py` | `run_firm_report` | On-demand |
| `style_profile.py` | `run_style_profile_refresh` | Cron + per-entity |
| `transcription.py` | `run_transcription` | On-demand |
| `rag_index.py` | `run_rag_index_update` | On-demand |

Supporting files:
- `enqueue.py` -- `JobContext` model, `get_job_pool()`, per-job `enqueue_*()` helpers.
- `worker.py` -- `WorkerSettings` with `functions`, `cron_jobs`, `on_startup`, `on_shutdown`.
- `retry.py` -- `with_retry_policy` decorator.
- `errors.py` -- `FailureCategory` enum, `classify_error()`, `compute_retry_delay()`.
- `observability.py` -- `JobTracer` class wrapping Langfuse spans.
- `gc_jobs.py` -- Garbage collection.

New job needed: `run_portfolio_construction` (on-demand, enqueued from router).

### Model Layer (`app/models/`)

| File | Purpose |
|------|---------|
| `schemas.py` | Agent output types (`PortfolioAnalysis`, `HazelCopilot`, `DailyDigest`, etc.) |
| `platform_models.py` | Platform API response models (all use `Decimal`, include `FreshnessMeta`) |
| `access_scope.py` | `AccessScope` with visibility checks and vector filter generation |
| `base.py` | Base model utilities |

New models needed: `ConstructPortfolioRequest`, `ConstructPortfolioResponse`, `ParsedIntent`, `IntentConstraints`, `FactorPreferences`, `ThemeScore`, `CompositeScore`, `ProposedHolding`, `CriticFeedback`, `PortfolioRationale`, `SecuritySnapshot`, `FundamentalsV2`, `PriceDataV2`, plus job event types.

---

## Structural Patterns

### 1. Analytics Model Definition

Every analytics model follows this pattern:

```python
# app/analytics/drift_detection.py
from app.analytics.registry import ModelCategory, ModelKind, ModelMetadata

class DriftDetector:
    metadata = ModelMetadata(
        name="drift_detection",
        version="1.0.0",
        owner="portfolio-analytics",
        category=ModelCategory.PORTFOLIO,
        kind=ModelKind.DETERMINISTIC,
        description="...",
        use_case="...",
        input_freshness_seconds=86_400,
        known_limitations=(
            "Assumes asset class mapping is pre-computed upstream.",
            "Does not model intra-day price movements.",
        ),
    )

    def __init__(self, default_threshold_pct: float = 5.0) -> None:
        self._default_thresh = default_threshold_pct

    def score(self, inputs: dict[str, Any]) -> dict[str, Any]:
        # inputs is an untyped dict with documented keys
        current = inputs["current_allocation"]
        # ... computation ...
        return {
            "as_of": as_of,
            "overall_severity": overall_severity,
            "drift_score": round(drift_score, 2),
            # ... structured result dict
        }
```

Key conventions:
- `metadata` is a class-level attribute (not instance).
- `score()` takes and returns `dict[str, Any]`.
- Constructor takes configuration knobs with defaults.
- Known limitations are a `tuple[str, ...]`.
- Score values are 0-100 scale, rounded to 2 decimal places.
- Results include `as_of`, `severity`, and domain-specific fields.

Registration happens in `app/analytics/startup.py`:

```python
from app.analytics.registry import get_registry

def register_all_models() -> None:
    registry = get_registry()
    registry.register(DriftDetector())
    registry.register(ConcentrationRiskScorer())
    # ...
```

### 2. Pydantic AI Agent Definition

Agents follow this pattern (from `portfolio_analyst.py`):

```python
from pydantic_ai import Agent, RunContext
from app.agents.base_deps import AgentDeps
from app.agents.registry import registry
from app.models.schemas import PortfolioAnalysis

# Agent constructor with typed deps and output
portfolio_analyst_agent: Agent[AgentDeps, PortfolioAnalysis] = Agent(
    model="anthropic:claude-sonnet-4-6",
    output_type=PortfolioAnalysis,
    tools=[
        get_household_summary,
        get_account_summary,
        get_order_projection,
        get_report_snapshot,
    ],
    retries=2,
    defer_model_check=True,
)

# System prompt via decorator
@portfolio_analyst_agent.system_prompt
async def build_portfolio_analyst_prompt(ctx: RunContext[AgentDeps]) -> str:
    return "\n".join([
        "You are a portfolio analysis assistant for wealth advisors.",
        "",
        "## Context",
        f"- Tenant: {ctx.deps.tenant_id}",
        f"- Advisor: {ctx.deps.actor_id}",
        "",
        "## Instructions",
        "- Analyze portfolio allocation...",
    ])

# Registration at module level
registry.register(
    "portfolio_analyst",
    portfolio_analyst_agent,
    tier="copilot",
    description="...",
)
```

For simpler agents without tools (used in jobs), the pattern is more compact:

```python
# From app/jobs/meeting_summary.py
summary_agent: Agent[None, MeetingSummary] = Agent(
    model="anthropic:claude-sonnet-4-6",
    output_type=MeetingSummary,
    defer_model_check=True,
    system_prompt="You are a meeting summarizer...",
    retries=2,
)
```

Extended deps pattern (from `copilot.py`):

```python
@dataclass
class CopilotDeps(AgentDeps):
    active_client_id: str | None = None
    active_household_id: str | None = None
```

Inline tool definition via decorator:

```python
@copilot_agent.tool
async def extract_document(ctx: RunContext[CopilotDeps], document_id: str) -> dict:
    """Docstring visible to the LLM as tool description."""
    content = await ctx.deps.platform.get_document_content(
        document_id, ctx.deps.access_scope
    )
    return {...}
```

### 3. Agent Registry

```python
# app/agents/registry.py
@dataclass
class AgentEntry:
    name: str
    agent: Agent[Any, Any]
    tier: str           # "copilot", "batch", "extraction"
    description: str

class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, AgentEntry] = {}

    def register(self, name: str, agent: Agent, *, tier: str, description: str = "") -> None:
        ...

    def get(self, name: str) -> AgentEntry:
        ...

    def list_agents(self) -> list[AgentEntry]:
        ...

# Module-level singleton
registry = AgentRegistry()
```

Agents register at import time (module-level call to `registry.register()`).

### 4. ARQ Job Definition

Job function signature:

```python
@with_retry_policy
async def run_meeting_summary(
    ctx: dict[str, Any],           # ARQ worker context (platform_client, redis, langfuse, etc.)
    job_ctx_raw: dict | None = None, # Serialized JobContext
    meeting_id: str | None = None,
    transcript_key: str | None = None,
) -> dict:
```

Job lifecycle:
1. Parse `JobContext` from `job_ctx_raw`
2. Extract shared deps from `ctx` dict: `ctx["platform_client"]`, `ctx["redis"]`, `ctx.get("langfuse")`
3. Create `JobTracer` for observability
4. Load data, run agent(s), persist result to Redis
5. Return summary dict
6. On failure: `tracer.fail(exc, category=...)`, then re-raise

Job registration in `worker.py`:

```python
class WorkerSettings:
    functions = [
        func(with_retry_policy(run_meeting_summary), name="run_meeting_summary"),
        # ...
    ]
```

Enqueue helper in `enqueue.py`:

```python
class JobContext(BaseModel):
    tenant_id: str
    actor_id: str
    actor_type: str
    request_id: str
    access_scope: dict

async def enqueue_meeting_summary(job_ctx: JobContext, meeting_id: str, transcript_key: str) -> str:
    pool = await get_job_pool()
    job = await pool.enqueue_job("run_meeting_summary", job_ctx.model_dump(), meeting_id, transcript_key)
    return job.job_id
```

### 5. Router Endpoint Definition

```python
# app/routers/portfolio.py
from fastapi import APIRouter, Depends
from app.context import RequestContext
from app.dependencies import get_langfuse, get_platform_client, get_redis, get_request_context

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

class PortfolioAnalysisRequest(BaseModel):
    client_id: str = Field(description="...")
    analysis_types: list[str] = Field(default_factory=list)

@router.post("/analyze")
async def analyze_portfolio(
    body: PortfolioAnalysisRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
    platform: Annotated[PlatformClient, Depends(get_platform_client)],
    langfuse: Annotated[Langfuse, Depends(get_langfuse)],
) -> PortfolioAnalysis:
    ...
```

For async job endpoints, the pattern returns 202 with a job_id (not yet in the codebase but planned for this feature):

```python
# Expected pattern (derived from enqueue.py + router patterns):
@router.post("/construct", status_code=202)
async def construct_portfolio(
    body: ConstructPortfolioRequest,
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> dict:
    job_ctx = JobContext(
        tenant_id=ctx.tenant_id,
        actor_id=ctx.actor_id,
        actor_type=ctx.actor_type,
        request_id=ctx.request_id,
        access_scope=ctx.access_scope.model_dump(),
    )
    job_id = await enqueue_portfolio_construction(job_ctx, body)
    return {"job_id": job_id}
```

### 6. Platform Client Method Typing

Every method follows this pattern:

```python
async def get_household_summary(
    self,
    household_id: str,
    access_scope: AccessScope,
) -> HouseholdSummary:
    key = self._cache_key("household_summary", hid=household_id, scope=access_scope.fingerprint())
    resp = await self._get(
        f"/v1/households/{household_id}/summary",
        access_scope=access_scope,
        cache_key=key,
    )
    return HouseholdSummary.model_validate(resp.json())
```

Key conventions:
- Every method takes `access_scope: AccessScope` as last positional arg.
- Cache key uses `self._cache_key()` with method name + identifying params + scope fingerprint.
- Response parsed via `ModelType.model_validate(resp.json())`.
- List endpoints return `[ModelType.model_validate(item) for item in resp.json()]`.
- `_get()` handles circuit breaker, timeout, error classification, and request-scoped caching.

### 7. Error Handling Patterns

**Router level** (from `app/utils/errors.py`):

```python
from app.utils.errors import ModelProviderHTTPError

try:
    result = await agent.run(prompt, deps=deps)
    return result.data
except Exception as exc:
    logger.exception("Portfolio analysis failed")
    raise ModelProviderHTTPError(str(exc), ctx.request_id) from exc
```

**Job level**: errors are classified and retried via `with_retry_policy`:

```python
class FailureCategory(str, Enum):
    PLATFORM_READ = "platform_read"     # retries: 3, backoff: 5s * 2^n
    MODEL_PROVIDER = "model_provider"   # retries: 3, backoff: 10s * 3^n
    VALIDATION = "validation"           # no retry
    INTERNAL = "internal"               # retries: 1, backoff: 30s
```

**Global error handling** in `main.py`:

```python
@app.exception_handler(SidecarError)
async def sidecar_error_handler(request, exc):
    return JSONResponse(status_code=exc.status_code, content={
        "ok": False,
        "error": {"code": exc.error_code, "category": exc.category, "message": exc.message, ...},
    })
```

**SidecarError hierarchy** (from `app/errors/__init__.py`):
- `SidecarError` (base)
  - `PlatformReadError` (platform_read)
  - `PlatformTimeoutError` (platform_read, 504)
  - `ModelProviderError` (model_provider, 502)
  - `ModelProviderRateLimitError` (model_provider, 429)
  - `ValidationError` (validation, 422)
  - `ScopeViolationError` (validation, 403)
  - `TranscriptionError` (transcription, 502)
  - `InternalError` (internal, 500)
  - `RedisUnavailableError` (internal, 503)

### 8. AgentDeps and Dependency Injection

Two `AgentDeps` exist:
- `app/agents/base_deps.py` (simple): `platform`, `access_scope`, `tenant_id`, `actor_id` -- used by tool functions and simpler agents.
- `app/agents/deps.py` (rich): `context: RequestContext`, `platform`, `redis`, `retriever` -- used by agents needing full request context.

Most agents use `base_deps.AgentDeps`. The copilot extends it with `CopilotDeps`.

FastAPI dependency injection wiring in `app/dependencies.py`:

```python
def get_agent_deps(request: Request):
    ctx = get_request_context(request)
    platform = get_platform_client(request)
    return AgentDeps(
        platform=platform,
        access_scope=ctx.access_scope or AccessScope(visibility_mode="full_tenant"),
        tenant_id=ctx.tenant_id,
        actor_id=ctx.actor_id,
    )
```

### 9. Observability (JobTracer)

```python
tracer = JobTracer(
    langfuse=langfuse,
    job_name="meeting_summary",
    tenant_id=job_ctx.tenant_id,
    actor_id=job_ctx.actor_id,
    extra_metadata={"meeting_id": meeting_id},
)

gen = tracer.start_generation(name="...", model="...", input_data="...")
# ... run agent ...
tracer.end_generation(gen, output=result.model_dump())
tracer.record_platform_read()
tracer.record_cache_hit()  / tracer.record_cache_miss()
tracer.complete(output={...})  # on success
tracer.fail(exc, category="...")  # on failure
```

### 10. Configuration

```python
# app/config.py
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SIDECAR_", env_file=".env", frozen=True)

    copilot_model: str = "anthropic:claude-sonnet-4-6"      # intent_parser, rationale, critic
    batch_model: str = "anthropic:claude-haiku-4-5"          # theme_scorer
    analysis_model: str = "anthropic:claude-opus-4-6"
    redis_url: str = "redis://localhost:6379/0"
    arq_queue_name: str = "sidecar:queue"
    arq_job_timeout_s: int = 600
    # ...

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

New config keys needed: `SIDECAR_PORTFOLIO_FRESHNESS_WARN_S` (default 86400), possibly `SIDECAR_PORTFOLIO_THEME_CACHE_TTL_S` (default 21600).

### 11. Test Patterns

**Test file naming**: `tests/test_{module_name}.py` (flat directory, no nested `tests/` subpackages except `tests/mocks/`).

**Conftest** (`conftest.py` at repo root):

```python
import os
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-dummy")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy")
os.environ.setdefault("TOGETHER_API_KEY", "test-dummy")
```

**Analytics model tests** (unit, no mocks needed):

```python
# tests/test_concentration_risk.py
from app.analytics.concentration_risk import ConcentrationRiskScorer

def test_concentrated_position_flagged() -> None:
    scorer = ConcentrationRiskScorer()
    result = scorer.score({
        "holdings": [...],
        "total_portfolio_value": 100000,
        "as_of": "2026-03-28",
    })
    assert len(result["flags"]) > 0
```

**Registry tests**:

```python
# tests/test_analytics_registry.py
class _FakeModel:
    def __init__(self, name="test_model", version="1.0.0"):
        self.metadata = _make_meta(name, version)
    def score(self, inputs):
        return {"result": "ok"}

def test_register_and_get() -> None:
    reg = ModelRegistry()
    model = _FakeModel()
    reg.register(model)
    assert reg.get("test_model") is model
```

**Router tests** (FastAPI TestClient with mocked state):

```python
# tests/test_router_endpoints.py
@pytest.fixture
def client() -> TestClient:
    app = create_app()
    app.state.redis = AsyncMock()
    app.state.vector_store = AsyncMock()
    app.state.platform_client = AsyncMock()
    app.state.langfuse = MagicMock()
    app.state.settings = MagicMock()
    return TestClient(app, raise_server_exceptions=False)

REQUIRED_HEADERS = {
    "X-Tenant-ID": "t_001", "X-Actor-ID": "a_001",
    "X-Actor-Type": "advisor", "X-Request-ID": "r_001",
}

def test_endpoint_exists(client):
    resp = client.post("/ai/portfolio/analyze", json={...}, headers=REQUIRED_HEADERS)
    assert resp.status_code not in (404, 405)
```

**Mock platform client** (`tests/mocks/mock_platform_client.py`):
- `MockPlatformClient` with `set_*()` methods for configuring canned responses.
- `set_error()` for injecting `PlatformReadError`.
- All async methods with default canned data.
- Factory helpers like `_freshness()` and `_account()`.

**Enqueue tests** (model serialization only, no Redis):

```python
def test_job_context_serialization() -> None:
    ctx = JobContext(tenant_id="t_001", actor_id="a_001", ...)
    data = ctx.model_dump()
    restored = JobContext(**data)
    assert restored.tenant_id == "t_001"
```

### 12. Platform Models Convention [grep-fallback]

All monetary values use `Decimal` (never `float`). All data-bearing models include `FreshnessMeta`:

```python
class FreshnessMeta(BaseModel):
    as_of: datetime
    source: str
    staleness_seconds: int | None = None
```

### 13. AccessScope Flow

```
HTTP Request
  -> TenantContextMiddleware (extracts headers, builds RequestContext with AccessScope)
    -> Router (injects RequestContext via Depends(get_request_context))
      -> AgentDeps (carries access_scope)
        -> PlatformClient methods (pass access_scope for scope headers)
          -> Platform API (enforces access)
```

For jobs, scope flows through `JobContext.access_scope` (serialized as dict), then reconstructed:

```python
job_ctx = JobContext(**job_ctx_raw)
access_scope = AccessScope(**job_ctx.access_scope)
```

---

## Key Files

### Reference Reading (understand patterns)

| File | Why |
|------|-----|
| `app/analytics/registry.py` | ModelRegistry, ModelMetadata, AnalyticalModel protocol |
| `app/analytics/startup.py` | Model registration pattern |
| `app/analytics/concentration_risk.py` | Most complex existing analytics model |
| `app/analytics/drift_detection.py` | Simpler analytics model reference |
| `app/analytics/tax_loss_harvesting.py` | Multi-lot scoring with dataclasses |
| `app/agents/portfolio_analyst.py` | Agent definition with tools and registry |
| `app/agents/copilot.py` | Extended deps, inline tools, system prompt |
| `app/agents/registry.py` | AgentRegistry singleton |
| `app/agents/base_deps.py` | AgentDeps shape |
| `app/agents/runner.py` | Agent retry/fallback runner |
| `app/routers/portfolio.py` | Existing portfolio endpoint to extend |
| `app/services/platform_client.py` | Typed client methods, cache pattern |
| `app/models/schemas.py` | PortfolioAnalysis, all output types |
| `app/models/platform_models.py` | FreshnessMeta, Decimal convention, Holding, AccountSummary |
| `app/models/access_scope.py` | AccessScope with visibility checks |
| `app/jobs/meeting_summary.py` | Full job lifecycle pattern |
| `app/jobs/daily_digest.py` | Cron sweep + per-entity + caching |
| `app/jobs/enqueue.py` | JobContext, enqueue helpers |
| `app/jobs/worker.py` | WorkerSettings function/cron registration |
| `app/jobs/retry.py` | with_retry_policy decorator |
| `app/jobs/errors.py` | FailureCategory, retry policy |
| `app/jobs/observability.py` | JobTracer for Langfuse |
| `app/config.py` | Settings, model tiers |
| `app/context.py` | RequestContext |
| `app/dependencies.py` | DI wiring, build_worker_dependencies |
| `app/main.py` | App factory, lifespan, router mounting, error handlers |
| `app/errors/__init__.py` | SidecarError hierarchy |
| `app/utils/errors.py` | HTTP error wrappers for routers |
| `app/tools/platform.py` | Tool function pattern for agents |
| `tests/test_analytics_registry.py` | Registry test pattern |
| `tests/test_concentration_risk.py` | Analytics model test pattern |
| `tests/test_enqueue.py` | Job context test pattern |
| `tests/test_router_endpoints.py` | Router test pattern with TestClient |
| `tests/mocks/mock_platform_client.py` | Mock platform client pattern |
| `conftest.py` | Test env setup |

### Expected Edits (modify existing files)

| File | Change |
|------|--------|
| `app/analytics/startup.py` | Add `from app.analytics.portfolio_factor_model_v2 import PortfolioFactorModelV2` and `registry.register(PortfolioFactorModelV2())` |
| `app/routers/portfolio.py` | Add `POST /portfolio/construct`, `GET /portfolio/jobs/{job_id}`, `GET /portfolio/jobs/{job_id}/events` endpoints |
| `app/services/platform_client.py` | Add `get_security_universe()`, `bulk_fundamentals()`, `bulk_price_data()` methods |
| `app/models/platform_models.py` | Add `SecuritySnapshot`, `FundamentalsV2`, `PriceDataV2` typed response models |
| `app/jobs/enqueue.py` | Add `enqueue_portfolio_construction()` helper |
| `app/jobs/worker.py` | Register `run_portfolio_construction` in `WorkerSettings.functions` |
| `app/config.py` | Add `portfolio_freshness_warn_s`, `portfolio_theme_cache_ttl_s` settings |
| `app/main.py` | No change needed (portfolio router already mounted) |
| `tests/mocks/mock_platform_client.py` | Add mock methods for `get_security_universe()`, `bulk_fundamentals()`, `bulk_price_data()` |

### Expected New Files

| File | Purpose |
|------|---------|
| **Module root** | |
| `app/portfolio_construction/__init__.py` | Package init |
| `app/portfolio_construction/config.py` | Theme-factor priors, factor definitions, default parameters |
| `app/portfolio_construction/orchestrator.py` | Main pipeline: intent -> data -> recall -> score -> optimize -> review loop |
| `app/portfolio_construction/models.py` | `ParsedIntent`, `IntentConstraints`, `FactorPreferences`, `ThemeScore`, `CompositeScore`, `ProposedHolding`, `CriticFeedback`, `PortfolioRationale`, `ConstructPortfolioRequest`, `ConstructPortfolioResponse`, job event types |
| **Analytics** | |
| `app/analytics/portfolio_factor_model_v2.py` | Factor model: 6 canonical factors, hierarchical normalization, correlation-adjusted sub-factor aggregation, reliability shrinkage, breadth-sensitive scoring, weighted geometric mean |
| **Agents** | |
| `app/portfolio_construction/agents/__init__.py` | Package init |
| `app/portfolio_construction/agents/intent_parser.py` | `portfolio_intent_parser` agent (copilot tier) -> `ParsedIntent` |
| `app/portfolio_construction/agents/theme_scorer.py` | `portfolio_theme_scorer` agent (batch tier) -> `ThemeScore` list |
| `app/portfolio_construction/agents/rationale.py` | `portfolio_rationale` agent (copilot tier) -> `PortfolioRationale` |
| `app/portfolio_construction/agents/critic.py` | `portfolio_critic` agent (copilot tier) -> `CriticFeedback` |
| **Scoring and Optimization** | |
| `app/portfolio_construction/recall_pool.py` | Two-stage recall pool: factor top-N + metadata matches + explicit includes |
| `app/portfolio_construction/composite_scorer.py` | Seven-step composite scoring pipeline |
| `app/portfolio_construction/optimizer.py` | Candidate selection, four weighting strategies, position limits, constraint relaxation |
| `app/portfolio_construction/account_aware.py` | Account-mode: holdings overlap, turnover, drift, tax-sensitive warnings |
| **Data** | |
| `app/portfolio_construction/data_loader.py` | Typed platform reads with freshness checks, fallback market data adapter |
| `app/portfolio_construction/market_data_fallback.py` | yfinance dev-time fallback with field-by-field merge and provenance |
| **Job** | |
| `app/portfolio_construction/job.py` | `run_portfolio_construction` ARQ job entry point with progress events |
| `app/portfolio_construction/events.py` | Redis Streams progress event emission and reading |
| **Tests** | |
| `tests/test_portfolio_factor_model_v2.py` | Factor math unit tests |
| `tests/test_portfolio_composite_scorer.py` | Composite scoring and gating tests |
| `tests/test_portfolio_optimizer.py` | Optimizer, constraint relaxation, weighting strategy tests |
| `tests/test_portfolio_recall_pool.py` | Recall pool construction tests |
| `tests/test_portfolio_construction_models.py` | Pydantic model validation tests |
| `tests/test_portfolio_construction_orchestrator.py` | Integration test with mocked agents and platform client |
| `tests/test_portfolio_construction_router.py` | Router endpoint wiring tests |
| `tests/test_portfolio_construction_enqueue.py` | Job enqueue and context tests |
| `tests/test_portfolio_construction_events.py` | Progress event emission and reading tests |
