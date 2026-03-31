# Spike Report: portfolio-construction-v2

## Dependencies Explored

### 1. numpy (>=2.0)

- **Version observed**: 2.4.3
- **Status**: Installed in venv, import succeeds
- **Used for**: Percentile ranking, correlation matrices, array operations in factor model
- **Happy path**: `import numpy; numpy.percentile([1,2,3,4,5], 50)` → `3.0`
- **Edge cases**: Empty arrays raise `ValueError` — factor model must guard against empty peer buckets
- **Mock template**: Not needed — used directly in unit tests with synthetic data

### 2. scipy (>=1.12)

- **Version observed**: NOT INSTALLED
- **Status**: Listed in `pyproject.toml` but not installed in `.venv`
- **Used for**: `scipy.optimize.minimize(method='SLSQP')` for min-variance optimizer
- **Action required**: Run `uv pip install scipy` or `uv sync` before implementation
- **Happy path**: (documentation-derived) `minimize(fun, x0, method='SLSQP', bounds=bounds, constraints=constraints)` returns `OptimizeResult` with `.x` (weights), `.success`, `.message`
- **Error behavior**: (documentation-derived) On non-convergence, `.success` is `False`. Spec requires fallback to risk_parity on solver failure.
- **Mock template**: Min-variance tests should use a small (5-stock) synthetic universe where the optimal solution is known analytically. Solver failure can be triggered by conflicting bounds.

### 3. scikit-learn (>=1.3)

- **Version observed**: NOT INSTALLED
- **Status**: Listed in `pyproject.toml` but not installed in `.venv`
- **Used for**: `sklearn.covariance.LedoitWolf` for covariance shrinkage
- **Action required**: Run `uv pip install scikit-learn` or `uv sync` before implementation
- **Happy path**: (documentation-derived) `LedoitWolf().fit(returns_matrix).covariance_` returns shrunk covariance matrix
- **Error behavior**: (documentation-derived) Raises `ValueError` if input has fewer than 2 observations. Factor model must validate minimum observation count.
- **Mock template**: Tests should use synthetic return series (20+ days, 5+ stocks) with known covariance structure.

### 4. redis.asyncio (>=5.0)

- **Version observed**: Installed, import succeeds
- **Used for**: Job state, theme score cache, Redis Streams (XADD/XREAD) for progress events
- **Happy path**: `import redis.asyncio` succeeds. Redis Streams available in Redis 5+.
- **Edge cases**: Redis Streams require `XADD` and `XREAD` commands. If Redis version < 5.0, these will fail with `ResponseError`.
- **Mock template**: Use `fakeredis.aioredis.FakeRedis` (already used in existing tests) for unit tests. Streams support in fakeredis may be limited — verify or use a real Redis in integration tests.

### 5. arq (>=0.25)

- **Version observed**: Installed, import succeeds
- **Used for**: Async job execution following existing `run_meeting_summary` pattern
- **Happy path**: Job defined as `async def run_portfolio_construction(ctx, ...)`, registered in `WorkerSettings.functions`
- **Mock template**: Follow existing test patterns — mock `ctx` dict with `redis`, `platform`, `settings` keys.

### 6. pydantic-ai (>=0.1)

- **Version observed**: Installed, import succeeds
- **Used for**: All 4 LLM agents (intent parser, theme scorer, rationale, critic)
- **Happy path**: `Agent(model="anthropic:claude-haiku-4-5", output_type=MyModel)` creates agent. `await agent.run(prompt)` returns typed result.
- **Mock template**: Use `Agent.override(model=TestModel(custom_result_type=...))` context manager for prompt-contract tests. Alternatively, mock at the `agent.run()` level to return canned `RunResult` objects.

### 7. yfinance (>=0.2)

- **Version observed**: NOT INSTALLED (expected — it's a new dev dependency)
- **Used for**: Dev-time fallback market data adapter
- **Action required**: Add to `[project.optional-dependencies]` under a `dev` group
- **Happy path**: (documentation-derived) `yfinance.Ticker("AAPL").info` returns dict with fundamentals. `yfinance.Ticker("AAPL").history(period="1y")` returns DataFrame with price data.
- **Error behavior**: (documentation-derived) Returns empty dict/DataFrame for invalid tickers. Network errors raise `ConnectionError`.
- **Mock template**: Mock `yfinance.Ticker` to return a canned dict and DataFrame. Never call real yfinance in unit tests.

## Scratch Files

No scratch scripts created — all dependencies are either already verified via import or are documentation-derived (scipy, sklearn, yfinance need installation before live testing).

### Pre-implementation actions

1. Ensure scipy and scikit-learn are installed: `cd apps/intelligence-layer && uv sync` (they're in pyproject.toml, may just need a fresh sync)
2. Add yfinance to dev dependencies in pyproject.toml
3. Verify Redis Streams support with fakeredis in existing test infrastructure
