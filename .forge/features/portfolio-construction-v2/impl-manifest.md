# Implementation Manifest: portfolio-construction-v2

## Files Created

| File | Purpose |
|------|---------|
| `src/app/portfolio_construction/__init__.py` | Package init |
| `src/app/portfolio_construction/models.py` | All domain models (ParsedIntent, FactorPreferences, IntentConstraints, ThemeScoreResult, FactorScoreResult, CompositeScoreResult, ProposedHolding, CriticFeedback, PortfolioRationale, JobEvent, ConstructPortfolioRequest, ConstructPortfolioResponse, PortfolioConstructionAccepted) |
| `src/app/portfolio_construction/config.py` | Factor definitions, theme-factor priors, default parameters |
| `src/app/portfolio_construction/data_loader.py` | DataLoader with Decimal→float conversion and freshness warnings |
| `src/app/portfolio_construction/market_data_fallback.py` | yfinance dev-time fallback adapter |
| `src/app/portfolio_construction/recall_pool.py` | Two-stage recall pool builder |
| `src/app/portfolio_construction/composite_scorer.py` | Seven-step gated composite scoring |
| `src/app/portfolio_construction/optimizer.py` | 4 weighting strategies, candidate selection, auto-relax, position clamping |
| `src/app/portfolio_construction/account_aware.py` | Account-mode overlap/turnover/drift computation |
| `src/app/portfolio_construction/events.py` | ProgressEventEmitter using Redis Streams |
| `src/app/portfolio_construction/orchestrator.py` | PortfolioConstructionPipeline class with review loop |
| `src/app/portfolio_construction/cache.py` | ThemeScoreCache with SHA256 keys and Redis backing |
| `src/app/portfolio_construction/prompts.py` | Theme scorer prompt template |
| `src/app/portfolio_construction/agents/__init__.py` | Agent registration imports |
| `src/app/portfolio_construction/agents/theme_scorer.py` | portfolio_theme_scorer agent + score_themes function |
| `src/app/portfolio_construction/agents/intent_parser.py` | portfolio_intent_parser agent |
| `src/app/portfolio_construction/agents/rationale.py` | portfolio_rationale agent |
| `src/app/portfolio_construction/agents/critic.py` | portfolio_critic agent |
| `src/app/analytics/portfolio_factor_model_v2.py` | Full factor model v2 with peer-bucket normalization, correlation-adjusted aggregation, reliability shrinkage, breadth caps, geometric mean |
| `src/app/jobs/portfolio_construction.py` | ARQ job entry point |

## Files Modified

| File | Change |
|------|--------|
| `src/app/config.py` | Added `portfolio_freshness_warn_s` and `portfolio_theme_cache_ttl_s` settings |
| `src/app/analytics/startup.py` | Registered PortfolioFactorModelV2 in register_all_models() |
| `src/app/routers/portfolio.py` | Added POST /construct, GET /jobs/{job_id}, GET /jobs/{job_id}/events endpoints |
| `src/app/jobs/enqueue.py` | Added enqueue_portfolio_construction() helper |
| `src/app/jobs/worker.py` | Imported and registered run_portfolio_construction |
| `pyproject.toml` | Added scikit-learn>=1.4.0 dependency |

## Patterns Followed

- Analytics model: follows ConcentrationRiskScorer pattern (ModelMetadata + score() method)
- Agents: follow portfolio_analyst pattern (Agent constructor, system_prompt, output_type)
- ARQ job: follows run_meeting_summary pattern (parse ctx, extract deps, delegate to pipeline)
- Router: follows existing portfolio.py pattern (FastAPI dependency injection, response models)
- PlatformClient: follows existing typed method pattern (_cache_key + _get + model_validate)
- Tests: follow existing test patterns (pytest fixtures, mock classes, TestClient)

## Test Results

All 210 new portfolio construction tests passing. 182 existing tests unaffected.

```
tests/test_portfolio_construction_models.py       - 24 passed
tests/test_portfolio_data_loader.py               - 12 passed
tests/test_portfolio_factor_model_v2.py           - 42 passed
tests/test_portfolio_recall_pool.py               - 12 passed
tests/test_portfolio_theme_scorer.py              - 18 passed
tests/test_portfolio_composite_scorer.py          - 26 passed
tests/test_portfolio_optimizer.py                 - 34 passed
tests/test_portfolio_agent_contracts.py           - 14 passed
tests/test_portfolio_construction_events.py       - 10 passed
tests/test_portfolio_construction_orchestrator.py - 12 passed
tests/test_portfolio_construction_router.py       -  6 passed
```
